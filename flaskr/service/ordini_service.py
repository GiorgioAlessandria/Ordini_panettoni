from typing import Any

from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import OrdiniCliente, StatoRigaOrdine

date_format = "%d-%m-%Y"

def dati_tabella_produzione(stato: str) -> list[dict[str, Any]]:
    stmt = (
        db.select(OrdiniCliente)
        .join(OrdiniCliente.stato)
        .options(
            joinedload(OrdiniCliente.Cliente),
            joinedload(OrdiniCliente.articolo),
            joinedload(OrdiniCliente.stato),
        )
        .where(StatoRigaOrdine.Stato == stato)
    )
    ordini_cliente_grezzi = db.session.execute(stmt).scalars().all()
    ordini_cliente_assegnati = [
        {
            "IdDocumento": ordine.IdDocumento,
            "IdRigaDoc": ordine.IdRigaDoc,
            "DataRegistrazione": ordine.DataRegistrazione,
            "NumRegistraz": ordine.NumRegistraz,
            "Cliente": ordine.Cliente.RagioneSociale,
            "CodArt": ordine.CodArt,
            "DesArt": ordine.DesArt,
            "DataConsegna": ordine.DataConsegna.strftime(date_format),
            "QTA_ORD": ordine.QTA_ORD,
            "Prezzatura": ordine.articolo.Prezzatura,
            "Glassatura": "Si" if ordine.articolo.Glassatura == 1 else "No",
            "Stato": ordine.stato.Stato,
        }
        for ordine in ordini_cliente_grezzi
    ]
    return ordini_cliente_assegnati
