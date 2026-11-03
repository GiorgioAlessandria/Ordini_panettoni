"""Fase 3B — conferma e ciclo di vita di una pianificazione composta.

Dati storici: la bozza 3A non è alterata; la prenotazione 3B referenzia lo
snapshot. Nessun surplus né prelievo stock: ogni pezzo delle cotte è assegnato
ad un ordine ERP. I piani legacy non vengono modificati.

Per proteggere le risorse condivise, i servizi legacy devono essere aggiornati
assieme a questo file (risorse_produzione_service, ordini_service).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    BozzaPianificazioneMista, BozzaPianificazioneMistaOrdine,
    LogOperazioni, OrdiniCliente, PianificazioneMistaAttiva,
    PianificazioneOrdini, PrenotazioneMistaForno,
    PrenotazioneMistaImpastatrice, StatoRigaOrdine,
)
from flaskr.service.anteprima_impasto_misto_service import (
    ErroreAnteprimaMista, _fasi_e_ricetta, simula,
)
from flaskr.service.pianificazione_service import blocchi_fase
from flaskr.service.risorse_produzione_service import _blocchi_occupati


class ErroreAttivazioneMista(ValueError):
    pass


def _transazione_sqlite_immediata() -> None:
    """Una singola prenotazione alla volta, compresa la pianificazione legacy.

    Deve essere invocata PRIMA di letture nella sessione corrente. Le route
    eseguono qui solo operazioni POST con CSRF già verificato. Rollback esplicito
    di un'eventuale transazione di sola lettura preesistente della request.
    """
    if db.engine.dialect.name != 'sqlite':
        raise ErroreAttivazioneMista('Attivazione supportata soltanto su SQLite.')
    db.session.rollback()
    db.session.execute(text('BEGIN IMMEDIATE'))


def _firma_bozza(bozza: BozzaPianificazioneMista) -> tuple:
    righe = tuple(sorted((o.IdDocumento, o.IdRigaDoc, o.CodArt,
                          o.IdForno, int(o.QtaPianificata)) for o in bozza.ordini))
    forni = tuple(sorted((f.IdForno, f.DataFase, f.Blocco, int(f.NumeroCotte))
                         for f in bozza.forni))
    cotte = tuple(sorted((c.IdForno, c.NumeroCotta, c.CodArt, int(c.QtaPezzi))
                         for f in bozza.forni for c in f.cotte))
    fasi = tuple(sorted((f.IdRicettaFase, f.IdLavorazione, f.OffsetBlocchi,
                         f.DataFase, f.Blocco, f.DurataBlocchi)
                        for f in bozza.fasi_impasto))
    mixers = tuple(sorted(m.IdImpastatrice for m in bozza.impastatrici))
    return righe, forni, cotte, fasi, mixers


def _firma_esito(esito: dict, bozza: BozzaPianificazioneMista) -> tuple:
    mappa_forni = {o.CodArt: o.IdForno for o in bozza.ordini}
    righe = tuple(sorted((v['ordine'].IdDocumento, v['ordine'].IdRigaDoc,
                          v['ordine'].CodArt, mappa_forni[v['ordine'].CodArt],
                          int(v['pianificata']))
                         for v in esito['ordini'] if int(v['pianificata']) > 0))
    forni = tuple(sorted((fid, v['data'], v['blocco'], int(v['esito'].numero_cotte))
                         for fid, v in esito['risultati'].items()))
    cotte = tuple(sorted((fid, indice, codice, int(qta))
                         for fid, v in esito['risultati'].items()
                         for indice, contenuto in enumerate(v['esito'].cotte, 1)
                         for codice, qta in contenuto.items()))
    fasi = tuple(sorted((f['IdRicettaFase'], f['IdLavorazione'], f['OffsetBlocchi'],
                         f['DataFase'], f['Blocco'], f['DurataBlocchi'])
                        for f in esito['fasi_impasto']))
    mixers = tuple(sorted(m.IdImpastatrice for m in bozza.impastatrici))
    return righe, forni, cotte, fasi, mixers


def _valida_e_ricalcola(bozza: BozzaPianificazioneMista, *, include_cnf: bool) -> dict:
    if bozza.Stato != 'Bozza':
        raise ErroreAttivazioneMista('La proposta non è una bozza disponibile.')
    if not bozza.ordini or not bozza.forni or not bozza.fasi_impasto or not bozza.impastatrici:
        raise ErroreAttivazioneMista('Bozza priva di ordini, cotte, fasi o macchine.')
    forni_codici: dict[str, int] = {}
    for ordine in bozza.ordini:
        vecchio = forni_codici.setdefault(ordine.CodArt, ordine.IdForno)
        if vecchio != ordine.IdForno:
            raise ErroreAttivazioneMista('Un codice è stato assegnato a forni differenti nella stessa bozza.')
    try:
        esito = simula(
            gruppo_id=bozza.IdGruppo,
            chiavi=[f'{o.IdDocumento}-{o.IdRigaDoc}' for o in bozza.ordini],
            forno_articolo=forni_codici,
            cotte_per_forno={f.IdForno: int(f.NumeroCotte) for f in bozza.forni},
            data_inizio=bozza.DataProduzione,
            blocco=bozza.BloccoIniziale,
            impastatrici_ids=[m.IdImpastatrice for m in bozza.impastatrici],
            include_cnf=include_cnf,
        )
    except (ErroreAnteprimaMista, ValueError) as exc:
        raise ErroreAttivazioneMista(f'La proposta non è più confermabile: {exc}') from exc
    if _firma_bozza(bozza) != _firma_esito(esito, bozza):
        raise ErroreAttivazioneMista(
            'La disponibilità degli ordini o la composizione delle cotte è cambiata. '
            'Riapri la proposta, ripeti la simulazione e crea una nuova bozza.'
        )
    if abs(Decimal(str(esito['peso_kg'])) * 1000 - Decimal(str(bozza.PesoTotaleGrammi))) > Decimal('0.005'):
        raise ErroreAttivazioneMista('Il peso dell’impasto è cambiato: riesegui la simulazione.')
    return esito


def _rileggi_stato(id_documento: int, id_riga: int, adesso: datetime, operatore: str) -> None:
    """Ricalcola stato ordine includendo piani legacy e piani misti."""
    o = db.session.get(OrdiniCliente, (id_documento, id_riga))
    if o is None:
        raise ErroreAttivazioneMista('Ordine ERP non più presente: aggiorna il feed.')
    if o.QTA_SALDO_DOC is None:
        raise ErroreAttivazioneMista('Saldo ordine sconosciuto.')
    legacy = db.session.execute(
        db.select(PianificazioneOrdini.Stato, PianificazioneOrdini.QtaPianificata).where(
            PianificazioneOrdini.IdDocumento == id_documento,
            PianificazioneOrdini.IdRiga == id_riga,
            PianificazioneOrdini.Stato.in_(('In produzione', 'Chiuso'))
        )
    ).all()
    miste = db.session.execute(
        db.select(PianificazioneMistaAttiva.Stato, BozzaPianificazioneMistaOrdine.QtaPianificata)
        .join(BozzaPianificazioneMistaOrdine,
              BozzaPianificazioneMistaOrdine.IdBozza == PianificazioneMistaAttiva.IdBozza)
        .where(BozzaPianificazioneMistaOrdine.IdDocumento == id_documento,
               BozzaPianificazioneMistaOrdine.IdRigaDoc == id_riga,
               PianificazioneMistaAttiva.Stato.in_(('In produzione','Chiuso')))
    ).all()
    righe = [*legacy, *miste]
    impegnate = sum(int(q) for _, q in righe)
    saldo = int(o.QTA_SALDO_DOC)
    if saldo == 0:
        stato = 'Chiuso'
    elif impegnate >= saldo:
        stato = 'In produzione' if any(s == 'In produzione' for s, _ in righe) else 'Chiuso'
    else:
        stato = 'Nuovo'
    record = db.session.get(StatoRigaOrdine, (id_documento, id_riga))
    if record is None:
        record = StatoRigaOrdine(IdDocumento=id_documento, IdRigaDoc=id_riga,
                                Stato=stato, Operatore=operatore,
                                DataRegistrazione=adesso.date(), synced_at=adesso)
        db.session.add(record)
    else:
        record.Stato = stato
        record.Operatore = operatore
        record.DataRegistrazione = adesso.date()
        record.synced_at = adesso


def _log(bozza: BozzaPianificazioneMista, operatore: str, stato: str, adesso: datetime) -> None:
    for r in bozza.ordini:
        db.session.add(LogOperazioni(
            IdDocumento=r.IdDocumento, IdRigaDoc=r.IdRigaDoc,
            Stato=f'{stato} (impasto misto #{bozza.IdBozza})',
            Operatore=operatore, synced_at=adesso,
        ))


def conferma_bozza(id_bozza: int, operatore: str, *, include_cnf: bool = False) -> int:
    """Prenota sotto BEGIN IMMEDIATE: ordini, impastatrici e cotte per foro/blocco.

    Nessuna scrittura a stock. La richiesta deve essere autorizzata e protetta
    con CSRF dalla route. Un errore annulla TUTTA la transazione.
    """
    if not operatore or len(operatore) > 120:
        raise ErroreAttivazioneMista('Operatore non valido.')
    try:
        _transazione_sqlite_immediata()
        bozza = db.session.get(BozzaPianificazioneMista, id_bozza)
        if bozza is None:
            raise ErroreAttivazioneMista('Bozza non trovata.')
        esistente = db.session.get(PianificazioneMistaAttiva, id_bozza)
        if esistente is not None:
            if esistente.Stato == 'In produzione':
                db.session.rollback()
                return id_bozza  # doppio clic / POST ripetuto: nessuna duplicazione
            raise ErroreAttivazioneMista('Questa bozza è già stata conclusa o annullata.')
        esito = _valida_e_ricalcola(bozza, include_cnf=include_cnf)
        adesso = datetime.now()
        db.session.add(PianificazioneMistaAttiva(
            IdBozza=id_bozza, Stato='In produzione',
            OperatoreConferma=operatore, ConfermataIl=adesso,
        ))
        db.session.flush()
        # Prenota ogni macchina in ogni blocco occupato dalle fasi condivise.
        for macchina in bozza.impastatrici:
            if Decimal(str(macchina.KgAssegnati)) <= 0:
                continue  # macchine opzionali mai utilizzate, nessuna prenotazione
            for fase in bozza.fasi_impasto:
                for giorno, blocco in _blocchi_occupati(
                    fase.DataFase, fase.Blocco, int(fase.DurataBlocchi)
                ):
                    db.session.add(PrenotazioneMistaImpastatrice(
                        IdBozza=id_bozza, IdImpastatrice=macchina.IdImpastatrice,
                        DataFase=giorno, Blocco=blocco,
                        KgAssegnati=macchina.KgAssegnati,
                    ))
        # Un forno può avere più ricette nello stesso slot; la cotta si conta UNA volta.
        for forno in bozza.forni:
            codici = {o.CodArt for o in bozza.ordini if o.IdForno == forno.IdForno}
            durate = set()
            limiti = set()
            for codice in codici:
                fasi_db, _firma, ricetta_forno = _fasi_e_ricetta(codice)
                durate.add(int(ricetta_forno.lavorazione.DurataBlocchi))
                limiti.add(int(ricetta_forno.CapacitaMax))
            if len(durate) != 1 or not limiti:
                raise ErroreAttivazioneMista('Fasi forno non compatibili nella proposta.')
            massimo = min(limiti)
            for giorno, blocco in _blocchi_occupati(
                forno.DataFase, forno.Blocco, next(iter(durate))
            ):
                db.session.add(PrenotazioneMistaForno(
                    IdBozza=id_bozza, IdForno=forno.IdForno,
                    DataFase=giorno, Blocco=blocco,
                    NumeroCotte=int(forno.NumeroCotte), MaxCotteBlocco=massimo,
                ))
        db.session.flush()
        for r in bozza.ordini:
            _rileggi_stato(r.IdDocumento, r.IdRigaDoc, adesso, operatore)
        _log(bozza, operatore, 'In produzione', adesso)
        db.session.commit()
        return id_bozza
    except ErroreAttivazioneMista:
        db.session.rollback()
        raise
    except (IntegrityError, SQLAlchemyError) as exc:
        db.session.rollback()
        raise ErroreAttivazioneMista(
            'Prenotazione non riuscita: una risorsa potrebbe essere già occupata. '
            'Nessuna modifica confermata.'
        ) from exc


def chiudi_piano_misto(id_bozza: int, operatore: str) -> None:
    """Completa gli ordini assegnati; nessuna operazione stock (surplus nullo)."""
    try:
        _transazione_sqlite_immediata()
        piano = db.session.get(PianificazioneMistaAttiva, id_bozza)
        if piano is None or piano.Stato != 'In produzione':
            raise ErroreAttivazioneMista('Pianificazione mista non attiva.')
        adesso = datetime.now()
        piano.Stato = 'Chiuso'
        piano.ChiusaIl = adesso
        piano.OperatoreChiusura = operatore
        db.session.flush()
        for r in piano.bozza.ordini:
            _rileggi_stato(r.IdDocumento, r.IdRigaDoc, adesso, operatore)
        _log(piano.bozza, operatore, 'Chiuso', adesso)
        # Come per i piani storici, prenotazioni conservate nello storico.
        db.session.commit()
    except ErroreAttivazioneMista:
        db.session.rollback()
        raise
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise ErroreAttivazioneMista('Chiusura non riuscita: nessuna modifica salvata.') from exc


def annulla_piano_misto(id_bozza: int, operatore: str, motivo: str) -> None:
    """Storna solo prenotazioni e quantità (nessun movimento stock effettuato)."""
    motivo = (motivo or '').strip()
    if len(motivo) < 5 or len(motivo) > 500:
        raise ErroreAttivazioneMista('Indica un motivo tra 5 e 500 caratteri.')
    try:
        _transazione_sqlite_immediata()
        piano = db.session.get(PianificazioneMistaAttiva, id_bozza)
        if piano is None or piano.Stato != 'In produzione':
            raise ErroreAttivazioneMista('Solo un piano misto attivo può essere annullato.')
        adesso = datetime.now()
        piano.Stato = 'Annullato'
        piano.OperatoreAnnullamento = operatore
        piano.AnnullataIl = adesso
        piano.MotivoAnnullamento = motivo
        db.session.execute(delete(PrenotazioneMistaForno).where(
            PrenotazioneMistaForno.IdBozza == id_bozza))
        db.session.execute(delete(PrenotazioneMistaImpastatrice).where(
            PrenotazioneMistaImpastatrice.IdBozza == id_bozza))
        db.session.flush()
        for r in piano.bozza.ordini:
            _rileggi_stato(r.IdDocumento, r.IdRigaDoc, adesso, operatore)
        _log(piano.bozza, operatore, 'Annullato', adesso)
        db.session.commit()
    except ErroreAttivazioneMista:
        db.session.rollback()
        raise
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise ErroreAttivazioneMista('Annullamento non riuscito: nessuna modifica salvata.') from exc
