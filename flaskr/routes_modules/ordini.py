from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import OrdiniCliente, StatoRigaOrdine
from flaskr.routes_auth import login_required

bp_ordini_cliente = Blueprint("ordini_cliente", __name__, url_prefix="/")

@login_required
@bp_ordini_cliente.route("/index/")
def ordini_cliente():
    return render_template("ordini/ordini.j2")

@login_required
@bp_ordini_cliente.route("/index/")
def ordini_cliente():
    return render_template("ordini/ordini.j2")


@bp_ordini_cliente.route("/tabella_ordini_da_produrre")
def tabella_ordini_cliente_da_produrre():
    stmt = db.select(OrdiniCliente).options(
        joinedload(OrdiniCliente.Cliente),
        joinedload(OrdiniCliente.articolo),
        joinedload(OrdiniCliente.stato),
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
            "DataConsegna": ordine.DataConsegna,
            "QTA_ORD": ordine.QTA_ORD,
            "Prezzatura": ordine.articolo.Prezzatura,
            "Glassatura": "Si" if ordine.articolo.Glassatura == True else "No",
            "Stato": ordine.stato.Stato,
        }
        for ordine in ordini_cliente_grezzi
        if ordine.stato.Stato == "Nuovo"
    ]
    return render_template(
        "ordini/_partials/_partial_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
    )


@bp_ordini_cliente.route("/tabella_ordini_in_produzione")
def tabella_ordini_cliente_in_produzione():
    stmt = db.select(OrdiniCliente).options(
        joinedload(OrdiniCliente.Cliente),
        joinedload(OrdiniCliente.articolo),
        joinedload(OrdiniCliente.stato),
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
            "DataConsegna": ordine.DataConsegna,
            "QTA_ORD": ordine.QTA_ORD,
            "Prezzatura": ordine.articolo.Prezzatura,
            "Glassatura": "Si" if ordine.articolo.Glassatura == True else "No",
            "Stato": ordine.stato.Stato,
        }
        for ordine in ordini_cliente_grezzi
        if ordine.stato.Stato == "In produzione"
    ]
    return render_template(
        "ordini/_partials/_partial_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
    )
