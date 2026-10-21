"""Creazione e gestione account da parte del ruolo admin."""
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash
from flaskr.extensions import db
from flaskr.models.model_auth import User
from flaskr.models.model_accessi import NotaVenditeOrdine, RuoloUtente, RUOLI
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.accessi import MIN_PASSWORD_LENGTH, solo_ruoli, verifica_csrf


@bp_ordini_cliente.get("/utenti", endpoint="admin_utenti")
@solo_ruoli("admin")
def admin_utenti():
    utenti = db.session.scalars(select(User).order_by(User.username)).all()
    mapping = {r.UserId: r.Ruolo for r in db.session.scalars(select(RuoloUtente)).all()}
    numero_admin = db.session.scalar(
        select(func.count()).select_from(RuoloUtente)
        .join(User, RuoloUtente.UserId == User.id)
        .where(RuoloUtente.Ruolo == "admin")
    ) or 0
    return render_template(
        "ordini/utenti_amministrazione.j2",
        utenti=utenti, ruoli=mapping, ruoli_validi=RUOLI,
        numero_admin=numero_admin, utente_corrente_id=g.user.id,
        min_password_length=MIN_PASSWORD_LENGTH,
    )


@bp_ordini_cliente.post("/utenti/crea", endpoint="admin_crea_utente")
@solo_ruoli("admin")
def admin_crea_utente():
    verifica_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    ruolo = request.form.get("ruolo", "")
    if not username or len(password) < MIN_PASSWORD_LENGTH or ruolo not in RUOLI:
        abort(400, description=f"Username, ruolo e password di almeno {MIN_PASSWORD_LENGTH} caratteri obbligatori.")
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
        quanti = db.session.scalar(
            select(func.count()).select_from(RuoloUtente)
            .join(User, RuoloUtente.UserId == User.id)
            .where(RuoloUtente.Ruolo == "admin")
        )
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
    if len(password) < MIN_PASSWORD_LENGTH:
        abort(400, description=f"La password deve contenere almeno {MIN_PASSWORD_LENGTH} caratteri.")
    utente = db.session.get(User, user_id)
    if utente is None:
        abort(404)
    utente.password = generate_password_hash(password)
    db.session.commit()
    flash("Password modificata.", "success")
    return redirect(url_for("ordini_cliente.admin_utenti"))


@bp_ordini_cliente.post("/utenti/<int:user_id>/elimina", endpoint="admin_elimina_utente")
@solo_ruoli("admin")
def admin_elimina_utente(user_id: int):
    """Elimina l'account senza cancellare la storia dei commenti vendite."""
    verifica_csrf()

    # L'amministratore che sta eseguendo l'azione deve restare autenticabile.
    if user_id == g.user.id:
        abort(403, description="Non puoi eliminare il tuo account.")

    utente = db.session.get(User, user_id)
    if utente is None:
        abort(404, description="Account inesistente.")

    ruolo = db.session.get(RuoloUtente, user_id)
    if ruolo is not None and ruolo.Ruolo == "admin":
        numero_admin = db.session.scalar(
            select(func.count()).select_from(RuoloUtente)
            .join(User, RuoloUtente.UserId == User.id)
            .where(RuoloUtente.Ruolo == "admin")
        ) or 0
        if numero_admin <= 1:
            abort(409, description="Non puoi eliminare l'ultimo amministratore.")

    try:
        # I commenti mantengono AutoreNome. Un ID negativo impedisce che
        # un futuro utente con ID riutilizzato possa modificare vecchie note.
        db.session.execute(
            update(NotaVenditeOrdine)
            .where(NotaVenditeOrdine.AutoreId == user_id)
            .values(AutoreId=-user_id)
        )
        if ruolo is not None:
            db.session.delete(ruolo)
        db.session.delete(utente)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Account non eliminato: esistono collegamenti che ne impediscono la cancellazione.", "danger")
        return redirect(url_for("ordini_cliente.admin_utenti"))
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Errore durante l'eliminazione dell'utente %s", user_id)
        flash("Impossibile eliminare l'account. Nessuna modifica salvata.", "danger")
        return redirect(url_for("ordini_cliente.admin_utenti"))

    flash("Account eliminato. I commenti storici sono stati conservati.", "success")
    return redirect(url_for("ordini_cliente.admin_utenti"))
