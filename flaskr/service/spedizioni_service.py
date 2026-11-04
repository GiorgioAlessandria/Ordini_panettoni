"""Gestione locale spedizioni di righe ERP interamente prodotte.

Non scrive mai su ordini_cliente_aperti o StatoRigaOrdine. Non registra
spedizioni parziali: una riga completa puo' partire una sola volta.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import text, tuple_, and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    OrdiniCliente, StatoRigaOrdine, PianificazioneOrdini, PianificazioneProduzione,
    PianificazioneMistaAttiva, BozzaPianificazioneMistaOrdine,
    Spedizione, SpedizioneOrdine,
)
from flaskr.models.model_lotto import LottoPianificazione


class ErroreSpedizione(ValueError):
    pass


def _transazione_esclusiva():
    """Serializza i POST SQLite, come le conferme delle pianificazioni miste."""
    if db.engine.dialect.name != "sqlite":
        raise ErroreSpedizione("Servizio disponibile solamente con SQLite")
    db.session.rollback()  # Elimina una eventuale transazione di sola lettura.
    db.session.execute(text("BEGIN IMMEDIATE"))


def _quantita_chiusa(chiavi: list[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Somma quantita' chiuse nei piani tradizionali E misti attivi/chiusi."""
    quantita = defaultdict(int)
    for start in range(0, len(chiavi), 200):
        lotto = chiavi[start:start + 200]
        if not lotto:
            continue
        stmt = (
            db.select(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga,
                      db.func.sum(PianificazioneOrdini.QtaPianificata))
            .join(PianificazioneProduzione,
                  PianificazioneProduzione.IdPianificazione == PianificazioneOrdini.IdPianificazione)
            .where(tuple_(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga).in_(lotto),
                   PianificazioneOrdini.Stato == "Chiuso",
                   PianificazioneProduzione.StatoRigaOrdine == "Chiuso")
            .group_by(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga)
        )
        for doc, riga, totale in db.session.execute(stmt):
            quantita[(doc, riga)] += int(totale or 0)
        stmt_misto = (
            db.select(BozzaPianificazioneMistaOrdine.IdDocumento,
                      BozzaPianificazioneMistaOrdine.IdRigaDoc,
                      db.func.sum(BozzaPianificazioneMistaOrdine.QtaPianificata))
            .join(PianificazioneMistaAttiva,
                  PianificazioneMistaAttiva.IdBozza == BozzaPianificazioneMistaOrdine.IdBozza)
            .where(tuple_(BozzaPianificazioneMistaOrdine.IdDocumento,
                          BozzaPianificazioneMistaOrdine.IdRigaDoc).in_(lotto),
                   PianificazioneMistaAttiva.Stato == "Chiuso")
            .group_by(BozzaPianificazioneMistaOrdine.IdDocumento,
                      BozzaPianificazioneMistaOrdine.IdRigaDoc)
        )
        for doc, riga, totale in db.session.execute(stmt_misto):
            quantita[(doc, riga)] += int(totale or 0)
    return dict(quantita)


def _lotti_chiusi(chiavi: list[tuple[int, int]]) -> dict[tuple[int, int], list[str]]:
    """Tutti i lotti correnti delle pianificazioni chiuse, miste e tradizionali.

    Un ordine può essere stato completato in più pianificazioni distinte.
    """
    risultato: dict[tuple[int, int], set[str]] = defaultdict(set)
    for start in range(0, len(chiavi), 200):
        blocco = chiavi[start:start + 200]
        if not blocco:
            continue
        for doc, riga, lotto in db.session.execute(
            db.select(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga,
                      LottoPianificazione.Lotto)
            .join(PianificazioneProduzione,
                  PianificazioneProduzione.IdPianificazione == PianificazioneOrdini.IdPianificazione)
            .join(LottoPianificazione, and_(LottoPianificazione.TipoOwner == "TRAD",
                   LottoPianificazione.IdOwner == PianificazioneOrdini.IdPianificazione,
                   LottoPianificazione.Corrente.is_(True)))
            .where(tuple_(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga).in_(blocco),
                   PianificazioneOrdini.Stato == "Chiuso",
                   PianificazioneProduzione.StatoRigaOrdine == "Chiuso")
        ):
            risultato[(doc, riga)].add(lotto)
        for doc, riga, lotto in db.session.execute(
            db.select(BozzaPianificazioneMistaOrdine.IdDocumento,
                      BozzaPianificazioneMistaOrdine.IdRigaDoc,
                      LottoPianificazione.Lotto)
            .join(PianificazioneMistaAttiva,
                  PianificazioneMistaAttiva.IdBozza == BozzaPianificazioneMistaOrdine.IdBozza)
            .join(LottoPianificazione, and_(LottoPianificazione.TipoOwner == "MISTA",
                  LottoPianificazione.IdOwner == BozzaPianificazioneMistaOrdine.IdBozza,
                  LottoPianificazione.Corrente.is_(True)))
            .where(tuple_(BozzaPianificazioneMistaOrdine.IdDocumento,
                          BozzaPianificazioneMistaOrdine.IdRigaDoc).in_(blocco),
                   PianificazioneMistaAttiva.Stato == "Chiuso")
        ):
            risultato[(doc, riga)].add(lotto)
    return {chiave: sorted(lotti) for chiave, lotti in risultato.items()}


def _chiavi_prenotate() -> set[tuple[int, int]]:
    return set(db.session.execute(
        db.select(SpedizioneOrdine.IdDocumento, SpedizioneOrdine.IdRigaDoc)
        .join(Spedizione, Spedizione.IdSpedizione == SpedizioneOrdine.IdSpedizione)
        .where(Spedizione.Stato.in_(("Preparata", "Spedita")), SpedizioneOrdine.Attiva.is_(True))
    ).all())


def _ordini(chiavi=None):
    stmt = db.select(OrdiniCliente).options(joinedload(OrdiniCliente.Cliente),
                                               joinedload(OrdiniCliente.stato))
    if chiavi is not None:
        stmt = stmt.where(tuple_(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc).in_(chiavi))
    stmt = stmt.order_by(OrdiniCliente.DataConsegna,
                         OrdiniCliente.CodCliFor,
                         OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc)
    return db.session.scalars(stmt).all()


def _disponibili(ordini):
    chiavi = [(o.IdDocumento, o.IdRigaDoc) for o in ordini]
    prodotti = _quantita_chiusa(chiavi)
    prenotati = _chiavi_prenotate()
    lotti = _lotti_chiusi(chiavi)
    disponibili = []
    for o in ordini:
        chiave = (o.IdDocumento, o.IdRigaDoc)
        # Lo stato esistente Chiuso è obbligatorio, non introdurre Prodotto.
        if o.stato is None or o.stato.Stato != "Chiuso":
            continue
        if (chiave in prenotati or int(o.QTA_SALDO_DOC or 0) <= 0
                or prodotti.get(chiave, 0) < int(o.QTA_SALDO_DOC)):
            continue
        # Solo attributi di visualizzazione; le tabelle ERP restano immutate.
        o.QtaAssegnataSpedizione = prodotti[chiave]
        o.LottiSpedizione = lotti.get(chiave, [])
        disponibili.append(o)
    return disponibili


def ordini_disponibili_spedizione():
    """Solo righe interamente prodotte e non gia' preparate/spedite."""
    return _disponibili(_ordini())


def elenco_spedizioni():
    righe = db.session.scalars(
        db.select(Spedizione)
        .options(selectinload(Spedizione.righe))
        .order_by(Spedizione.IdSpedizione.desc())
    ).all()
    return [s for s in righe if s.Stato == "Preparata"], [s for s in righe if s.Stato == "Spedita"]


def _parse_chiavi(ids: list[str]) -> list[tuple[int, int]]:
    if not 1 <= len(ids) <= 200:
        raise ErroreSpedizione("Seleziona da 1 a 200 righe ordine")
    try:
        chiavi = []
        for valore in ids:
            parti = valore.split("-")
            if len(parti) != 2 or any(not p.isdecimal() for p in parti):
                raise ValueError
            chiavi.append((int(parti[0]), int(parti[1])))
    except (AttributeError, ValueError) as exc:
        raise ErroreSpedizione("Identificativo ordine non valido") from exc
    if len(chiavi) != len(set(chiavi)):
        raise ErroreSpedizione("La selezione contiene ordini duplicati")
    return chiavi


def prepara_spedizione(ids: list[str], data_prevista: date | None,
                       note: str, operatore: str) -> int:
    chiavi = _parse_chiavi(ids)
    note = (note or "").strip()
    if len(note) > 600:
        raise ErroreSpedizione("Le note non possono superare 600 caratteri")
    try:
        _transazione_esclusiva()
        ordini = _ordini(chiavi)
        if len(ordini) != len(chiavi):
            raise ErroreSpedizione("Uno o piu' ordini non sono piu' disponibili nel feed ERP")
        validi = {(o.IdDocumento, o.IdRigaDoc): o for o in _disponibili(ordini)}
        if set(chiavi) != set(validi):
            raise ErroreSpedizione("Una o piu' righe non sono prodotte oppure sono gia' assegnate a spedizione")
        codici_clienti = {o.CodCliFor for o in ordini}
        if len(codici_clienti) != 1:
            raise ErroreSpedizione("Una spedizione puo' contenere soltanto ordini dello stesso cliente")
        primo = ordini[0]
        spedizione = Spedizione(
            CodCliFor=primo.CodCliFor,
            ClienteSnapshot=primo.Cliente.RagioneSociale if primo.Cliente else str(primo.CodCliFor),
            Stato="Preparata", DataPrevista=data_prevista, DataPartenza=None,
            DataCreazione=datetime.now(), OperatoreCreazione=operatore,
            OperatorePartenza=None, OperatoreAnnullamento=None, DataAnnullamento=None,
            Note=note or None,
        )
        db.session.add(spedizione)
        db.session.flush()
        for doc, riga in chiavi:
            o = validi[(doc, riga)]
            spedizione.righe.append(SpedizioneOrdine(
                IdDocumento=doc, IdRigaDoc=riga, CodArtSnapshot=o.CodArt,
                DesArtSnapshot=o.DesArt or o.CodArt, Quantita=int(o.QTA_SALDO_DOC),
                QtaAssegnata=o.QtaAssegnataSpedizione,
                LottiSnapshot=", ".join(o.LottiSpedizione) or None, Attiva=True))
        db.session.flush()
        id_spedizione = spedizione.IdSpedizione
        db.session.commit()
        return id_spedizione
    except (ErroreSpedizione, SQLAlchemyError) as exc:
        db.session.rollback()
        if isinstance(exc, ErroreSpedizione):
            raise
        raise ErroreSpedizione("Impossibile preparare la spedizione: verificare disponibilita' e duplicati") from exc


def conferma_partenza(id_spedizione: int, partenza: datetime, operatore: str) -> None:
    if partenza.tzinfo is not None:
        raise ErroreSpedizione("La data di partenza deve essere espressa nell’ora locale, senza fuso orario")
    if partenza > datetime.now() + timedelta(minutes=5):
        raise ErroreSpedizione("La data di partenza non puo' essere futura")
    try:
        _transazione_esclusiva()
        s = db.session.scalar(db.select(Spedizione)
                              .where(Spedizione.IdSpedizione == id_spedizione)
                              .options(selectinload(Spedizione.righe)))
        if s is None or s.Stato != "Preparata" or not s.righe:
            raise ErroreSpedizione("Spedizione inesistente o gia' confermata/annullata")
        if partenza < s.DataCreazione:
            raise ErroreSpedizione("La partenza non può precedere la preparazione della spedizione")
        chiavi = [(r.IdDocumento, r.IdRigaDoc) for r in s.righe]
        ordini = {(o.IdDocumento, o.IdRigaDoc): o for o in _ordini(chiavi)}
        if len(ordini) != len(chiavi):
            raise ErroreSpedizione("Il feed ERP e' cambiato: almeno una riga non e' piu' presente")
        chiuse = _quantita_chiusa(chiavi)
        for r in s.righe:
            chiave = (r.IdDocumento, r.IdRigaDoc)
            o = ordini[chiave]
            if (not r.Attiva or o.stato is None or o.stato.Stato != "Chiuso"
                    or o.CodCliFor != s.CodCliFor or o.CodArt != r.CodArtSnapshot
                    or int(o.QTA_SALDO_DOC or 0) != r.Quantita
                    or chiuse.get(chiave, 0) < r.Quantita):
                raise ErroreSpedizione(
                    f"Ordine {r.IdDocumento}-{r.IdRigaDoc} modificato o non completamente prodotto")
        s.Stato = "Spedita"
        s.DataPartenza = partenza
        s.OperatorePartenza = operatore
        db.session.commit()
    except (ErroreSpedizione, SQLAlchemyError) as exc:
        db.session.rollback()
        if isinstance(exc, ErroreSpedizione):
            raise
        raise ErroreSpedizione("Impossibile registrare la partenza") from exc


def annulla_preparazione(id_spedizione: int, operatore: str):
    """Libera gli ordini dalla bozza, conservando la testata annullata per audit."""
    try:
        _transazione_esclusiva()
        s = db.session.scalar(db.select(Spedizione)
                              .where(Spedizione.IdSpedizione == id_spedizione)
                              .options(selectinload(Spedizione.righe)))
        if s is None or s.Stato != "Preparata":
            raise ErroreSpedizione("Si possono annullare solo spedizioni preparate")
        s.Stato = "Annullata"
        s.OperatoreAnnullamento = operatore
        s.DataAnnullamento = datetime.now()
        for riga in s.righe:
            riga.Attiva = False  # libera il vincolo parziale; storico righe conservato.
        db.session.commit()
    except (ErroreSpedizione, SQLAlchemyError) as exc:
        db.session.rollback()
        if isinstance(exc, ErroreSpedizione):
            raise
        raise ErroreSpedizione("Impossibile annullare la preparazione") from exc
