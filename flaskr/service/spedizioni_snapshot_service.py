"""Conserva per la logistica gli ordini CHIUSO prima del cleanup del sync ERP.

Questo servizio opera con una Session SQLAlchemy esplicita: e' utilizzabile dal
sync indipendente da Flask. NON esegue commit; upsert e cancellazione ERP vanno
eseguiti in una sola transazione SQLite BEGIN IMMEDIATE.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select, tuple_
from sqlalchemy.orm import Session

from flaskr.models.model_consuntivi import ConsuntivoProduzioneOrdine
from flaskr.models.model_lotto import LottoPianificazione
from flaskr.models.model_ordini import (
    AnagraficaClienti, BozzaPianificazioneMistaOrdine,
    OrdiniCliente, PianificazioneMistaAttiva, PianificazioneOrdini,
    PianificazioneProduzione, StatoRigaOrdine,
)
from flaskr.models.model_spedizioni_snapshot import OrdineProntoSpedizione


CHIUSO = "Chiuso"


def _quantita_e_lotti(sess: Session, chiavi: list[tuple[int, int]]) -> tuple[dict, dict]:
    """Consuntivi effettivi e numeri di lotto, senza dedurre QtaProdotta dal pianificato.

    Gli eventuali pezzi QtaDaStock dei piani TRAD sono gia' stati prelevati
    dalla giacenza e sono pronti insieme ai nuovi pezzi effettivamente prodotti.
    """
    totali: dict[tuple[int, int], int] = defaultdict(int)
    lotti: dict[tuple[int, int], set[str]] = defaultdict(set)
    for offset in range(0, len(chiavi), 200):
        blocco = chiavi[offset:offset + 200]
        if not blocco:
            continue
        condizioni = tuple_(ConsuntivoProduzioneOrdine.IdDocumento,
                            ConsuntivoProduzioneOrdine.IdRigaDoc).in_(blocco)
        for documento, riga, prodotti, da_stock, lotto in sess.execute(
            select(ConsuntivoProduzioneOrdine.IdDocumento,
                   ConsuntivoProduzioneOrdine.IdRigaDoc,
                   ConsuntivoProduzioneOrdine.QtaProdotta,
                   PianificazioneOrdini.QtaDaStock,
                   ConsuntivoProduzioneOrdine.LottoSnapshot)
            .join(PianificazioneOrdini, and_(
                ConsuntivoProduzioneOrdine.IdPiano == PianificazioneOrdini.IdPianificazione,
                ConsuntivoProduzioneOrdine.IdDocumento == PianificazioneOrdini.IdDocumento,
                ConsuntivoProduzioneOrdine.IdRigaDoc == PianificazioneOrdini.IdRiga))
            .join(PianificazioneProduzione,
                  PianificazioneProduzione.IdPianificazione == PianificazioneOrdini.IdPianificazione)
            .where(ConsuntivoProduzioneOrdine.TipoPiano == "TRAD", condizioni,
                   PianificazioneOrdini.Stato == CHIUSO,
                   PianificazioneProduzione.StatoRigaOrdine == CHIUSO)
        ):
            key = (documento, riga)
            totali[key] += int(prodotti or 0) + int(da_stock or 0)
            if lotto:
                lotti[key].add(str(lotto))

        for documento, riga, prodotti, lotto in sess.execute(
            select(ConsuntivoProduzioneOrdine.IdDocumento,
                   ConsuntivoProduzioneOrdine.IdRigaDoc,
                   ConsuntivoProduzioneOrdine.QtaProdotta,
                   ConsuntivoProduzioneOrdine.LottoSnapshot)
            .join(BozzaPianificazioneMistaOrdine, and_(
                ConsuntivoProduzioneOrdine.IdPiano == BozzaPianificazioneMistaOrdine.IdBozza,
                ConsuntivoProduzioneOrdine.IdDocumento == BozzaPianificazioneMistaOrdine.IdDocumento,
                ConsuntivoProduzioneOrdine.IdRigaDoc == BozzaPianificazioneMistaOrdine.IdRigaDoc))
            .join(PianificazioneMistaAttiva,
                  PianificazioneMistaAttiva.IdBozza == BozzaPianificazioneMistaOrdine.IdBozza)
            .where(ConsuntivoProduzioneOrdine.TipoPiano == "MISTA", condizioni,
                   PianificazioneMistaAttiva.Stato == CHIUSO)
        ):
            key = (documento, riga)
            totali[key] += int(prodotti or 0)
            if lotto:
                lotti[key].add(str(lotto))

        # I lotti gia' registrati sono comunque mostrati anche se la chiusura
        # e' precedente all'introduzione del consuntivo fisico (qta = 0).
        for doc, riga, lotto in sess.execute(
            select(PianificazioneOrdini.IdDocumento,
                   PianificazioneOrdini.IdRiga,
                   LottoPianificazione.Lotto)
            .join(PianificazioneProduzione,
                  PianificazioneProduzione.IdPianificazione == PianificazioneOrdini.IdPianificazione)
            .join(LottoPianificazione, and_(
                LottoPianificazione.TipoOwner == "TRAD",
                LottoPianificazione.IdOwner == PianificazioneOrdini.IdPianificazione,
                LottoPianificazione.Corrente.is_(True)))
            .where(tuple_(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga).in_(blocco),
                   PianificazioneOrdini.Stato == CHIUSO,
                   PianificazioneProduzione.StatoRigaOrdine == CHIUSO)
        ):
            lotti[(doc, riga)].add(str(lotto))
        for doc, riga, lotto in sess.execute(
            select(BozzaPianificazioneMistaOrdine.IdDocumento,
                   BozzaPianificazioneMistaOrdine.IdRigaDoc,
                   LottoPianificazione.Lotto)
            .join(PianificazioneMistaAttiva,
                  PianificazioneMistaAttiva.IdBozza == BozzaPianificazioneMistaOrdine.IdBozza)
            .join(LottoPianificazione, and_(
                LottoPianificazione.TipoOwner == "MISTA",
                LottoPianificazione.IdOwner == BozzaPianificazioneMistaOrdine.IdBozza,
                LottoPianificazione.Corrente.is_(True)))
            .where(tuple_(BozzaPianificazioneMistaOrdine.IdDocumento,
                          BozzaPianificazioneMistaOrdine.IdRigaDoc).in_(blocco),
                   PianificazioneMistaAttiva.Stato == CHIUSO)
        ):
            lotti[(doc, riga)].add(str(lotto))

    return dict(totali), {key: sorted(valori) for key, valori in lotti.items()}


def aggiorna_snapshot_spedizioni(
    sess: Session, sync_time: datetime, chiavi_erp: set[tuple[int, int]]
) -> dict[str, int]:
    """Materializza i CHIUSO prima della cancellazione di ordini ERP obsoleti.

    Include i record che sono *ancora* in SQLite ma non sono piu' nel feed:
    questa e' l'ultima occasione per copiarli nel registro logistico.
    """
    record = sess.execute(
        select(OrdiniCliente, StatoRigaOrdine.Stato, AnagraficaClienti.RagioneSociale)
        .outerjoin(StatoRigaOrdine, and_(
            StatoRigaOrdine.IdDocumento == OrdiniCliente.IdDocumento,
            StatoRigaOrdine.IdRigaDoc == OrdiniCliente.IdRigaDoc))
        .outerjoin(AnagraficaClienti, AnagraficaClienti.CodCliFor == OrdiniCliente.CodCliFor)
    ).all()
    chiuse = [(o, cliente) for o, stato, cliente in record if stato == CHIUSO]
    chiavi = [(o.IdDocumento, o.IdRigaDoc) for o, _ in chiuse]
    quantita, lotti = _quantita_e_lotti(sess, chiavi)
    adesso = datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)

    # Una riga chiusa e poi riaperta, ma presente nel feed, non deve essere
    # nuovamente spedibile soltanto perche' ne esiste un vecchio snapshot.
    chiavi_riaperte = [(o.IdDocumento, o.IdRigaDoc) for o, stato, _ in record
                      if stato != CHIUSO and (o.IdDocumento, o.IdRigaDoc) in chiavi_erp]
    for indice in range(0, len(chiavi_riaperte), 200):
        blocco = chiavi_riaperte[indice:indice + 200]
        for snap in sess.scalars(
            select(OrdineProntoSpedizione).where(
                tuple_(OrdineProntoSpedizione.IdDocumento,
                       OrdineProntoSpedizione.IdRigaDoc).in_(blocco))
        ):
            snap.ConfermatoChiuso = False
            snap.PresenteERP = True
            snap.AggiornatoIl = adesso

    nuove = 0
    for ordine, cliente in chiuse:
        chiave = (ordine.IdDocumento, ordine.IdRigaDoc)
        presente = chiave in chiavi_erp
        snap = sess.get(OrdineProntoSpedizione, chiave)
        if snap is None:
            snap = OrdineProntoSpedizione(IdDocumento=chiave[0], IdRigaDoc=chiave[1])
            sess.add(snap)
            nuove += 1
        snap.CodCliFor = ordine.CodCliFor
        snap.ClienteSnapshot = (cliente or str(ordine.CodCliFor))[:250]
        snap.CodArt = str(ordine.CodArt)
        snap.DesArt = (ordine.DesArt or ordine.CodArt or "")[:500]
        snap.DataConsegna = ordine.DataConsegna
        snap.QtaSaldoERPSnapshot = max(0, int(ordine.QTA_SALDO_DOC or 0))
        snap.QtaAssegnataSpedizione = int(quantita.get(chiave, 0))
        snap.LottiSnapshot = ", ".join(lotti.get(chiave, [])) or None
        snap.PresenteERP = presente
        snap.ConfermatoChiuso = True
        snap.AggiornatoIl = adesso
    sess.flush()
    return {"chiuse": len(chiuse), "nuove": nuove,
            "senza_consuntivo": sum(quantita.get(k, 0) <= 0 for k in chiavi)}
