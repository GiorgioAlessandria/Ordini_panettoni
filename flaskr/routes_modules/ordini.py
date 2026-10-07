from flask import abort, render_template, request
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import Coefficienti, OrdiniCliente
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.ordini_service import (
    calcolo_infornate,
    dati_tabella_produzione,
    determina_tipologia_panettone,
    trova_capacita_forno,
)


@bp_ordini_cliente.route("/tabella_ordini_da_produrre")
@login_required
def tabella_ordini_cliente_da_produrre():
    ordini_cliente_assegnati = dati_tabella_produzione(stato="Nuovo")
    return render_template(
        "ordini/_partials/_partial_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
    )


@bp_ordini_cliente.route("/tabella_ordini_in_produzione")
@login_required
def tabella_ordini_cliente_in_produzione():
    ordini_cliente_assegnati = dati_tabella_produzione(stato="In produzione")
    return render_template(
        "ordini/_partials/_partial_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
    )


@bp_ordini_cliente.route("/riepilogo_selezione", methods=["POST"])
@login_required
def riepilogo_selezione():
    list_ordini_selezionati = request.form.getlist("ordini_selezionati")
    if not list_ordini_selezionati:
        errore = "Nessun ordine selezionato"
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=errore)
    list_oggetti_ordini_selezionati = []
    for ordine in list_ordini_selezionati:
        IdDocumento, IdRigaDoc = ordine.split("-")
        IdDocumento = int(IdDocumento)
        IdRigaDoc = int(IdRigaDoc)
        stmt_ordine = (
            db.select(OrdiniCliente)
            .options(joinedload(OrdiniCliente.articolo))
            .where(
                OrdiniCliente.IdDocumento == IdDocumento,
                OrdiniCliente.IdRigaDoc == IdRigaDoc,
            )
        )
        dati_ordine = db.session.execute(stmt_ordine).scalar_one_or_none()
        if dati_ordine is None:
            abort(404)

        list_oggetti_ordini_selezionati.append(dati_ordine)
    codici_articolo_complessivo = {ordine.CodArt for ordine in list_oggetti_ordini_selezionati}
    prezzature_complessivo = {
        ordine.articolo.Prezzatura for ordine in list_oggetti_ordini_selezionati
    }
    glassatura_complessivo: set[int] = {
        ordine.articolo.Glassatura for ordine in list_oggetti_ordini_selezionati
    }
    if len(glassatura_complessivo) > 1:
        errore = "Sono stati selezionati ordini di formato eterogeneo (Torino - Milano)"
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=errore)
    iteratore_glassatura = iter(glassatura_complessivo)
    glassatura_complessivo = next(iteratore_glassatura)

    if len(codici_articolo_complessivo) > 1:
        errore = "Il numero di codici selezionati è eterogeneo"
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=errore)
    if len(prezzature_complessivo) > 1:
        errore = "Il numero di prezzature selezionate è eterogeneo"
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=errore)

    tipologia_panettone = determina_tipologia_panettone(glassatura_complessivo)
    numero_ordini = len(list_ordini_selezionati)
    qta_panettoni = 0
    peso_impasto = 0
    numero_cotte = 0
    panettoni_avanzo = 0

    stmt_coefficiente_glassatura = db.select(Coefficienti).where(
        Coefficienti.Coefficiente == "PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA"
    )
    coefficiente_glassatura = db.session.execute(stmt_coefficiente_glassatura).scalar_one_or_none()
    if coefficiente_glassatura is None:
        errore = "Il coefficiente di glassatura non è stato trovato"
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=errore)

    stmt_coefficiente_peso_totale = db.select(Coefficienti).where(
        Coefficienti.Coefficiente == "PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO"
    )
    coefficiente_peso_totale = db.session.execute(
        stmt_coefficiente_peso_totale
    ).scalar_one_or_none()
    if coefficiente_peso_totale is None:
        errore = "Il coefficiente di peso totale non è stato trovato"
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=errore)

    for ordine in list_oggetti_ordini_selezionati:
        qta_panettoni += ordine.QTA_ORD
        peso_impasto += (
            (ordine.QTA_ORD * ordine.articolo.Prezzatura)
            if ordine.articolo.Glassatura == 1
            else ((ordine.QTA_ORD * ordine.articolo.Prezzatura) * coefficiente_glassatura.Valore)
        )
    capacita_forno = trova_capacita_forno(list_oggetti_ordini_selezionati[0], tipologia_panettone)
    if capacita_forno.is_integer():
        numero_cotte, panettoni_avanzo = calcolo_infornate(capacita_forno, qta_panettoni)

    peso_impasto = peso_impasto * coefficiente_peso_totale.Valore
    peso_impasto /= 1000
    peso_impasto = f"{peso_impasto:.2f}"

    return render_template(
        "ordini/_partials/_partial_riepilogo_ordini.j2",
        errore=None,
        numero_ordini=numero_ordini,
        qta_panettoni=qta_panettoni,
        peso_impasto=peso_impasto,
        numero_cotte=numero_cotte,
        panettoni_avanzo=panettoni_avanzo,
    )


@bp_ordini_cliente.route("/dettaglio/<int:IdDocumento>/<int:IdRigaDoc>")
@login_required
def dettaglio_ordine(IdDocumento: int, IdRigaDoc: int):
    stmt_ordine = (
        db.select(OrdiniCliente)
        .options(
            joinedload(OrdiniCliente.Cliente),
            joinedload(OrdiniCliente.articolo),
            joinedload(OrdiniCliente.stato),
        )
        .where(
            OrdiniCliente.IdDocumento == IdDocumento,
            OrdiniCliente.IdRigaDoc == IdRigaDoc,
        )
    )
    ordine = db.session.execute(stmt_ordine).scalar_one_or_none()
    if not ordine:
        errore = "Ordine selezionato non esistente a programma"
        render_template("ordini/_partials/_partial_dettaglio_ordine.j2", errore=errore)
    if ordine is None:
        abort(404)

    return render_template(
        "ordini/_partials/_partial_dettaglio_ordine.j2", ordine=ordine, errore=None
    )
