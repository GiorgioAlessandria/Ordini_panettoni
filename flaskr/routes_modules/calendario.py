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
from flaskr.routes_production import bp_ordini_cliente


@bp_ordini_cliente.route("/calendario")
def ordini_calendario():
    return render_template("ordini/calendario.j2")


@bp_ordini_cliente.route("/calendario/eventi")
def ordini_calendario_inseriti():
    return render_template("ordini/_partials/_partial_eventi_calendario.j2")

