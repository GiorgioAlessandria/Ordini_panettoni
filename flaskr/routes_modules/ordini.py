from flask import render_template

from flaskr.models import OrdiniCliente
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.ordini_service import dati_tabella_produzione


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

