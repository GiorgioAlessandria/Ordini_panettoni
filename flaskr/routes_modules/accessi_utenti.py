"""Creazione e gestione account da parte del ruolo admin."""
from sqlalchemy import select, func
from flask import abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash
from flaskr.extensions import db
from flaskr.models.model_auth import User
from flaskr.models.model_accessi import RuoloUtente, RUOLI
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.accessi import solo_ruoli, verifica_csrf


@bp_ordini_cliente.get("/utenti", endpoint="admin_utenti")
@solo_ruoli("admin")
def admin_utenti():
    utenti = db.session.scalars(select(User).order_by(User.username)).all()
    mapping = {r.UserId: r.Ruolo for r in db.session.scalars(select(RuoloUtente)).all()}
    return render_template("ordini/utenti_amministrazione.j2", utenti=utenti,
                           ruoli=mapping, ruoli_validi=RUOLI)


@bp_ordini_cliente.post("/utenti/crea", endpoint="admin_crea_utente")
@solo_ruoli("admin")
def admin_crea_utente():
    verifica_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    ruolo = request.form.get("ruolo", "")
    if not username or len(password) < 10 or ruolo not in RUOLI:
        abort(400, description="Username, ruolo e password di almeno 10 caratteri obbligatori.")
    if db.session.scalar(select(User.id).where(User.username == username)) is not None:
        flash("Username gia' presente.", "warning")
        return redirect(url_for("ordini_cliente.admin_utenti"))
    try:
        utente = User(username=username, password=generate_password_hash(password))
        db.session.add(utente)
        db.session.flush()
        db.session.add(RuoloUtente(UserId=utente.id, Ruolo=ruolo))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    flash("Account creato.", "success")
    return redirect(url_for("ordini_cliente.admin_utenti"))


@bp_ordini_cliente.post("/utenti/<int:user_id>/ruolo", endpoint="admin_cambia_ruolo")
@solo_ruoli("admin")
def admin_cambia_ruolo(user_id):
    verifica_csrf()
    ruolo = request.form.get("ruolo", "")
    if ruolo not in RUOLI:
        abort(400)
    utente = db.session.get(User, user_id)
    if utente is None:
        abort(404)
    record = db.session.get(RuoloUtente, user_id)
    if record and record.Ruolo == "admin" and ruolo != "admin":
        quanti = db.session.scalar(select(func.count()).select_from(RuoloUtente).where(RuoloUtente.Ruolo == "admin"))
        if quanti <= 1:
            abort(409, description="Impossibile rimuovere l'ultimo amministratore.")
    if record:
        record.Ruolo = ruolo
    else:
        db.session.add(RuoloUtente(UserId=user_id, Ruolo=ruolo))
    db.session.commit()
    flash("Ruolo aggiornato.", "success")
    return redirect(url_for("ordini_cliente.admin_utenti"))


@bp_ordini_cliente.post("/utenti/<int:user_id>/password", endpoint="admin_cambia_password")
@solo_ruoli("admin")
def admin_cambia_password(user_id):
    verifica_csrf()
    password = request.form.get("password", "")
    if len(password) < 10:
        abort(400, description="La password deve contenere almeno 10 caratteri.")
    utente = db.session.get(User, user_id)
    if utente is None:
        abort(404)
    utente.password = generate_password_hash(password)
    db.session.commit()
    flash("Password modificata.", "success")
    return redirect(url_for("ordini_cliente.admin_utenti"))
