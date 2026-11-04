"""Route logistiche. Importate da routes_modules/ordini.py sul blueprint esistente."""
from datetime import date, datetime

from flask import abort, flash, g, redirect, render_template, request, url_for

from flaskr.accessi import ruolo_attuale, verifica_csrf
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.spedizioni_service import (
    ErroreSpedizione, annulla_preparazione, conferma_partenza,
    elenco_spedizioni, ordini_disponibili_spedizione, prepara_spedizione,
)


def _accesso_spedizioni():
    if ruolo_attuale() not in ("admin", "utente_spedizioni"):
        abort(403)


@bp_ordini_cliente.get("/spedizioni/")
@login_required
def spedizioni_index():
    _accesso_spedizioni()
    preparate, spedite = elenco_spedizioni()
    return render_template(
        "ordini/spedizioni.j2",
        ordini_disponibili=ordini_disponibili_spedizione(),
        preparate=preparate,
        spedite=spedite,
    )


@bp_ordini_cliente.post("/spedizioni/prepara/")
@login_required
def spedizioni_prepara():
    _accesso_spedizioni()
    verifica_csrf()
    try:
        data_raw = request.form.get("data_prevista", "").strip()
        data_prevista = date.fromisoformat(data_raw) if data_raw else None
        id_spedizione = prepara_spedizione(
            request.form.getlist("ordini"), data_prevista,
            request.form.get("note", ""), g.user.username,
        )
        flash(f"Spedizione #{id_spedizione} preparata. La merce non risulta ancora partita.", "success")
        return redirect(url_for("ordini_cliente.spedizioni_index") + "#preparate")
    except (ErroreSpedizione, ValueError) as err:
        flash(str(err) if isinstance(err, ErroreSpedizione) else "Data prevista non valida", "danger")
        return redirect(url_for("ordini_cliente.spedizioni_index") + "#da-spedire")


@bp_ordini_cliente.post("/spedizioni/<int:id_spedizione>/partenza/")
@login_required
def spedizioni_partenza(id_spedizione: int):
    _accesso_spedizioni()
    verifica_csrf()
    try:
        partenza_raw = request.form.get("data_partenza", "").strip()
        partenza = datetime.fromisoformat(partenza_raw) if partenza_raw else datetime.now()
        conferma_partenza(id_spedizione, partenza, g.user.username)
        flash(f"Partenza della spedizione #{id_spedizione} registrata: ordini rimossi dalla schermata Vendite.", "success")
        return redirect(url_for("ordini_cliente.spedizioni_index") + "#storico")
    except (ErroreSpedizione, ValueError) as err:
        flash(str(err) if isinstance(err, ErroreSpedizione) else "Data e ora di partenza non valide", "danger")
        return redirect(url_for("ordini_cliente.spedizioni_index") + "#preparate")


@bp_ordini_cliente.post("/spedizioni/<int:id_spedizione>/annulla/")
@login_required
def spedizioni_annulla(id_spedizione: int):
    _accesso_spedizioni()
    verifica_csrf()
    try:
        annulla_preparazione(id_spedizione, g.user.username)
        flash(f"Preparazione #{id_spedizione} annullata: righe nuovamente disponibili.", "success")
    except ErroreSpedizione as err:
        flash(str(err), "danger")
    return redirect(url_for("ordini_cliente.spedizioni_index") + "#preparate")
