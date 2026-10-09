from typing import Any, Iterator, Literal

from flask import abort, render_template
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaForni,
    CapacitaForno,
    Coefficienti,
    OrdiniCliente,
    RicettaFasi,
    StatoRigaOrdine,
    TipLavorazioniAnagForni,
    TipologiaRicette,
    TipologieLavorazione,
    TipRicetteAnagArticoli,
)

date_format = "%d-%m-%Y"


class ErroreCalcoloRiepilogo(Exception):
    pass


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


def determina_tipologia_panettone(glassatura: Any) -> Literal["TO", "MI"]:
    glassatura = int(glassatura)
    if glassatura == 1:
        return "TO"
    return "MI"


def trova_capacita_forno(
    ordine: OrdiniCliente, tipologia_panettone: Literal["TO", "MI"]
) -> int | str:
    # 1
    errore = None
    stmt_ricetta_articolo = db.select(TipRicetteAnagArticoli).where(
        TipRicetteAnagArticoli.CodArt == ordine.CodArt
    )
    ricette_articoli_selezionati: TipRicetteAnagArticoli | None = db.session.scalars(
        stmt_ricetta_articolo
    ).one_or_none()

    if ricette_articoli_selezionati is None:
        errore = "Nessun articolo selezionato"
        return errore

    # 2
    stmt_tipologia_ricetta = db.select(TipologiaRicette).where(
        TipologiaRicette.IdRicetta == ricette_articoli_selezionati.IdRicetta
    )
    ricetta_selezionata = db.session.scalars(stmt_tipologia_ricetta).one_or_none()

    if ricetta_selezionata is None:
        errore = "Nessuna ricetta selezionata"
        return errore

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
            if not capacita_forno:
                errore = "Non è stato possibile trovare la capacità del forno"
                return errore
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


def recupera_ordini_selezionati(identificativi: list[str]) -> list[OrdiniCliente]:
    if not identificativi:
        raise ErroreCalcoloRiepilogo("Nessun ordine selezionato")
    list_ordini: list[OrdiniCliente] = []
    for ordine in identificativi:
        IdDocumento, IdRiga = ordine.split("-")
        IdDocumento, IdRiga = int(IdDocumento), int(IdRiga)
        stmt_ordine_selezionato = db.select(OrdiniCliente).where(
            OrdiniCliente.IdDocumento == IdDocumento, OrdiniCliente.IdRigaDoc == IdRiga
        )
        ordine_selezionato: OrdiniCliente | None = db.session.scalars(
            stmt_ordine_selezionato
        ).one_or_none()
        if not ordine_selezionato:
            raise ErroreCalcoloRiepilogo(
                f"Non è stato possibile selezionare l'ordine corrente {IdDocumento, IdRiga}"
            )
        list_ordini.append(ordine_selezionato)
    return list_ordini


def calcolo_riepilogo_ordini(list_ordini_selezionati: list[OrdiniCliente]):
    codici_articolo_complessivo = {ordine.CodArt for ordine in list_ordini_selezionati}
    prezzature_complessivo = {ordine.articolo.Prezzatura for ordine in list_ordini_selezionati}
    valori_glassatura: set[int] = {ordine.articolo.Glassatura for ordine in list_ordini_selezionati}
    if len(valori_glassatura) > 1:
        raise ErroreCalcoloRiepilogo(
            "Sono stati selezionati ordini di formato eterogeneo (Torino - Milano)"
        )
    glassatura_complessivo: int = next(iter(valori_glassatura))

    if len(codici_articolo_complessivo) != 1:
        raise ErroreCalcoloRiepilogo("Il numero di codici selezionati è eterogeneo")
    if len(prezzature_complessivo) > 1:
        raise ErroreCalcoloRiepilogo("Il numero di prezzature selezionate è eterogeneo")

    tipologia_panettone = determina_tipologia_panettone(glassatura_complessivo)
    numero_ordini = len(list_ordini_selezionati)
    qta_panettoni = 0
    peso_impasto = 0

    stmt_coefficiente_glassatura = db.select(Coefficienti).where(
        Coefficienti.Coefficiente == "PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA"
    )
    coefficiente_glassatura = db.session.execute(stmt_coefficiente_glassatura).scalar_one_or_none()
    if coefficiente_glassatura is None:
        raise ErroreCalcoloRiepilogo("Il coefficiente di glassatura non è stato trovato")

    stmt_coefficiente_peso_totale = db.select(Coefficienti).where(
        Coefficienti.Coefficiente == "PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO"
    )
    coefficiente_peso_totale = db.session.execute(
        stmt_coefficiente_peso_totale
    ).scalar_one_or_none()
    if coefficiente_peso_totale is None:
        raise ErroreCalcoloRiepilogo("Il coefficiente di peso totale non è stato trovato")

    for ordine in list_ordini_selezionati:
        qta_panettoni += ordine.QTA_ORD
        peso_impasto += (
            (ordine.QTA_ORD * ordine.articolo.Prezzatura)
            if ordine.articolo.Glassatura == 1
            else ((ordine.QTA_ORD * ordine.articolo.Prezzatura) * coefficiente_glassatura.Valore)
        )

    peso_impasto = peso_impasto * coefficiente_peso_totale.Valore
    # qty_impasto_panettoni = peso_impasto / list(prezzature_complessivo)[0]
    peso_impasto /= 1000
    capacita_forno = trova_capacita_forno(list_ordini_selezionati[0], tipologia_panettone)
    if type(capacita_forno) is int:
        numero_cotte, panettoni_avanzo = calcolo_infornate(capacita_forno, qta_panettoni)
    else:
        raise ErroreCalcoloRiepilogo(capacita_forno)
    return {
        "numero_ordini": numero_ordini,
        "qta_panettoni": qta_panettoni,
        "peso_impasto": peso_impasto,
        "numero_cotte": numero_cotte,
        "panettoni_avanzo": panettoni_avanzo,
        "tipologia_panettone": tipologia_panettone,
    }
