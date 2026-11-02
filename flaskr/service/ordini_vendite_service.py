"""Righe unificate della vista Vendite: una per ordine ERP, mai per piano."""
from collections import defaultdict

from sqlalchemy import tuple_
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    OrdiniCliente, PianificazioneOrdini, PianificazioneProduzione,
)
from flaskr.service.scadenze_giornaliere_service import eseguito


def _stato_visivo(ordine, quantita_presa: int, quantita_chiusa: int) -> tuple[str, str]:
    """Stati allineati alle scadenze, con distinzione aggiuntiva del parziale."""
    if eseguito(ordine, quantita_chiusa):
        return 'Eseguito', 'eseguito'
    if quantita_presa <= 0:
        return 'Nuovo', 'nuovo'
    if quantita_presa < max(0, int(ordine.QTA_SALDO_DOC or 0)):
        return 'Preso in carico (parziale)', 'parziale'
    return 'Preso in carico', 'preso'


def righe_ordini_vendite() -> list[dict]:
    """Tutti gli ordini aperti, incluse righe assegnate solo in parte o in più piani.

    Non usa StatoRigaOrdine.Stato: come per le scadenze del calendario,
    l'effettiva presa in carico deriva dalle associazioni alle pianificazioni.
    """
    ordini = db.session.scalars(
        db.select(OrdiniCliente)
        .options(joinedload(OrdiniCliente.Cliente), joinedload(OrdiniCliente.articolo))
        .order_by(OrdiniCliente.DataConsegna, OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc)
    ).all()
    if not ordini:
        return []

    chiavi = [(o.IdDocumento, o.IdRigaDoc) for o in ordini]
    prese = defaultdict(int)
    chiuse = defaultdict(int)
    for start in range(0, len(chiavi), 200):
        blocco = chiavi[start:start + 200]
        stmt = (
            db.select(
                PianificazioneOrdini.IdDocumento,
                PianificazioneOrdini.IdRiga,
                PianificazioneOrdini.QtaPianificata,
                PianificazioneOrdini.Stato,
            )
            .join(
                PianificazioneProduzione,
                PianificazioneProduzione.IdPianificazione == PianificazioneOrdini.IdPianificazione,
            )
            .where(
                tuple_(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga).in_(blocco),
                PianificazioneOrdini.Stato.in_(('In produzione', 'Chiuso')),
                PianificazioneProduzione.StatoRigaOrdine != 'Annullato',
            )
        )
        for documento, riga, qta, stato in db.session.execute(stmt):
            chiave = (documento, riga)
            prese[chiave] += int(qta or 0)
            if stato == 'Chiuso':
                chiuse[chiave] += int(qta or 0)

    righe = []
    for ordine in ordini:
        chiave = (ordine.IdDocumento, ordine.IdRigaDoc)
        presa, chiusa = prese[chiave], chiuse[chiave]
        stato, classe_stato = _stato_visivo(ordine, presa, chiusa)
        data = ordine.DataConsegna
        righe.append({
            'IdDocumento': ordine.IdDocumento,
            'IdRigaDoc': ordine.IdRigaDoc,
            'Cliente': ordine.Cliente.RagioneSociale if ordine.Cliente else 'Cliente non disponibile',
            'CodArt': ordine.CodArt,
            'DesArt': ordine.DesArt,
            'DataConsegna': data.strftime('%d-%m-%Y') if data else '',
            'DataConsegnaISO': data.date().isoformat() if data else '',
            'QTA_ORD': int(ordine.QTA_ORD or 0),
            'QTA_Saldo': int(ordine.QTA_SALDO_DOC or 0),
            'QtaPresa': presa,
            'QtaChiusa': chiusa,
            'QtaDaPianificare': max(0, int(ordine.QTA_SALDO_DOC or 0) - presa),
            'Prezzatura': ordine.articolo.Prezzatura if ordine.articolo else None,
            'Glassatura': 'Si' if ordine.articolo and ordine.articolo.Glassatura == 1 else 'No',
            'Stato': stato,
            'ClasseStato': classe_stato,
        })
    return righe
