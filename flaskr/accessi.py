"""Autorizzazioni applicative lato server. Importare e inizializzare in create_app."""
import secrets
from functools import wraps
import click
from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import generate_password_hash
from flaskr.service.password_login_service import imposta_password
from flaskr.extensions import db
from flaskr.models.model_auth import User
from flaskr.models.model_accessi import RuoloUtente, RUOLI

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
    "ordini_cliente.calendario_riepilogo_giorno",
    "ordini_cliente.calendario_riepilogo_piano",
    "ordini_cliente.note_vendite_ordine",
    "ordini_cliente.note_vendite_pannello",
})
VENDITE_POST = frozenset({
    "ordini_cliente.aggiungi_nota_vendite",
    "ordini_cliente.modifica_nota_vendite",
    "ordini_cliente.elimina_nota_vendite",
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
        """Crea un primo amministratore con password personalizzata."""
        from sqlalchemy import select
        if db.session.scalar(select(RuoloUtente).where(RuoloUtente.Ruolo == "admin")):
            raise click.ClickException("Un admin esiste gia': usa imposta-password-login.")
        username = username.strip()
        if not username or not password:
            raise click.ClickException("Username e password non vuoti obbligatori.")
        utente = db.session.scalar(select(User).where(User.username == username))
        if utente is None:
            utente = User(username=username, password=generate_password_hash(password))
            db.session.add(utente)
            db.session.flush()
        try:
            imposta_password(utente, password)
        except ValueError as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from error
        ruolo = db.session.get(RuoloUtente, utente.id)
        if ruolo is None:
            db.session.add(RuoloUtente(UserId=utente.id, Ruolo="admin"))
        else:
            ruolo.Ruolo = "admin"
        db.session.commit()
        click.echo(f"Account amministratore '{utente.username}' configurato con la password scelta.")

    @app.cli.command("diagnostica-login")
    def diagnostica_login():
        """Mostra dove Flask cerca gli utenti, senza leggere password o codici."""
        from sqlalchemy import inspect, text
        from sqlalchemy.exc import SQLAlchemyError

        db_url = db.engine.url
        click.echo(f"Database collegato: {db_url.render_as_string(hide_password=True)}")
        click.echo(f"Tabella usata dal modello User: {User.__tablename__}")
        tabelle = set(inspect(db.engine).get_table_names())
        click.echo("Tabelle utente presenti: " + ", ".join(
            sorted(tabelle.intersection({"user", "users"}))
        ) if tabelle.intersection({"user", "users"}) else "Tabelle utente presenti: NESSUNA")

        # I nomi delle tabelle sono costanti, non input dell'utente.
        for nome in ("users", "user"):
            if nome not in tabelle:
                continue
            try:
                with db.engine.connect() as connessione:
                    righe = connessione.execute(
                        text(f'SELECT id, username FROM "{nome}" ORDER BY id LIMIT 40')
                    ).all()
                    totale = connessione.execute(
                        text(f'SELECT COUNT(*) FROM "{nome}"')
                    ).scalar_one()
                click.echo(f"Tabella {nome}: {totale} utenti. Primi 40: ")
                for identificativo, username in righe:
                    click.echo(f"  ID={identificativo}, username={username}")
            except SQLAlchemyError as exc:
                click.echo(f"Impossibile leggere {nome}: {exc.__class__.__name__}")

        if User.__tablename__ not in tabelle:
            click.secho("ATTENZIONE: il modello User punta a una tabella inesistente in questo database.", fg="red")
            return
        if User.__tablename__ != "users" and "users" in tabelle:
            click.secho("ATTENZIONE: il modello NON legge la tabella 'users'. Verifica model_auth.py.", fg="yellow")

        if RuoloUtente.__tablename__ in tabelle:
            try:
                from sqlalchemy import select
                amministratori = db.session.execute(
                    select(User.id, User.username)
                    .join(RuoloUtente, RuoloUtente.UserId == User.id)
                    .where(RuoloUtente.Ruolo == "admin")
                ).all()
                click.echo("Account con ruolo admin: " + (
                    ", ".join(f"{username} (ID={identificativo})" for identificativo, username in amministratori)
                    if amministratori else "nessuno"
                ))
            except SQLAlchemyError as exc:
                click.echo(f"Impossibile leggere i ruoli: {exc.__class__.__name__}")
        else:
            click.echo("Tabella ruoli non trovata: verifica le migrazioni.")

    @app.cli.command("imposta-password-login")
    @click.argument("username", required=False)
    @click.option("--user-id", type=int, help="ID numerico account, da diagnostica-login.")
    @click.option("--admin", "seleziona_admin", is_flag=True, help="Seleziona l'unico account admin.")
    @click.password_option(confirmation_prompt=True)
    def imposta_password_login(username, user_id, seleziona_admin, password):
        """Sostituisce la password di un account senza conoscerne quella vecchia.

        ATTENZIONE: usare soltanto sulla console del server, con accesso
        amministrativo. La password e' richiesta in maniera non visibile.
        """
        from sqlalchemy import inspect, select, func
        selezioni = sum([bool(username), user_id is not None, seleziona_admin])
        if selezioni != 1:
            raise click.ClickException(
                "Usa uno username, --user-id N o --admin (esattamente uno). "
                "Puoi prima eseguire diagnostica-login."
            )
        tabelle = set(inspect(db.engine).get_table_names())
        if User.__tablename__ not in tabelle:
            raise click.ClickException(
                f"Il modello punta a '{User.__tablename__}', ma la tabella non esiste. "
                "Controlla model_auth.py e la configurazione database."
            )
        if User.__tablename__ != 'users' and 'users' in tabelle:
            raise click.ClickException(
                "Il modello non sta usando 'users', presente nel database. "
                "Controlla model_auth.py prima di impostare una password."
            )
        if not password:
            raise click.ClickException("La password non puo' essere vuota.")
        utente = None
        if user_id is not None:
            utente = db.session.get(User, user_id) if user_id > 0 else None
        elif seleziona_admin:
            admins = db.session.scalars(
                select(User).join(RuoloUtente, RuoloUtente.UserId == User.id)
                .where(RuoloUtente.Ruolo == 'admin')
            ).all()
            if len(admins) != 1:
                raise click.ClickException(
                    f"Trovati {len(admins)} admin: usa diagnostica-login e --user-id N."
                )
            utente = admins[0]
        else:
            richiesto = username.strip()
            utente = db.session.scalar(select(User).where(User.username == richiesto))
            if utente is None:
                candidati = db.session.scalars(
                    select(User).where(func.lower(User.username) == richiesto.lower())
                ).all()
                if len(candidati) == 1:
                    utente = candidati[0]
        if utente is None:
            raise click.ClickException("Account non trovato: esegui diagnostica-login.")
        try:
            imposta_password(utente, password)
            db.session.commit()
        except ValueError as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from error
        except Exception:
            db.session.rollback()
            raise
        click.echo(f"Password aggiornata per {utente.username} (ID={utente.id}).")

