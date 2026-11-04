"""Ripianificazione di piani misti attivi senza mutare gli snapshot storici.

GET/anteprima: controlli in sola lettura escludendo le prenotazioni del piano
originale. POST: BEGIN IMMEDIATE; storno temporaneo, simulazione completa,
nuova bozza con lotto, nuova attivazione, storico, commit unico.
Una eccezione ripristina il piano iniziale e tutte le sue prenotazioni.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaImpastatrici, BozzaPianificazioneMista, PianificazioneMistaAttiva,
    PrenotazioneMistaForno, PrenotazioneMistaImpastatrice, TipologieLavorazione,
)
from flaskr.models.model_lotto import LottoPianificazione
from flaskr.models.model_ripianificazione_mista import StoricoRipianificazioneMista
from flaskr.service.anteprima_impasto_misto_service import _fasi_e_ricetta, simula
from flaskr.service.attivazione_impasto_misto_service import (
    _log, _transazione_sqlite_immediata, conferma_bozza,
)
from flaskr.service.bozze_impasto_misto_service import salva_bozza
from flaskr.service.lotto_pianificazione_service import (
    ErroreLotto, lotto_corrente, prima_data_impasto_fasi, proponi_lotto,
)
from flaskr.service.pianificazione_service import ErroreCalcoloPianificazione
from flaskr.service.scadenze_giornaliere_service import articolo_cnf
from flaskr.service.risorse_produzione_service import (
    _blocchi_occupati, _capacita_kg, disponibilita_forno, impastatrici_libere,
)


class ErroreRipianificazioneMista(ValueError):
    pass


def _sposta_blocco(data_iniziale: date, blocco_iniziale: str, offset: int) -> tuple[date, str]:
    """Conserva l'offset di blocchi M/P della lavorazione originale."""
    if blocco_iniziale not in ('M', 'P') or offset < 0:
        raise ErroreRipianificazioneMista('Blocco iniziale o offset non valido.')
    indice = (1 if blocco_iniziale == 'P' else 0) + offset
    return data_iniziale + timedelta(days=indice // 2), 'M' if indice % 2 == 0 else 'P'


def _offset_assoluto(data_iniziale: date, blocco_iniziale: str,
                      data_fase: date, blocco_fase: str) -> int:
    if blocco_fase not in ('M', 'P'):
        raise ErroreRipianificazioneMista('Blocco forno originale non valido.')
    offset = ((data_fase - data_iniziale).days * 2
              + (1 if blocco_fase == 'P' else 0)
              - (1 if blocco_iniziale == 'P' else 0))
    if offset < 0:
        raise ErroreRipianificazioneMista('Date delle fasi originali non coerenti.')
    return offset


def _carica_origine(id_bozza: int, nuova_data: date):
    if not isinstance(nuova_data, date) or isinstance(nuova_data, datetime):
        raise ErroreRipianificazioneMista('Inserisci una data valida.')
    piano = db.session.get(PianificazioneMistaAttiva, id_bozza)
    if piano is None or piano.Stato != 'In produzione':
        raise ErroreRipianificazioneMista('La ripianificazione richiede un piano misto attivo.')
    bozza = piano.bozza
    if bozza is None or bozza.DataProduzione == nuova_data:
        raise ErroreRipianificazioneMista('Seleziona una data diversa da quella originale.')
    if not bozza.ordini or not bozza.forni or not bozza.fasi_impasto or not bozza.impastatrici:
        raise ErroreRipianificazioneMista('Piano originario privo di ordini, fasi o risorse.')
    if bozza.BloccoIniziale not in ('M', 'P'):
        raise ErroreRipianificazioneMista('Blocco originale non valido.')
    if len({(o.IdDocumento, o.IdRigaDoc) for o in bozza.ordini}) != len(bozza.ordini):
        raise ErroreRipianificazioneMista('Ordini duplicati nel piano originale.')
    lotto = lotto_corrente('MISTA', id_bozza)
    if lotto is None:
        raise ErroreRipianificazioneMista(
            'Il piano originale è privo di lotto corrente: completare prima la migrazione dei lotti.'
        )
    return piano, bozza, lotto


def _verifica_prenotazioni_originali(bozza):
    """Impedisce la ripianificazione di dati storici già incoerenti."""
    forni_attesi = set()
    for forno in bozza.forni:
        codici = {o.CodArt for o in bozza.ordini if o.IdForno == forno.IdForno}
        if not codici:
            raise ErroreRipianificazioneMista('Forno senza ordini associati.')
        durate = {int(_fasi_e_ricetta(c)[2].lavorazione.DurataBlocchi) for c in codici}
        if len(durate) != 1:
            raise ErroreRipianificazioneMista('Durate di cottura originali non coerenti.')
        for giorno, blocco in _blocchi_occupati(forno.DataFase, forno.Blocco, next(iter(durate))):
            forni_attesi.add((forno.IdForno, giorno, blocco, int(forno.NumeroCotte)))
    forni_reali = {
        (p.IdForno, p.DataFase, p.Blocco, int(p.NumeroCotte)) for p in db.session.scalars(
            db.select(PrenotazioneMistaForno).where(PrenotazioneMistaForno.IdBozza == bozza.IdBozza)
        )
    }
    if forni_attesi != forni_reali:
        raise ErroreRipianificazioneMista('Prenotazioni storiche dei forni incomplete o incoerenti.')

    miscelatrici_attese = set()
    for m in bozza.impastatrici:
        if Decimal(str(m.KgAssegnati)) <= 0:
            continue
        for fase in bozza.fasi_impasto:
            for giorno, blocco in _blocchi_occupati(fase.DataFase, fase.Blocco, int(fase.DurataBlocchi)):
                miscelatrici_attese.add((m.IdImpastatrice, giorno, blocco, Decimal(str(m.KgAssegnati))))
    miscelatrici_reali = {
        (p.IdImpastatrice, p.DataFase, p.Blocco, Decimal(str(p.KgAssegnati)))
        for p in db.session.scalars(db.select(PrenotazioneMistaImpastatrice).where(
            PrenotazioneMistaImpastatrice.IdBozza == bozza.IdBozza))
    }
    if miscelatrici_attese != miscelatrici_reali:
        raise ErroreRipianificazioneMista('Prenotazioni storiche delle impastatrici incomplete o incoerenti.')


def prepara_ripianificazione_mista(id_bozza: int, nuova_data: date,
                                    *, strict: bool = True, include_cnf: bool = False) -> dict:
    """Controlli in sola lettura. Il POST li ripete sempre dentro il lock SQLite."""
    piano, bozza, lotto_vecchio = _carica_origine(id_bozza, nuova_data)
    if not include_cnf and any(articolo_cnf(o.CodArt) for o in bozza.ordini):
        raise ErroreRipianificazioneMista('Pianificazione non disponibile per questo profilo.')
    _verifica_prenotazioni_originali(bozza)
    fasi = []
    for fase in bozza.fasi_impasto:
        data, blocco = _sposta_blocco(nuova_data, bozza.BloccoIniziale, int(fase.OffsetBlocchi))
        fasi.append(SimpleNamespace(
            IdRicettaFase=fase.IdRicettaFase, IdLavorazione=fase.IdLavorazione,
            OffsetBlocchi=fase.OffsetBlocchi, DataFase=data, Blocco=blocco,
            DurataBlocchi=int(fase.DurataBlocchi),
        ))
    # Le ricette attuali non possono cambiare la natura delle fasi di impasto.
    firme_attuali = {tuple(sorted(_fasi_e_ricetta(o.CodArt)[1], key=lambda f: (f[1], f[0])))
                    for o in bozza.ordini}
    firma_originale = tuple((f.IdLavorazione, int(f.OffsetBlocchi), int(f.DurataBlocchi))
                            for f in sorted(bozza.fasi_impasto,
                                            key=lambda f: (f.OffsetBlocchi, f.IdLavorazione)))
    if len(firme_attuali) != 1 or next(iter(firme_attuali)) != firma_originale:
        raise ErroreRipianificazioneMista('Ricette di impasto modificate dopo la pianificazione originale.')

    ids_lavorazioni = {f.IdLavorazione for f in fasi}
    lavorazioni = {l.IdLavorazione: l for l in db.session.scalars(
        db.select(TipologieLavorazione).where(TipologieLavorazione.IdLavorazione.in_(ids_lavorazioni))
    )}
    if set(lavorazioni) != ids_lavorazioni:
        raise ErroreRipianificazioneMista('Lavorazioni delle fasi originali non più configurate.')
    ids_macchine = {m.IdImpastatrice for m in bozza.impastatrici}
    macchine = {m.IdImpastatrice: m for m in db.session.scalars(db.select(AnagraficaImpastatrici).where(
        AnagraficaImpastatrici.IdImpastatrice.in_(ids_macchine)
    ))}
    if ids_macchine != set(macchine):
        raise ErroreRipianificazioneMista('Una delle impastatrici originali non esiste più.')
    libere, _ = impastatrici_libere(
        list(macchine.values()), [(f, lavorazioni[f.IdLavorazione]) for f in fasi],
        escludi_id_bozza_mista=id_bozza,
    )
    conflitti = []
    macchine_stato = []
    for item in sorted(bozza.impastatrici, key=lambda m: m.IdImpastatrice):
        id_m = item.IdImpastatrice
        kg = Decimal(str(item.KgAssegnati))
        capacitakg = _capacita_kg(macchine[id_m])
        ok = id_m in libere and kg <= capacitakg
        macchine_stato.append(dict(id=id_m, kg=kg, capacita=capacitakg, libera=ok))
        if not ok:
            conflitti.append(f'Impastatrice {id_m} occupata o con capacità insufficiente nella nuova data.')
    peso_vecchio_kg = Decimal(str(bozza.PesoTotaleGrammi)) / 1000
    if abs(sum((v['kg'] for v in macchine_stato), Decimal(0)) - peso_vecchio_kg) > Decimal('0.000001'):
        raise ErroreRipianificazioneMista('Peso e assegnazioni delle impastatrici originali non coincidono.')

    forni_stato = []
    for forno in sorted(bozza.forni, key=lambda f: f.IdForno):
        offset = _offset_assoluto(bozza.DataProduzione, bozza.BloccoIniziale,
                                   forno.DataFase, forno.Blocco)
        data, blocco = _sposta_blocco(nuova_data, bozza.BloccoIniziale, offset)
        codici = {o.CodArt for o in bozza.ordini if o.IdForno == forno.IdForno}
        libere_forno = []
        for cod in codici:
            _, _, ricetta_forno = _fasi_e_ricetta(cod)
            # La data e il blocco forno devono essere ancora quelli della ricetta.
            # Se la ricetta e' cambiata, non mutare lo storico implicitamente.
            offset_attuale = int(ricetta_forno.OffsetBlocchi)
            if offset_attuale != offset:
                raise ErroreRipianificazioneMista(f'{cod}: posizione della fase forno modificata.')
            provvisorio = SimpleNamespace(DataFase=data, Blocco=blocco)
            n, _ = disponibilita_forno(provvisorio, ricetta_forno.lavorazione,
                                       ricetta_forno, forno.IdForno,
                                       escludi_id_bozza_mista=id_bozza)
            libere_forno.append(int(n))
        disponibili = min(libere_forno)
        forni_stato.append(dict(id=forno.IdForno, data=data, blocco=blocco,
                                cotte=int(forno.NumeroCotte), libere=disponibili))
        if int(forno.NumeroCotte) > disponibili:
            conflitti.append(
                f'Forno {forno.IdForno}: richieste {forno.NumeroCotte} cotte, disponibili {disponibili}.'
            )

    if strict and conflitti:
        raise ErroreRipianificazioneMista(' '.join(conflitti))
    # Il codice lotto si usa solo come proposta; non prenotarlo durante la GET.
    data_lotto = prima_data_impasto_fasi(fasi)
    return dict(piano=piano, bozza=bozza, nuova_data=nuova_data,
                macchine=macchine_stato, forni=forni_stato, fasi=fasi,
                lotto_vecchio=lotto_vecchio.Lotto,
                lotto_proposto=proponi_lotto(data_lotto), data_lotto=data_lotto,
                conflitti=conflitti)


def storico_piano_misto(id_bozza: int) -> dict:
    precedente = db.session.scalar(db.select(StoricoRipianificazioneMista).where(
        StoricoRipianificazioneMista.IdBozzaNuova == id_bozza))
    successiva = db.session.scalar(db.select(StoricoRipianificazioneMista).where(
        StoricoRipianificazioneMista.IdBozzaPrecedente == id_bozza))
    return {'precedente': precedente, 'successiva': successiva}


def salva_ripianificazione_mista(id_bozza: int, nuova_data: date, operatore: str,
                                motivo: str, *, include_cnf: bool) -> int:
    motivo = (motivo or '').strip()
    if not (5 <= len(motivo) <= 500):
        raise ErroreRipianificazioneMista('La motivazione deve contenere da 5 a 500 caratteri.')
    if not operatore or len(operatore) > 120:
        raise ErroreRipianificazioneMista('Operatore non valido.')
    try:
        _transazione_sqlite_immediata()
        dati = prepara_ripianificazione_mista(id_bozza, nuova_data, include_cnf=include_cnf)
        piano, bozza = dati['piano'], dati['bozza']
        ordini = list(bozza.ordini)
        forni_vecchi = list(bozza.forni)
        macchine_vecchie = list(bozza.impastatrici)
        nomi_kg = {m.IdImpastatrice: Decimal(str(m.KgAssegnati)) for m in macchine_vecchie}
        chiavi = [f'{o.IdDocumento}-{o.IdRigaDoc}' for o in ordini]
        forni_per_articolo = {o.CodArt: o.IdForno for o in ordini}
        if len({(o.CodArt, o.IdForno) for o in ordini}) != len(set(forni_per_articolo)):
            raise ErroreRipianificazioneMista('Articolo assegnato a più forni nello storico.')
        quantita = {f'{o.IdDocumento}-{o.IdRigaDoc}': int(o.QtaPianificata) for o in ordini}
        cotte = {f.IdForno: int(f.NumeroCotte) for f in forni_vecchi}
        ids_macchine = [m.IdImpastatrice for m in macchine_vecchie]
        ora = datetime.now()
        # La cancellazione è SOLO provvisoria fino al commit finale: simula può
        # così controllare ordini e slot senza contare due volte il piano origine.
        piano.Stato = 'Annullato'
        piano.OperatoreAnnullamento = operatore
        piano.AnnullataIl = ora
        db.session.execute(delete(PrenotazioneMistaForno).where(
            PrenotazioneMistaForno.IdBozza == id_bozza))
        db.session.execute(delete(PrenotazioneMistaImpastatrice).where(
            PrenotazioneMistaImpastatrice.IdBozza == id_bozza))
        db.session.flush()

        esito = simula(gruppo_id=bozza.IdGruppo, chiavi=chiavi,
                      forno_articolo=forni_per_articolo, cotte_per_forno=cotte,
                      data_inizio=nuova_data, blocco=bozza.BloccoIniziale,
                      impastatrici_ids=ids_macchine, include_cnf=include_cnf,
                      quantita_per_ordine=quantita)
        if ({f'{r["ordine"].IdDocumento}-{r["ordine"].IdRigaDoc}': int(r['pianificata'])
             for r in esito['ordini']} != quantita
             or int(esito['totale_cotte']) != int(bozza.NumeroCotte)
             or abs(Decimal(str(esito['peso_kg'])) * 1000
                    - Decimal(str(bozza.PesoTotaleGrammi))) > Decimal('0.005')):
            raise ErroreRipianificazioneMista('La nuova simulazione differisce dalle quantità originali.')
        if {fid: (d['data'], d['blocco'], int(d['esito'].numero_cotte))
            for fid, d in esito['risultati'].items()} != {
                f['id']: (f['data'], f['blocco'], f['cotte']) for f in dati['forni']}:
            raise ErroreRipianificazioneMista('La posizione delle cotture non coincide con lo storico.')

        nuovo_id = salva_bozza(
            esito=esito, gruppo_id=bozza.IdGruppo, data_inizio=nuova_data,
            blocco=bozza.BloccoIniziale, forno_articolo=forni_per_articolo,
            impastatrici_ids=ids_macchine, richiesta_id=uuid4().hex,
            operatore=operatore, progressivo_lotto=None, commit=False,
        )
        nuovo = db.session.get(BozzaPianificazioneMista, nuovo_id)
        # Preserva l'assegnazione di kg alle STESSE impastatrici, non soltanto
        # l'elenco di candidate: salva_bozza puo' ottimizzare in modo diverso.
        for m in nuovo.impastatrici:
            m.KgAssegnati = nomi_kg[m.IdImpastatrice]
        db.session.flush()
        conferma_bozza(nuovo_id, operatore, include_cnf=include_cnf,
                       transazione_gestita=True)
        lotto_nuovo = lotto_corrente('MISTA', nuovo_id)
        lotto_vecchio = lotto_corrente('MISTA', id_bozza)
        if lotto_nuovo is None or lotto_vecchio is None:
            raise ErroreRipianificazioneMista('Lotto nuovo o precedente non registrato.')
        lotto_origine = lotto_vecchio.Lotto
        lotto_vecchio.Corrente = False
        lotto_vecchio.SostituitoIl = ora
        lotto_vecchio.MotivoCambio = motivo
        piano.MotivoAnnullamento = f'Ripianificata con M-{nuovo_id}: {motivo}'[:500]
        db.session.add(StoricoRipianificazioneMista(
            IdBozzaPrecedente=id_bozza, IdBozzaNuova=nuovo_id,
            DataPrecedente=bozza.DataProduzione, DataNuova=nuova_data,
            LottoPrecedente=lotto_origine, LottoNuovo=lotto_nuovo.Lotto,
            Operatore=operatore, Motivo=motivo, DataOperazione=ora,
        ))
        _log(bozza, operatore, f'Ripianificato in M-{nuovo_id}', ora)
        db.session.commit()
        return int(nuovo_id)
    except (ErroreRipianificazioneMista, ErroreLotto):
        db.session.rollback()
        raise
    except (IntegrityError, SQLAlchemyError) as exc:
        db.session.rollback()
        raise ErroreRipianificazioneMista(
            'Impossibile ripianificare: lotto o risorse già occupati; nessuna modifica salvata.'
        ) from exc
    except Exception:
        db.session.rollback()
        raise
