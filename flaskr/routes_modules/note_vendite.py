"""Note per riga d'ordine: produzione sola lettura, vendite scrittura."""
from datetime import datetime, timezone
from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from flaskr.extensions import db
from flaskr.models.model_ordini import OrdiniCliente
from flaskr.models.model_accessi import NotaVenditeOrdine
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.accessi import solo_ruoli, verifica_csrf, ruolo_attuale


def _ordine_esistente(id_documento, id_riga):
    ordine = db.session.get(OrdiniCliente, (id_documento, id_riga))
    if ordine is None:
        abort(404, description="Ordine non presente nel database ERP sincronizzato.")
    return ordine


@bp_ordini_cliente.get("/ordine/<int:id_documento>/<int:id_riga>/note", endpoint="note_vendite_ordine")
@solo_ruoli("admin", "utente_produzione", "utente_vendite")
def note_vendite_ordine(id_documento, id_riga):
    ordine = _ordine_esistente(id_documento, id_riga)
    note = db.session.scalars(select(NotaVenditeOrdine).where(
        NotaVenditeOrdine.IdDocumento == id_documento,
        NotaVenditeOrdine.IdRigaDoc == id_riga,
    ).order_by(NotaVenditeOrdine.CreatoIl.desc(), NotaVenditeOrdine.Id.desc())).all()
    return render_template("ordini/note_vendite.j2", ordine=ordine, note=note)


@bp_ordini_cliente.post("/ordine/<int:id_documento>/<int:id_riga>/note", endpoint="aggiungi_nota_vendite")
@solo_ruoli("admin", "utente_vendite")
def aggiungi_nota_vendite(id_documento, id_riga):
    verifica_csrf()
    _ordine_esistente(id_documento, id_riga)
    testo = request.form.get("testo", "").strip()
    if not (1 <= len(testo) <= 2000):
        abort(400, description="Il commento deve contenere da 1 a 2000 caratteri.")
    db.session.add(NotaVenditeOrdine(
        IdDocumento=id_documento, IdRigaDoc=id_riga,
        AutoreId=g.user.id, AutoreNome=g.user.username, Testo=testo))
    db.session.commit()
    flash("Commento salvato.", "success")
    return redirect(url_for("ordini_cliente.note_vendite_ordine", id_documento=id_documento, id_riga=id_riga))


@bp_ordini_cliente.post("/ordine/note/<int:nota_id>/modifica", endpoint="modifica_nota_vendite")
@solo_ruoli("admin", "utente_vendite")
def modifica_nota_vendite(nota_id):
    verifica_csrf()
    nota = db.session.get(NotaVenditeOrdine, nota_id)
    if nota is None:
        abort(404)
    if ruolo_attuale() != "admin" and nota.AutoreId != g.user.id:
        abort(403, description="Puoi modificare soltanto i tuoi commenti.")
    testo = request.form.get("testo", "").strip()
    if not (1 <= len(testo) <= 2000):
        abort(400)
    nota.Testo = testo
    nota.ModificatoIl = datetime.now(timezone.utc)
    db.session.commit()
    flash("Commento modificato.", "success")
    return redirect(url_for("ordini_cliente.note_vendite_ordine",
                            id_documento=nota.IdDocumento, id_riga=nota.IdRigaDoc))
