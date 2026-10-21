"""Autorizzazioni applicative lato server. Importare e inizializzare in create_app."""
import secrets
from functools import wraps
import click
from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import generate_password_hash
from flaskr.extensions import db
from flaskr.models.model_auth import User
from flaskr.models.model_accessi import RuoloUtente, RUOLI

MIN_PASSWORD_LENGTH = 5

RUOLI_GESTIONALI = frozenset(("admin", "utente_produzione"))

# Endpoints di sola consultazione concessi a vendite. L'elenco e' chiuso per sicurezza:
# una nuova route produttiva non diventa automaticamente visibile.
VENDITE_GET = frozenset({
    "ordini_cliente.ordini_cliente",
    "ordini_cliente.tabella_ordini_cliente_da_produrre",
    "ordini_cliente.tabella_ordini_cliente_in_produzione",
    "ordini_cliente.dettaglio_ordine",
    "ordini_cliente.ordini_calendario",
    "ordini_cliente.ordini_calendario_scadenze",
    "ordini_cliente.eventi_fase_calendario",
    "ordini_cliente.calendario_riepilogo_ordine",
    "ordini_cliente.calendario_riepilogo_piano",
    "ordini_cliente.note_vendite_ordine",
})
VENDITE_POST = frozenset({
    "ordini_cliente.aggiungi_nota_vendite",
    "ordini_cliente.modifica_nota_vendite",
})


def ruolo_attuale():
    if getattr(g, "user", None) is None:
        return None
    if not hasattr(g, "_ruolo_utente"):
        record = db.session.get(RuoloUtente, g.user.id)
        g._ruolo_utente = record.Ruolo if record else None
    return g._ruolo_utente


def solo_ruoli(*ruoli):
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if getattr(g, "user", None) is None:
                return redirect(url_for("auth.login"))
            if ruolo_attuale() not in ruoli:
                abort(403, description="Non sei autorizzato a effettuare questa operazione.")
            return fn(*args, **kwargs)
        return wrapper
    return decorate


def csrf_token():
    if "csrf_ruoli" not in session:
        session["csrf_ruoli"] = secrets.token_urlsafe(32)
    return session["csrf_ruoli"]


def verifica_csrf():
    token = request.form.get("csrf_token", "")
    secret = session.get("csrf_ruoli", "")
    if not secret or not secrets.compare_digest(token, secret):
        abort(400, description="Token CSRF mancante o scaduto.")


def init_accessi(app):
    @app.before_request
    def autorizza_route():
        endpoint = request.endpoint or ""
        # Disabilita la registrazione pubblica anche se la route legacy e' presente.
        if endpoint == "auth.register":
            # Gli utenti si creano solo dal pannello admin protetto da CSRF.
            abort(403, description="Usa la pagina amministrativa per creare account.")
        if not endpoint.startswith("ordini_cliente."):
            return None
        if getattr(g, "user", None) is None:
            return redirect(url_for("auth.login"))
        ruolo = ruolo_attuale()
        if ruolo == "admin":
            return None
        if ruolo == "utente_produzione":
            if endpoint.startswith(("ordini_cliente.impostazioni_", "ordini_cliente.admin_")):
                abort(403)
            if endpoint in VENDITE_POST:  # Le note sono modificabili solo dalle vendite/admin.
                abort(403)
            return None
        if ruolo == "utente_vendite":
            if request.method in ("GET", "HEAD") and endpoint in VENDITE_GET:
                return None
            if request.method == "POST" and endpoint in VENDITE_POST:
                return None
            abort(403, description="La funzione non e' disponibile per il profilo vendite.")
        abort(403, description="Nessun ruolo assegnato. Contatta un amministratore.")

    @app.context_processor
    def contesto_ruoli():
        return {
            "ruolo_corrente": ruolo_attuale(),
            "puo_gestire_produzione": ruolo_attuale() in RUOLI_GESTIONALI,
            "puo_scrivere_note_vendite": ruolo_attuale() in ("admin", "utente_vendite"),
            "csrf_ruoli": csrf_token,
        }

    @app.cli.command("bootstrap-admin")
    @click.argument("username")
    @click.password_option(confirmation_prompt=True)
    def bootstrap_admin(username, password):
        """Crea il primo amministratore, o promuove un account esistente."""
        from sqlalchemy import select
        if db.session.scalar(select(RuoloUtente).where(RuoloUtente.Ruolo == "admin")):
            raise click.ClickException("Un admin esiste gia': usa /ordini/utenti.")
        username = username.strip()
        if not username or len(password) < MIN_PASSWORD_LENGTH:
            raise click.ClickException(f"Username e password di almeno {MIN_PASSWORD_LENGTH} caratteri richiesti.")
        utente = db.session.scalar(select(User).where(User.username == username))
        if utente is None:
            utente = User(username=username, password=generate_password_hash(password))
            db.session.add(utente)
            db.session.flush()
        record = db.session.get(RuoloUtente, utente.id)
        if record:
            record.Ruolo = "admin"
        else:
            db.session.add(RuoloUtente(UserId=utente.id, Ruolo="admin"))
        db.session.commit()
        click.echo(f"Amministratore '{username}' configurato.")
