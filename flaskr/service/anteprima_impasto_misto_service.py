"""Analisi di impasto condiviso con numero di cotte dichiarato dall'operatore.

Il software calcola pesi, quantità e disponibilità delle risorse ma non
calcola, stima o ricompone fisicamente le cotte miste.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.orm import selectinload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaArticoli, AnagraficaImpastatrici, CapacitaForno, Coefficienti,
    GruppoProduzione, OrdiniCliente, PianificazioneProduzione,
    RicettaFasi, TipologieLavorazione, TipRicetteAnagArticoli,
    TipLavorazioniAnagForni,
)
from flaskr.service.cotte_miste_service import ErroreCotteMiste, RigaCotta, ripartisci_cotte
from flaskr.service.ordini_service import recupera_quantita_impegnate
from flaskr.service.scadenze_giornaliere_service import articolo_cnf
from flaskr.service.pianificazione_service import (
    ErroreCalcoloPianificazione, blocchi_fase, recupero_fasi_per_ricetta,
)
from flaskr.service.risorse_produzione_service import (
    _capacita_kg, disponibilita_forno, impastatrici_libere, individua_fasi_risorse,
)


class ErroreAnteprimaMista(ValueError):
    pass


def _fasi_e_ricetta(codart: str):
    relazioni = db.session.scalars(db.select(TipRicetteAnagArticoli).where(
        TipRicetteAnagArticoli.CodArt == codart
    )).all()
    if len(relazioni) != 1:
        raise ErroreAnteprimaMista(f'{codart}: attesa una sola ricetta associata; trovate {len(relazioni)}.')
    try:
        fasi = recupero_fasi_per_ricetta(SimpleNamespace(IdRicetta=relazioni[0].IdRicetta))
    except ErroreCalcoloPianificazione as exc:
        raise ErroreAnteprimaMista(f"{codart}: {exc}") from exc
    forni = [f for f in fasi if _forno(f.lavorazione)]
    impasti = [f for f in fasi if _impasto(f.lavorazione)]
    if len(forni) != 1 or not impasti:
        raise ErroreAnteprimaMista(f'{codart}: ricetta senza una fase di forno unica o senza impasto.')
    firma = tuple((f.IdLavorazione, int(f.OffsetBlocchi), int(f.lavorazione.DurataBlocchi)) for f in impasti)
    return fasi, firma, forni[0]


def _forno(lavorazione):
    nome = ' '.join(str(v or '') for v in (
        lavorazione.TipologiaLavorazione, lavorazione.CodiceLavorazione,
        lavorazione.DescrizioneLavorazione,
    )).casefold()
    return any(t in nome for t in ('forno', 'cottura', 'inforn'))


def _impasto(lavorazione):
    nome = ' '.join(str(v or '') for v in (
        lavorazione.TipologiaLavorazione, lavorazione.CodiceLavorazione,
        lavorazione.DescrizioneLavorazione,
    )).casefold()
    return 'impast' in nome


def _prezzo_peso_unitario(articolo, coeff_glassatura, coeff_impasto):
    # Formula già applicata da ordini_service.calcolo_riepilogo_ordini.
    iniziale = Decimal(str(articolo.Prezzatura))
    if int(articolo.Glassatura) != 1:
        iniziale *= coeff_glassatura
    return iniziale * coeff_impasto


def carica_gruppi():
    return db.session.scalars(
        db.select(GruppoProduzione).where(GruppoProduzione.Attivo == 1)
        .order_by(GruppoProduzione.Nome)
    ).all()


def carica_scelte(gruppo_id: int, *, include_cnf: bool = False):
    gruppo = db.session.get(GruppoProduzione, gruppo_id)
    if gruppo is None or gruppo.Attivo != 1:
        raise ErroreAnteprimaMista('Gruppo di produzione inesistente o non attivo.')
    codici = {item.CodArt for item in gruppo.articoli}
    if len(codici) < 2:
        raise ErroreAnteprimaMista('Il gruppo non ha almeno due articoli configurati.')
    codici_visibili = codici if include_cnf else {cod for cod in codici if not articolo_cnf(cod)}
    articoli = db.session.scalars(db.select(AnagraficaArticoli).where(
        AnagraficaArticoli.CodArt.in_(codici_visibili)
    )).all()
    ordini = db.session.scalars(db.select(OrdiniCliente).where(
        OrdiniCliente.CodArt.in_(codici_visibili), OrdiniCliente.QTA_SALDO_DOC > 0
    ).options(selectinload(OrdiniCliente.Cliente), selectinload(OrdiniCliente.articolo))
        .order_by(OrdiniCliente.DataConsegna, OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc)
    ).all()
    impegnate = recupera_quantita_impegnate()
    disponibili = {
        (o.IdDocumento, o.IdRigaDoc): max(0, int(o.QTA_SALDO_DOC or 0) -
                                            int(impegnate.get((o.IdDocumento, o.IdRigaDoc), 0)))
        for o in ordini
    }
    return gruppo, articoli, [o for o in ordini if disponibili[(o.IdDocumento, o.IdRigaDoc)] > 0], disponibili


def forni_per_articolo(articoli):
    disponibili = {}
    for a in articoli:
        _fasi, _firma, forno = _fasi_e_ricetta(a.CodArt)
        associati = set(db.session.scalars(db.select(TipLavorazioniAnagForni.IdForno).where(
            TipLavorazioniAnagForni.IdLavorazione == forno.IdLavorazione
        )).all())
        tipo = 'TO' if int(a.Glassatura) == 1 else 'MI'
        capacita = db.session.scalars(db.select(CapacitaForno).where(
            CapacitaForno.IdForno.in_(associati),
            CapacitaForno.DimensionePanettone == a.Prezzatura,
            CapacitaForno.TipologiaPanettone == tipo,
        )).all()
        disponibili[a.CodArt] = {
            c.IdForno: int(c.Capienza) for c in capacita if int(c.Capienza or 0) > 0
        }
    return disponibili


def simula(*, gruppo_id: int, chiavi: list[str], forno_articolo: dict[str, int],
           cotte_per_forno: dict[int, int], data_inizio: date, blocco: str,
           impastatrici_ids: list[int], include_cnf: bool = False,
           quantita_per_ordine: dict[str, int] | None = None):
    if not chiavi or len(chiavi) > 200:
        raise ErroreAnteprimaMista('Seleziona da 1 a 200 ordini.')
    if blocco not in ('M', 'P'):
        raise ErroreAnteprimaMista('Blocco di inizio non valido.')
    if not impastatrici_ids or len(set(impastatrici_ids)) != len(impastatrici_ids):
        raise ErroreAnteprimaMista('Seleziona almeno una impastatrice, senza duplicati.')
    gruppo, articoli, ordini, disponibili = carica_scelte(gruppo_id, include_cnf=include_cnf)
    mappa_ordini = {f'{o.IdDocumento}-{o.IdRigaDoc}': o for o in ordini}
    if len(chiavi) != len(set(chiavi)) or any(k not in mappa_ordini for k in chiavi):
        raise ErroreAnteprimaMista('La selezione contiene ordini duplicati, assenti o già impegnati.')
    selezionati = [mappa_ordini[k] for k in chiavi]
    # Di default tutto il saldo disponibile. Le quantita' possono essere ridotte
    # manualmente per permettere anche pianificazioni miste parziali.
    quantita_per_ordine = quantita_per_ordine or {}
    if set(quantita_per_ordine) - set(chiavi):
        raise ErroreAnteprimaMista('Quantità indicate per ordini non selezionati.')
    quantita_richieste = {}
    for chiave in chiavi:
        ordine = mappa_ordini[chiave]
        residuo = disponibili[(ordine.IdDocumento, ordine.IdRigaDoc)]
        valore = quantita_per_ordine.get(chiave, residuo)
        if type(valore) is not int or not (1 <= valore <= residuo):
            raise ErroreAnteprimaMista(
                f'Ordine {chiave}: inserisci una quantità da 1 a {residuo}.'
            )
        quantita_richieste[(ordine.IdDocumento, ordine.IdRigaDoc)] = valore
    codici_scelti = {o.CodArt for o in selezionati}
    articoli_per_codice = {a.CodArt: a for a in articoli if a.CodArt in codici_scelti}
    fasi_per_codice = {cod: _fasi_e_ricetta(cod) for cod in codici_scelti}
    firme = {firma for _, firma, _ in fasi_per_codice.values()}
    if len(firme) != 1:
        raise ErroreAnteprimaMista('Le fasi di impasto delle ricette selezionate non coincidono. Non è sicuro creare un impasto condiviso.')

    capacita_per_codice = forni_per_articolo(articoli_per_codice.values())
    gruppi_forni = defaultdict(list)
    for o in selezionati:
        forno_id = forno_articolo.get(o.CodArt)
        if forno_id not in capacita_per_codice[o.CodArt]:
            raise ErroreAnteprimaMista(f'{o.CodArt}: seleziona un forno compatibile con formato e ricetta.')
        gruppi_forni[forno_id].append(o)
    if any(forno not in gruppi_forni for forno in cotte_per_forno):
        raise ErroreAnteprimaMista('Specificato un forno non utilizzato dagli articoli.')

    prima = selezionati[0].CodArt
    fasi_comuni = blocchi_fase(
        fasi_per_codice[prima][0], PianificazioneProduzione(),
        data_inizio.isoformat(), 0 if blocco == 'M' else 1,
    )
    try:
        fasi_impasto, _ = individua_fasi_risorse(fasi_comuni)
    except ErroreCalcoloPianificazione as exc:
        raise ErroreAnteprimaMista(str(exc)) from exc
    macchine = db.session.scalars(db.select(AnagraficaImpastatrici).where(
        AnagraficaImpastatrici.IdImpastatrice.in_(impastatrici_ids)
    )).all()
    if len(macchine) != len(impastatrici_ids):
        raise ErroreAnteprimaMista('Impastatrice non presente in anagrafica.')
    try:
        libere, _ = impastatrici_libere(macchine, fasi_impasto)
    except ErroreCalcoloPianificazione as exc:
        raise ErroreAnteprimaMista(str(exc)) from exc
    if set(libere) != set(impastatrici_ids):
        raise ErroreAnteprimaMista('Almeno una impastatrice è occupata nei blocchi di impasto richiesti.')

    risultati = {}
    qta_pianificate = defaultdict(int)
    # Verifica solo disponibilità dei blocchi e numero di cotte dichiarato.
    # NON stima l'occupazione fisica dei formati nelle singole infornate.
    for id_forno, ordini_forno in sorted(gruppi_forni.items()):
        fasi_forno = []
        for cod in sorted({o.CodArt for o in ordini_forno}):
            fasi_db, _, ricetta_forno = fasi_per_codice[cod]
            fasi_piano = blocchi_fase(
                fasi_db, PianificazioneProduzione(), data_inizio.isoformat(),
                0 if blocco == 'M' else 1,
            )
            fase = next(f for f in fasi_piano if f.IdRicettaFase == ricetta_forno.id)
            fasi_forno.append((fase, ricetta_forno.lavorazione, ricetta_forno))
        # Sono ammesse ricette con forni diversi, ma il singolo forno
        # deve impegnare un unico insieme di slot per la cotta mista.
        slot_riferimento = None
        disponibilita = []
        for fase, lavorazione, ricetta_forno in fasi_forno:
            durata = int(lavorazione.DurataBlocchi)
            if durata <= 0:
                raise ErroreAnteprimaMista('Durata forno non valida')
            firma_slot = (fase.DataFase, fase.Blocco, durata)
            if slot_riferimento is not None and firma_slot != slot_riferimento:
                raise ErroreAnteprimaMista(f'Forno {id_forno}: ricette con blocchi di cottura differenti.')
            slot_riferimento = firma_slot
            try:
                disponibili_ricetta, _ = disponibilita_forno(fase, lavorazione, ricetta_forno, id_forno)
            except ErroreCalcoloPianificazione as exc:
                raise ErroreAnteprimaMista(str(exc)) from exc
            disponibilita.append(disponibili_ricetta)
        disponibili_forno = min(disponibilita)
        numero = cotte_per_forno.get(id_forno)
        if numero is None:
            raise ErroreAnteprimaMista(f'Inserisci le cotte da utilizzare per il forno {id_forno}.')
        righe = [RigaCotta(
            documento=o.IdDocumento, riga=o.IdRigaDoc, codart=o.CodArt,
            richiesti=quantita_richieste[(o.IdDocumento, o.IdRigaDoc)],
            capienza_omogenea=capacita_per_codice[o.CodArt][id_forno],
            priorita=i,
        ) for i, o in enumerate(ordini_forno)]
        try:
            esito = ripartisci_cotte(righe, numero, disponibili_forno)
        except ErroreCotteMiste as exc:
            raise ErroreAnteprimaMista(f'Forno {id_forno}: {exc}') from exc
        risultati[id_forno] = {
            'esito': esito, 'libere': disponibili_forno,
            'data': slot_riferimento[0], 'blocco': slot_riferimento[1],
            'capacita': {o.CodArt: capacita_per_codice[o.CodArt][id_forno] for o in ordini_forno},
            'ordini': ordini_forno,
        }
        for chiave, pezzi in esito.assegnazioni.items():
            qta_pianificate[chiave] += pezzi

    if not any(qta_pianificate.values()):
        raise ErroreAnteprimaMista('Nessuna quantità pianificabile.')
    coefficients = {}
    for nome in ('PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA', 'PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO'):
        c = db.session.scalars(db.select(Coefficienti).where(Coefficienti.Coefficiente == nome)).one_or_none()
        if c is None:
            raise ErroreAnteprimaMista(f'Coefficiente mancante: {nome}')
        coefficients[nome] = Decimal(str(c.Valore))
    peso_grammi = sum((
        _prezzo_peso_unitario(articoli_per_codice[o.CodArt],
                             coefficients['PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA'],
                             coefficients['PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO'])
        * qta_pianificate.get((o.IdDocumento, o.IdRigaDoc), 0)
        for o in selezionati
    ), Decimal(0))
    capacita_kg = sum((libere[i] for i in impastatrici_ids), Decimal(0))
    if peso_grammi > capacita_kg * Decimal(1000):
        raise ErroreAnteprimaMista(
            f'Impasto richiesto: {peso_grammi / 1000:.2f} kg; '
            f'capacità libera delle impastatrici: {capacita_kg:.2f} kg. '
            'Riduci le cotte o seleziona altre impastatrici.'
        )
    return {
        'gruppo': gruppo,
        'risultati': risultati,
        'ordini': [dict(ordine=o,
                        richiesta=disponibili[(o.IdDocumento, o.IdRigaDoc)],
                        pianificata=qta_pianificate.get((o.IdDocumento, o.IdRigaDoc), 0),
                        residuo=disponibili[(o.IdDocumento, o.IdRigaDoc)] - qta_pianificate.get((o.IdDocumento, o.IdRigaDoc), 0))
                   for o in selezionati],
        'peso_kg': peso_grammi / 1000,
        'capacita_kg': capacita_kg,
        'fasi_impasto': [
            dict(IdRicettaFase=f.IdRicettaFase, IdLavorazione=f.IdLavorazione,
                 OffsetBlocchi=int(f.OffsetBlocchi), DataFase=f.DataFase,
                 Blocco=f.Blocco, DurataBlocchi=int(l.DurataBlocchi))
            for f, l in fasi_impasto
        ],
        'totale_pezzi': sum(qta_pianificate.values()),
        'totale_cotte': sum(cotte_per_forno.values()),
    }
