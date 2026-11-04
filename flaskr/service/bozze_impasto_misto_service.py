"""Snapshot di simulazioni multi-articolo. NON crea pianificazioni attive.

La bozza conserva ordini, numero manuale di cotte, macchine e capacità.
La composizione fisica per singola cotta non viene generata automaticamente. Non occupa risorse e può diventare obsoleta:
prima di ogni futura conferma produttiva va rivalidata sotto lock SQLite.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import re

from flaskr.extensions import db
from flaskr.service.lotto_pianificazione_service import riserva_e_assegna_lotto_bozza
from flaskr.models.model_ordini import (
    AnagraficaImpastatrici, BozzaPianificazioneMista,
    BozzaPianificazioneMistaCotta, BozzaPianificazioneMistaForno,
    BozzaPianificazioneMistaImpastatrice, BozzaPianificazioneMistaOrdine,
    BozzaPianificazioneMistaFase,
)
from flaskr.service.risorse_produzione_service import (
    _capacita_kg, distribuisci_peso_impastatrici,
)


class ErroreBozzaMista(ValueError):
    pass


def _d(v) -> Decimal:
    return Decimal(str(v))


def salva_bozza(*, esito: dict, gruppo_id: int, data_inizio, blocco: str,
                forno_articolo: dict[str, int], impastatrici_ids: list[int],
                richiesta_id: str, operatore: str, progressivo_lotto: str | None = None,
                commit: bool = True) -> int:
    """Salva una simulazione GIÀ rivalidata dal server. Una sola commit atomica.

    Anche se il salvataggio è ripetuto con lo stesso UUID, restituisce la stessa
    bozza senza duplicare righe. Il chiamante gestisce gli errori e il rollback.
    """
    if not re.fullmatch(r"[a-f0-9]{32}", richiesta_id or ""):
        raise ErroreBozzaMista("Identificativo richiesta non valido. Ricarica il modulo.")
    esistente = db.session.scalar(
        db.select(BozzaPianificazioneMista.IdBozza).where(
            BozzaPianificazioneMista.RichiestaId == richiesta_id
        )
    )
    if esistente is not None:
        return int(esistente)
    if blocco not in ('M', 'P') or not operatore or len(operatore) > 120:
        raise ErroreBozzaMista("Data, blocco o operatore non validi.")
    if (not esito.get('ordini') or not esito.get('risultati') or
            not esito.get('fasi_impasto') or not impastatrici_ids):
        raise ErroreBozzaMista("Anteprima incompleta: impossibile salvare la bozza.")
    if len(set(impastatrici_ids)) != len(impastatrici_ids):
        raise ErroreBozzaMista("Impastatrici duplicate.")

    totale_pezzi = 0
    previsto_per_codice_forno = defaultdict(int)
    ordini_salvabili = []
    for voce in esito['ordini']:
        o = voce['ordine']
        qta = int(voce['pianificata'])
        if qta < 0 or qta > int(voce['richiesta']):
            raise ErroreBozzaMista("Quantità simulata non coerente con il residuo.")
        if qta == 0:
            continue
        forno_id = forno_articolo[o.CodArt]
        if forno_id not in esito['risultati']:
            raise ErroreBozzaMista("Forno selezionato non presente nella simulazione.")
        ordine_riga = {
            'IdDocumento': o.IdDocumento, 'IdRigaDoc': o.IdRigaDoc,
            'CodArt': o.CodArt, 'IdForno': forno_id, 'QtaPianificata': qta,
            'QtaDisponibileSnapshot': int(voce['richiesta']),
        }
        ordini_salvabili.append(ordine_riga)
        totale_pezzi += qta
        previsto_per_codice_forno[(forno_id, o.CodArt)] += qta
    if len({(v['IdDocumento'], v['IdRigaDoc']) for v in ordini_salvabili}) != len(ordini_salvabili):
        raise ErroreBozzaMista("Ordine duplicato nella simulazione.")
    if totale_pezzi <= 0 or totale_pezzi != int(esito['totale_pezzi']):
        raise ErroreBozzaMista("Totale dei pezzi non coerente.")

    righe_cotte = []
    for id_forno, dati in esito['risultati'].items():
        numero = int(dati['esito'].numero_cotte)
        if not 1 <= numero <= int(dati['libere']):
            raise ErroreBozzaMista("Cotte non coerenti con il limite del forno.")
        # Il numero di cotte e' dichiarato dall'operatore. Non avendo fornito
        # la composizione per singola cotta, il servizio non inventa righe fisiche.
        # L'assenza di righe `cotte` e' quindi lecita per i nuovi piani manuali.
        if dati['esito'].cotte and len(dati['esito'].cotte) != numero:
            raise ErroreBozzaMista("Composizione delle cotte incompleta.")
        for i, cotta in enumerate(dati['esito'].cotte, start=1):
            if not cotta or sum(int(x) for x in cotta.values()) <= 0:
                raise ErroreBozzaMista("Cotta priva di pezzi.")
            for codice, pezzi in cotta.items():
                if int(pezzi) <= 0:
                    raise ErroreBozzaMista("Cotta con quantità non positiva.")
                righe_cotte.append(dict(IdForno=id_forno, NumeroCotta=i,
                                       CodArt=codice, QtaPezzi=int(pezzi)))
    calcolato_per_codice_forno = defaultdict(int)
    for c in righe_cotte:
        calcolato_per_codice_forno[(c['IdForno'], c['CodArt'])] += c['QtaPezzi']
    if righe_cotte and dict(previsto_per_codice_forno) != dict(calcolato_per_codice_forno):
        raise ErroreBozzaMista("Distribuzione delle cotte diversa dalle quantità per ordine.")
    if sum(int(d['esito'].numero_cotte) for d in esito['risultati'].values()) != int(esito['totale_cotte']):
        raise ErroreBozzaMista("Totale cotte non coerente.")

    macchine = db.session.scalars(db.select(AnagraficaImpastatrici).where(
        AnagraficaImpastatrici.IdImpastatrice.in_(impastatrici_ids)
    )).all()
    if {m.IdImpastatrice for m in macchine} != set(impastatrici_ids):
        raise ErroreBozzaMista("Almeno una impastatrice non esiste più.")
    peso_kg = _d(esito['peso_kg'])
    if peso_kg <= 0:
        raise ErroreBozzaMista("Peso impasto non valido.")
    riparto_macchine = distribuisci_peso_impastatrici(
        peso_kg, {m.IdImpastatrice: _capacita_kg(m) for m in macchine}
    )
    if sum(riparto_macchine.values(), Decimal(0)) != peso_kg:
        raise ErroreBozzaMista("Ripartizione impasto non coerente.")

    bozza = BozzaPianificazioneMista(
        RichiestaId=richiesta_id, IdGruppo=gruppo_id,
        DataProduzione=data_inizio, BloccoIniziale=blocco,
        PesoTotaleGrammi=peso_kg * Decimal(1000),
        NumeroCotte=int(esito['totale_cotte']),
        Operatore=operatore, CreataIl=datetime.now(), Stato='Bozza',
    )
    db.session.add(bozza)
    db.session.flush()
    # Lotto assegnato già al primo salvataggio, con le stesse fasi effettive
    # della simulazione e nella stessa transazione delle righe della bozza.
    riserva_e_assegna_lotto_bozza(
        id_bozza=bozza.IdBozza, fasi_impasto=esito["fasi_impasto"],
        progressivo=progressivo_lotto, operatore=operatore,
    )
    for o in ordini_salvabili:
        db.session.add(BozzaPianificazioneMistaOrdine(IdBozza=bozza.IdBozza, **o))
    for id_forno, dati in esito['risultati'].items():
        forno = BozzaPianificazioneMistaForno(
            IdBozza=bozza.IdBozza, IdForno=id_forno, DataFase=dati['data'],
            Blocco=dati['blocco'], NumeroCotte=int(dati['esito'].numero_cotte),
            CotteLibereSnapshot=int(dati['libere']),
        )
        db.session.add(forno)
    for c in righe_cotte:
        db.session.add(BozzaPianificazioneMistaCotta(IdBozza=bozza.IdBozza, **c))
    fasi = esito['fasi_impasto']
    if len({f['IdRicettaFase'] for f in fasi}) != len(fasi):
        raise ErroreBozzaMista("Fasi di impasto duplicate.")
    for f in fasi:
        if f['Blocco'] not in ('M', 'P') or int(f['DurataBlocchi']) < 1:
            raise ErroreBozzaMista("Fase di impasto non valida.")
        db.session.add(BozzaPianificazioneMistaFase(
            IdBozza=bozza.IdBozza, IdRicettaFase=f['IdRicettaFase'],
            IdLavorazione=f['IdLavorazione'], OffsetBlocchi=int(f['OffsetBlocchi']),
            DataFase=f['DataFase'], Blocco=f['Blocco'], DurataBlocchi=int(f['DurataBlocchi']),
        ))
    for id_macchina in impastatrici_ids:
        db.session.add(BozzaPianificazioneMistaImpastatrice(
            IdBozza=bozza.IdBozza, IdImpastatrice=id_macchina,
            KgAssegnati=riparto_macchine.get(id_macchina, Decimal(0)),
        ))
    # Nel form unificato la conferma produttiva viene effettuata nella STESSA
    # transazione: flush visibile alla validazione, senza commit intermedio.
    db.session.flush()
    if commit:
        db.session.commit()
    return bozza.IdBozza


def carica_bozze(*, includi_archiviate: bool = False, limite: int = 100):
    stmt = db.select(BozzaPianificazioneMista)
    if not includi_archiviate:
        stmt = stmt.where(BozzaPianificazioneMista.Stato == 'Bozza')
    return db.session.scalars(
        stmt.order_by(BozzaPianificazioneMista.CreataIl.desc(),
                      BozzaPianificazioneMista.IdBozza.desc()).limit(limite)
    ).all()
