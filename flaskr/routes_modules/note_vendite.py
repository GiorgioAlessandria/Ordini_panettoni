"""Note per riga d'ordine: produzione sola lettura, vendite scrittura."""
from collections.abc import Mapping
from datetime import datetime, timezone
from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select, tuple_
from flaskr.extensions import db
from flaskr.models.model_ordini import OrdiniCliente
from flaskr.models.model_accessi import NotaVenditeOrdine
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.accessi import solo_ruoli, verifica_csrf, ruolo_attuale
from flaskr.service.scadenze_giornaliere_service import articolo_cnf


def _ordine_esistente(id_documento, id_riga):
    ordine = db.session.get(OrdiniCliente, (id_documento, id_riga))
    if ordine is None or (ruolo_attuale() == "utente_produzione" and articolo_cnf(ordine.CodArt)):
        abort(404, description="Ordine non presente o non visibile per questo profilo.")
    return ordine




def _carica_note(id_documento, id_riga):
    return db.session.scalars(select(NotaVenditeOrdine).where(
        NotaVenditeOrdine.IdDocumento == id_documento,
        NotaVenditeOrdine.IdRigaDoc == id_riga,
    ).order_by(NotaVenditeOrdine.CreatoIl.desc(), NotaVenditeOrdine.Id.desc())).all()


def _campo_ordine(riga, nome):
    """Legge sia i dizionari del service sia le istanze ORM/oggetto."""
    if isinstance(riga, Mapping):
        return riga.get(nome)
    return getattr(riga, nome, None)


def _chiave_ordine(riga):
    documento = _campo_ordine(riga, "IdDocumento")
    riga_doc = _campo_ordine(riga, "IdRigaDoc")
    if documento is None or riga_doc is None:
        return None
    try:
        return (int(documento), int(riga_doc))
    except (TypeError, ValueError):
        return None


def _note_per_righe(ordini_cliente, selezione_attiva):
    """Recupero batch delle note per ordini dict/ORM, inclusi i raggruppamenti.

    Nessuna query se non ci sono chiavi valide.
    """
    chiavi = set()
    for riga in ordini_cliente:
        if selezione_attiva:
            chiave = _chiave_ordine(riga)
            if chiave is not None:
                chiavi.add(chiave)
        else:
            for ordine in (_campo_ordine(riga, "OrdiniDettaglio") or ()):
                chiave = _chiave_ordine(ordine)
                if chiave is not None:
                    chiavi.add(chiave)
    risultati = {chiave: [] for chiave in chiavi}
    elenco = sorted(chiavi)
    # Fino a 250 coppie, compatibile con i limiti SQLite sui parametri.
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
    _ordine_esistente(id_documento, id_riga)
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


@bp_ordini_cliente.post("/ordine/note/<int:nota_id>/elimina", endpoint="elimina_nota_vendite")
@solo_ruoli("admin", "utente_vendite")
def elimina_nota_vendite(nota_id: int):
    """Elimina il singolo commento scelto; i precedenti restano nel DB.

    CSRF, ruolo e proprieta' del commento sono validati lato server.
    """
    verifica_csrf()
    nota = db.session.get(NotaVenditeOrdine, nota_id)
    if nota is None:
        abort(404, description="Commento non trovato.")
    _ordine_esistente(nota.IdDocumento, nota.IdRigaDoc)
    if ruolo_attuale() != "admin" and nota.AutoreId != g.user.id:
        abort(403, description="Puoi eliminare soltanto i tuoi commenti.")
    id_documento, id_riga = nota.IdDocumento, nota.IdRigaDoc
    db.session.delete(nota)
    db.session.commit()
    if request.headers.get("HX-Request") == "true":
        if request.form.get("vista_note") == "riga":
            return _render_cella(id_documento, id_riga)
        return _render_pannello_note(id_documento, id_riga)
    flash("Commento eliminato.", "success")
    return redirect(url_for("ordini_cliente.note_vendite_ordine",
                            id_documento=id_documento, id_riga=id_riga))
