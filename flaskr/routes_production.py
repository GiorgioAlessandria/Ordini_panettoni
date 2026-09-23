from flask import (Blueprint, flash, g, redirect, render_template, request, session, url_for)
from flaskr.routes_auth import login_required
from flaskr.extensions import db
from flaskr.models.model_ordini import OrdiniCliente

bp_ordini_cliente = Blueprint('ordini_cliente', __name__, url_prefix = '/')

@login_required
@bp_ordini_cliente.route('/index/')
def ordini_cliente():
    return render_template("ordini/ordini.j2")

@bp_ordini_cliente.route('/tabella_ordini')
def tabella_ordini_cliente():
    ordini_cliente_grezzi = db.session.execute(db.select(OrdiniCliente)).scalars().all()
    ordini_cliente = [
        {
        "IdDocumento": ordine_cliente.IdDocumento,
        "IdRigaDoc": ordine_cliente.IdRigaDoc,
        "DataRegistrazione": ordine_cliente.DataRegistrazione,
        "NumRegistraz": ordine_cliente.NumRegistraz,
        "Cliente": ordine_cliente.cliente.RagioneSociale,
        "CodArt": ordine_cliente.CodArt,
        "DesArt": ordine_cliente.DesArt,
        "DataConsegna": ordine_cliente.DataConsegna,
        "QTA_ORD": ordine_cliente.QTA_ORD,
        "Prezzatura": [int(s) for s in ordine_cliente.CodArt.split() if s.isdigit()],
        "Glassa": "Si" if ordine_cliente.CodArt[-1] == "G" else "No",
            }
        for ordine_cliente in ordini_cliente_grezzi
    ]
    return render_template("ordini/_partials/_partial_tabella_ordini.j2", ordini_cliente=ordini_cliente)