"""Autenticazione tramite password personale senza digitare lo username."""
import functools
import secrets

from flask import abort, flash, g, redirect, render_template, request, session, url_for
from flaskr.extensions import db
from flaskr.models.model_auth import User
from flaskr.routes_blueprint import bp_login
from flaskr.service.login_code_service import azzera_tentativi, blocco_attivo, registra_errore
from flaskr.service.password_login_service import (
    _verifica_hash, cerca_per_password, imposta_password,
)


@bp_login.route('/register', methods=('GET', 'POST'))
def register():
    # Gli account si creano esclusivamente dalla pagina amministrativa.
    abort(403)


@bp_login.route('/login', methods=('GET', 'POST'))
def login():
    if getattr(g, 'user', None) is not None:
        return redirect(url_for('ordini_cliente.ordini_cliente'))

    if request.method == 'POST':
        token = request.form.get('csrf_login', '')
        previsto = session.get('csrf_login', '')
        if not previsto or not secrets.compare_digest(token, previsto):
            abort(400, description='Sessione di accesso scaduta: ricarica la pagina.')

        ip = request.remote_addr or 'unknown'
        if blocco_attivo(ip):
            flash('Troppi tentativi. Riprova tra 15 minuti.', 'danger')
        else:
            password = request.form.get('password', '')
            # Valida direttamente gli hash presenti nel campo users.password.
            utente = cerca_per_password(password)
            if utente is not None:
                azzera_tentativi(ip)
                session.clear()
                session.permanent = False
                session['user_id'] = utente.id
                return redirect(url_for('ordini_cliente.ordini_cliente'))
            registra_errore(ip)
            flash('Password non valida.', 'danger')

    if 'csrf_login' not in session:
        session['csrf_login'] = secrets.token_urlsafe(24)
    return (render_template('login/login.j2', csrf_login=session['csrf_login']),
            200, {'Cache-Control': 'no-store'})


@bp_login.route('/password', methods=('GET', 'POST'))
def cambia_password_personale():
    """Ogni operatore autenticato puo' cambiare la propria password."""
    if getattr(g, 'user', None) is None:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        from flaskr.accessi import verifica_csrf
        verifica_csrf()
        attuale = request.form.get('password_attuale', '')
        nuova = request.form.get('password_nuova', '')
        conferma = request.form.get('password_conferma', '')
        if not _verifica_hash(g.user.password, attuale):
            flash('Password attuale non corretta.', 'danger')
        elif not nuova:
            flash('Inserisci la nuova password.', 'warning')
        elif nuova != conferma:
            flash('Le nuove password non coincidono.', 'warning')
        else:
            try:
                imposta_password(g.user, nuova)
                db.session.commit()
            except ValueError as error:
                db.session.rollback()
                flash(str(error), 'warning')
            except Exception:
                db.session.rollback()
                raise
            else:
                flash('Password personale aggiornata.', 'success')
                return redirect(url_for('auth.cambia_password_personale'))
    return render_template('login/cambia_password.j2')


@bp_login.route('/logout', methods=('POST',))
def logout():
    from flaskr.accessi import verifica_csrf
    verifica_csrf()
    session.clear()
    return redirect(url_for('auth.login'))


@bp_login.before_app_request
def load_logged_in_user():
    user_id = session.get('user_id')
    g.user = db.session.get(User, user_id) if user_id is not None else None


def login_required(f):
    @functools.wraps(f)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return wrapped_view
