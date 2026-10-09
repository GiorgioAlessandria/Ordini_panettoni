from flask import abort, render_template, request
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import AnagraficaImpastatrici, OrdiniCliente
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.ordini_service import (
    ErroreCalcoloRiepilogo,
    calcolo_infornate,
    calcolo_riepilogo_ordini,
    dati_tabella_produzione,
    determina_tipologia_panettone,
    recupera_ordini_selezionati,
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
    list_ordini_selezionati: list[str] = request.form.getlist("ordini_selezionati")
    try:
        list_ordini_selezionati_obj = recupera_ordini_selezionati(list_ordini_selezionati)
        risultato: dict[str, str] = calcolo_riepilogo_ordini(list_ordini_selezionati_obj)
    except ErroreCalcoloRiepilogo as errore:
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=str(errore))
    return render_template(
        "ordini/_partials/_partial_riepilogo_ordini.j2",
        numero_ordini=risultato["numero_ordini"],
        qta_panettoni=risultato["qta_panettoni"],
        peso_impasto=risultato["peso_impasto"],
        numero_cotte=risultato["numero_cotte"],
        panettoni_avanzo=risultato["panettoni_avanzo"],
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


@bp_ordini_cliente.post("/pianificazione/anteprima")
@login_required
def anteprima_pianificazione():
    ordini_selezionati = request.form.getlist("ordini_selezionati")
    if not ordini_selezionati:
        return render_template(
            "ordini/_partials/_form_pianificazione.j2", error="Nessun ordine selezionato"
        )
    stmt_anagrafica_impastatrici = db.select(AnagraficaImpastatrici).order_by(
        AnagraficaImpastatrici.IdImpastatrice
    )
    impastatrici = db.session.scalars(stmt_anagrafica_impastatrici).all()
    return render_template(
        "ordini/_partials/_form_pianificazione.j2",
        impastatrici=impastatrici,
        ordini_selezionati=ordini_selezionati,
        error="",
    )


@bp_ordini_cliente.post("/pianificazione/conferma")
@login_required
def conferma_pianificazione():
    data_inizio_produzione = request.form.get("data_inizio_produzione")
    form_ordini_selezionati = request.form.getlist("ordini_selezionati")
    if not form_ordini_selezionati:
        error = "Nessun ordine selezionato"
        return render_template("ordini/_partials/_conferma_pianificazione.j2", error=error)
    list_ordini = []

    impastatrici_selezionate = request.form.getlist("impastatrici_selezionate")
    if not impastatrici_selezionate:
        error = "Nessun impastatrice selezionata"
        return render_template("ordini/_partials/_conferma_pianificazione.j2", error=error)
    list_impastatrici = []
    for impastatrice in impastatrici_selezionate:
        stmt_impastatrice_selezionata = db.select(AnagraficaImpastatrici).where(
            AnagraficaImpastatrici.IdImpastatrice == impastatrice
        )
        impastatrice_selezionata = db.session.scalars(stmt_impastatrice_selezionata).one_or_none()
        if not impastatrice_selezionata:
            error = f"Non è stato possibile selezionare l'impastatrice corrente {impastatrice}"
            return render_template("ordini/_partials/_conferma_pianificazione.j2", error=error)
        list_impastatrici.append(impastatrice_selezionata)
    try:
        list_ordini_selezionati = recupera_ordini_selezionati(form_ordini_selezionati)

        risultato = calcolo_riepilogo_ordini(list_ordini_selezionati)
    except ErroreCalcoloRiepilogo as errore:
        return render_template("ordini/_partials/_conferma_pianificazione.j2", errore=str(errore))
    risultato
    return render_template(
        "ordini/_partials/_conferma_pianificazione.j2",
        ordini_selezionati=list_ordini,
        impastatrici_selezionate=list_impastatrici,
        error="",
    )
