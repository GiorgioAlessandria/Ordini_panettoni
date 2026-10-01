from flask import render_template

from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente


@bp_ordini_cliente.route("/index/")
@login_required
def ordini_cliente():
    return render_template("ordini/ordini.j2")
