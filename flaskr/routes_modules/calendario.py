from datetime import datetime

from flask import abort, jsonify, render_template, request, url_for
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models import OrdiniCliente
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente


# eventi calendario
# {
# "id": "49794-1",
# "title": "Cliente ABC - PNT750G",
# "start": "2026-10-05",
# "end": "2026-10-06",
# "allDay": true
# }
@bp_ordini_cliente.route("/calendario")
@login_required
def ordini_calendario():
    return render_template("ordini/calendario.j2")


@bp_ordini_cliente.route("/calendario/dati_scadenza")
@login_required
def ordini_calendario_scadenze():
    data_inizio = datetime.fromisoformat(request.args["start"])
    data_fine = datetime.fromisoformat(request.args["end"])
    stmt = db.select(OrdiniCliente).options(
            joinedload(OrdiniCliente.Cliente)).where(
            OrdiniCliente.DataConsegna >= data_inizio,
            OrdiniCliente.DataConsegna < data_fine,
            )
    ordini_data_scadenza = db.session.execute(stmt).scalars().all()
    json_ordini_data_scadenza = [
        {
            "id": f"{ordine.IdDocumento}-{ordine.IdRigaDoc}",
            "title": ordine.Cliente.RagioneSociale,
            "start": ordine.DataConsegna.date().isoformat(),
            "allDay": True,
            "extendedProps": {
                "detailUrl": url_for(
                    "ordini_cliente.dettaglio_ordine",
                    IdDocumento=ordine.IdDocumento,
                    IdRigaDoc=ordine.IdRigaDoc,
                )
            },
        }
        for ordine in ordini_data_scadenza
    ]

    return jsonify(json_ordini_data_scadenza)

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
    if ordine is None:
        abort(404)

    return render_template("ordini/_partials/_partial_dettaglio_ordine.j2", ordine=ordine)