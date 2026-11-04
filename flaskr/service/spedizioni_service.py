"""Gestione logistica su snapshot locali, indipendenti dal feed ERP.

Gli ordini Chiuso restano disponibili quando l'ERP li elimina. Solo la
conferma della PARTENZA li toglie dai pronti; la testata rimane nello storico.
Le spedizioni sono complete per riga ordine e contengono un unico cliente.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from flaskr.extensions import db
from flaskr.models.model_ordini import Spedizione, SpedizioneOrdine
from flaskr.models.model_spedizioni_snapshot import OrdineProntoSpedizione


class ErroreSpedizione(ValueError):
    pass


def _transazione_esclusiva():
    """Serializza le conferme rispetto al sync SQLite."""
    if db.engine.dialect.name != "sqlite":
        raise ErroreSpedizione("Servizio disponibile solamente con SQLite")
    db.session.rollback()
    db.session.execute(text("BEGIN IMMEDIATE"))


def _chiavi_prenotate() -> set[tuple[int, int]]:
    return set(db.session.execute(
        db.select(SpedizioneOrdine.IdDocumento, SpedizioneOrdine.IdRigaDoc)
        .join(Spedizione, Spedizione.IdSpedizione == SpedizioneOrdine.IdSpedizione)
        .where(Spedizione.Stato.in_(("Preparata", "Spedita")),
               SpedizioneOrdine.Attiva.is_(True))
    ).all())


def _ordini(chiavi: list[tuple[int, int]] | None = None):
    """Dati di sola lettura del registro; non cerca ordini_cliente_aperti."""
    stmt = db.select(OrdineProntoSpedizione)
    if chiavi is not None:
        if not chiavi:
            return []
        stmt = stmt.where(tuple_(OrdineProntoSpedizione.IdDocumento,
                                 OrdineProntoSpedizione.IdRigaDoc).in_(chiavi))
    stmt = stmt.order_by(OrdineProntoSpedizione.DataConsegna,
                         OrdineProntoSpedizione.CodCliFor,
                         OrdineProntoSpedizione.IdDocumento,
                         OrdineProntoSpedizione.IdRigaDoc)
    return db.session.scalars(stmt).all()


def _presentazione(s: OrdineProntoSpedizione):
    """Mantiene il contratto del template e dei commenti in sola lettura."""
    return SimpleNamespace(
        IdDocumento=s.IdDocumento, IdRigaDoc=s.IdRigaDoc,
        CodCliFor=s.CodCliFor,
        Cliente=SimpleNamespace(RagioneSociale=s.ClienteSnapshot),
        CodArt=s.CodArt, DesArt=s.DesArt, DataConsegna=s.DataConsegna,
        QTA_SALDO_DOC=s.QtaSaldoERPSnapshot,
        QtaAssegnataSpedizione=int(s.QtaAssegnataSpedizione),
        LottiSpedizione=s.LottiSnapshot.split(", ") if s.LottiSnapshot else [],
        PresenteERP=bool(s.PresenteERP),
        ConsuntivoMancante=int(s.QtaAssegnataSpedizione) <= 0,
    )


def _disponibili(ordini, *, includi_da_verificare: bool = False):
    prenotati = _chiavi_prenotate()
    risposta = []
    for s in ordini:
        key = (s.IdDocumento, s.IdRigaDoc)
        if key in prenotati or not s.ConfermatoChiuso:
            continue
        if not includi_da_verificare and int(s.QtaAssegnataSpedizione) <= 0:
            continue
        risposta.append(_presentazione(s))
    return risposta


def ordini_disponibili_spedizione():
    """Include i CHIUSO privi di consuntivo, visibili ma NON selezionabili."""
    return _disponibili(_ordini(), includi_da_verificare=True)


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
        righe = _ordini(chiavi)
        if len(righe) != len(chiavi):
            raise ErroreSpedizione("Uno o piu' ordini non risultano nel registro logistico")
        validi = {(o.IdDocumento, o.IdRigaDoc): o
                  for o in _disponibili(righe)}
        if set(chiavi) != set(validi):
            raise ErroreSpedizione("Ordine non Chiuso, gia' prenotato o senza consuntivo verificato")
        codici_clienti = {o.CodCliFor for o in validi.values()}
        if len(codici_clienti) != 1:
            raise ErroreSpedizione("Una spedizione puo' contenere solo ordini dello stesso cliente")
        primo = next(iter(validi.values()))
        spedizione = Spedizione(
            CodCliFor=primo.CodCliFor,
            ClienteSnapshot=primo.Cliente.RagioneSociale,
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
                DesArtSnapshot=o.DesArt or o.CodArt,
                Quantita=o.QtaAssegnataSpedizione,
                QtaAssegnata=o.QtaAssegnataSpedizione,
                LottiSnapshot=", ".join(o.LottiSpedizione) or None,
                Attiva=True,
            ))
        db.session.flush()
        risultato = spedizione.IdSpedizione
        db.session.commit()
        return risultato
    except (ErroreSpedizione, SQLAlchemyError) as exc:
        db.session.rollback()
        if isinstance(exc, ErroreSpedizione):
            raise
        raise ErroreSpedizione("Impossibile preparare la spedizione: verifica duplicati e disponibilita'") from exc


def conferma_partenza(id_spedizione: int, partenza: datetime, operatore: str) -> None:
    if partenza.tzinfo is not None:
        raise ErroreSpedizione("La data di partenza deve essere nell'ora locale")
    if partenza > datetime.now() + timedelta(minutes=5):
        raise ErroreSpedizione("La partenza non puo' essere futura")
    try:
        _transazione_esclusiva()
        s = db.session.scalar(db.select(Spedizione)
                              .where(Spedizione.IdSpedizione == id_spedizione)
                              .options(selectinload(Spedizione.righe)))
        if s is None or s.Stato != "Preparata" or not s.righe:
            raise ErroreSpedizione("Spedizione inesistente o gia' confermata/annullata")
        if partenza < s.DataCreazione:
            raise ErroreSpedizione("La partenza non puo' precedere la preparazione")
        chiavi = [(r.IdDocumento, r.IdRigaDoc) for r in s.righe]
        pronti = {(x.IdDocumento, x.IdRigaDoc): x for x in _ordini(chiavi)}
        if len(pronti) != len(chiavi):
            raise ErroreSpedizione("Mancano righe nel registro logistico")
        for riga in s.righe:
            key = (riga.IdDocumento, riga.IdRigaDoc)
            snap = pronti[key]
            if (not riga.Attiva or not snap.ConfermatoChiuso or
                    snap.CodCliFor != s.CodCliFor or
                    snap.CodArt != riga.CodArtSnapshot or
                    int(snap.QtaAssegnataSpedizione) != riga.Quantita or
                    riga.Quantita <= 0):
                raise ErroreSpedizione(
                    f"Ordine {riga.IdDocumento}-{riga.IdRigaDoc}: dati logistici cambiati")
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
    """Rende di nuovo disponibile la riga; conserva la testata annullata."""
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
            riga.Attiva = False
        db.session.commit()
    except (ErroreSpedizione, SQLAlchemyError) as exc:
        db.session.rollback()
        if isinstance(exc, ErroreSpedizione):
            raise
        raise ErroreSpedizione("Impossibile annullare la preparazione") from exc
