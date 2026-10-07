from typing import Any, Literal

from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaForni,
    CapacitaForno,
    OrdiniCliente,
    RicettaFasi,
    StatoRigaOrdine,
    TipLavorazioniAnagForni,
    TipologiaRicette,
    TipologieLavorazione,
    TipRicetteAnagArticoli,
)

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


def trova_capacita_forno(
    ordine: OrdiniCliente, tipologia_panettone: Literal["TO", "MI"]
) -> int | str:
    # 1
    stmt_ricetta_articolo = db.select(TipRicetteAnagArticoli).where(
        TipRicetteAnagArticoli.CodArt == ordine.CodArt
    )
    ricette_articoli_selezionati = db.session.scalars(stmt_ricetta_articolo).one_or_none()

    if ricette_articoli_selezionati is None:
        pass

    # 2
    stmt_tipologia_ricetta = db.select(TipologiaRicette).where(
        TipologiaRicette.IdRicetta == ricette_articoli_selezionati.IdRicetta
    )
    ricetta_selezionata = db.session.scalars(stmt_tipologia_ricetta).one_or_none()

    if ricetta_selezionata is None:
        pass

    # 3
    stmt_ricette_fasi = db.select(RicettaFasi).where(
        RicettaFasi.IdRicetta == ricetta_selezionata.IdRicetta
    )
    fasi_selezionate = db.session.scalars(stmt_ricette_fasi).all()

    # 4
    for fase in fasi_selezionate:
        lavorazione = fase.lavorazione
        if lavorazione.TipologiaLavorazione == "Forno":
            # 5
            stmt_lavorazioni_forno = db.select(TipLavorazioniAnagForni).where(
                TipLavorazioniAnagForni.IdLavorazione == lavorazione.IdLavorazione
            )
            lavorazioni_forno = db.session.scalars(stmt_lavorazioni_forno).all()
            # 6
            id_forni = [associazione.IdForno for associazione in lavorazioni_forno]
            # 7
            stmt_capacita_forno = db.select(CapacitaForno).where(
                CapacitaForno.IdForno.in_(id_forni),
                CapacitaForno.DimensionePanettone == ordine.articolo.Prezzatura,
                CapacitaForno.TipologiaPanettone == tipologia_panettone,
            )
            capacita_forno = db.session.scalars(stmt_capacita_forno).one_or_none()

            return capacita_forno.Capienza
        else:
            pass
    return "Capacità forno non trovata"


def calcolo_infornate(capacita_panettone: int, quantita_panettoni: int):
    numero_infornate = quantita_panettoni // capacita_panettone
    panettoni_restanti = quantita_panettoni % capacita_panettone
    if numero_infornate == 0 and panettoni_restanti:
        numero_infornate = 1
        panettoni_restanti = 0
    return numero_infornate, panettoni_restanti


def determina_tipologia_panettone(glassatura: Any) -> Literal["TO", "MI"]:
    glassatura = int(glassatura)
    if glassatura == 1:
        return "TO"
    return "MI"
