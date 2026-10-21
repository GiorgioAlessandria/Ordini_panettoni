"""Note per riga d'ordine: produzione sola lettura, vendite scrittura."""
from datetime import datetime, timezone
from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select, tuple_
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




def _carica_note(id_documento, id_riga):
    return db.session.scalars(select(NotaVenditeOrdine).where(
        NotaVenditeOrdine.IdDocumento == id_documento,
        NotaVenditeOrdine.IdRigaDoc == id_riga,
    ).order_by(NotaVenditeOrdine.CreatoIl.desc(), NotaVenditeOrdine.Id.desc())).all()


def _note_per_righe(ordini_cliente, selezione_attiva):
    """Recupera in batch i commenti degli ordini presenti, senza query per riga."""
    chiavi = set()
    for riga in ordini_cliente:
        if selezione_attiva:
            chiavi.add((int(riga.IdDocumento), int(riga.IdRigaDoc)))
        else:
            for ordine in (riga.OrdiniDettaglio or ()):
                chiavi.add((int(ordine.IdDocumento), int(ordine.IdRigaDoc)))
    risultati = {chiave: [] for chiave in chiavi}
    elenco = sorted(chiavi)
    # Un blocco di 250 coppie rientra anche nei limiti dei parametri SQLite legacy.
    for posizione in range(0, len(elenco), 250):
        blocco = elenco[posizione:posizione + 250]
        stmt = select(NotaVenditeOrdine).where(
            tuple_(NotaVenditeOrdine.IdDocumento, NotaVenditeOrdine.IdRigaDoc).in_(blocco)
        ).order_by(NotaVenditeOrdine.CreatoIl.desc(), NotaVenditeOrdine.Id.desc())
        for nota in db.session.scalars(stmt):
            risultati[(nota.IdDocumento, nota.IdRigaDoc)].append(nota)
    return risultati


@bp_ordini_cliente.app_context_processor
def _context_note_righe():
    return {"note_vendite_per_righe": _note_per_righe}


def _render_cella(id_documento, id_riga):
    return render_template(
        "ordini/_partials/_note_vendite_cella.j2",
        id_documento=id_documento,
        id_riga=id_riga,
        note=_carica_note(id_documento, id_riga),
    )


def _render_pannello_note(id_documento, id_riga):
    return render_template(
        "ordini/_partials/_note_vendite_pannello.j2",
        ordine=_ordine_esistente(id_documento, id_riga),
        note=_carica_note(id_documento, id_riga),
    )


@bp_ordini_cliente.get("/ordine/<int:id_documento>/<int:id_riga>/note/pannello", endpoint="note_vendite_pannello")
@solo_ruoli("admin", "utente_produzione", "utente_vendite")
def note_vendite_pannello(id_documento, id_riga):
    return _render_pannello_note(id_documento, id_riga)

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
    if request.headers.get("HX-Request") == "true":
        if request.form.get("vista_note") == "riga":
            return _render_cella(id_documento, id_riga)
        return _render_pannello_note(id_documento, id_riga)
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
    if request.headers.get("HX-Request") == "true":
        if request.form.get("vista_note") == "riga":
            return _render_cella(nota.IdDocumento, nota.IdRigaDoc)
        return _render_pannello_note(nota.IdDocumento, nota.IdRigaDoc)
    flash("Commento modificato.", "success")
    return redirect(url_for("ordini_cliente.note_vendite_ordine",
                            id_documento=nota.IdDocumento, id_riga=nota.IdRigaDoc))
