from flask import redirect, render_template, url_for
from flaskr.accessi import ruolo_attuale

from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente


@bp_ordini_cliente.route("/index/")
@login_required
def ordini_cliente():
    if ruolo_attuale() == "utente_vendite":
        return render_template("ordini/ordini_vendite.j2")
    return render_template("ordini/ordini.j2")


@bp_ordini_cliente.get("/tabella_ordini_vendite")
@login_required
def tabella_ordini_vendite():
    """Compatibilita' con le vecchie pagine in cache: usa la route esistente."""
    return redirect(url_for("ordini_cliente.tabella_ordini_cliente_da_produrre"), code=302)
