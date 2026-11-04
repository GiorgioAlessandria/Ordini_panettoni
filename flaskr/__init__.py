"""Factory Flask per sviluppo e produzione IIS + wfastcgi.

La configurazione di produzione ha precedenza sulle impostazioni di sviluppo.
Il database production e' configurato nel file esterno production.py.
"""
import logging
import os
from logging import FileHandler, Formatter
from pathlib import Path

from flask import Flask, redirect, url_for
from sqlalchemy.engine import make_url

from flaskr import routes_auth, routes_production
from flaskr.extensions import db, migrate
from flaskr.models import model_accessi  # importa i modelli per SQLAlchemy/Alembic
from flaskr.routes_blueprint import bp_login, bp_ordini_cliente
from flaskr.routes_modules import (
    accessi_utenti,
    calendario,
    impostazioni,
    lotto_pianificazione_routes,
    pianificazione_mista_unificata,
    ripianificazione_mista_routes,
    note_vendite,
    note_produzione,
    ordini,
)

def create_app():
    app = Flask(__name__)

    # Valori di sviluppo: la configurazione production li sovrascrive.
    # Non creare la directory instance in produzione.
    production_config = os.environ.get("ORDINI_PANETTONI_CONFIG")
    if not production_config:
        os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY="development",
        SQLALCHEMY_DATABASE_URI=(
            "sqlite:////C://ProgramData//OrdiniPanettoni//data//db_ordini.sqlite3"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DEBUG=False,
        TESTING=False,
        IMPOSTAZIONI_ADMIN_USERS=["admin"],
    )

    if production_config:
        config_file = Path(production_config)
        if not config_file.is_file():
            raise RuntimeError(f"Configurazione production assente: {config_file}")

        app.config.from_pyfile(str(config_file), silent=False)

        if app.config.get("SECRET_KEY") in (None, "", "development"):
            raise RuntimeError("SECRET_KEY production assente o non sicura")
        if app.config.get("DEBUG") or app.config.get("TESTING"):
            raise RuntimeError("DEBUG/TESTING non possono essere attivi in production")

        # SQLite puo' creare un nuovo database vuoto se il file non esiste.
        # In produzione, invece, rifiutiamo l'avvio per evitare database sbagliati.
        uri = app.config.get("SQLALCHEMY_DATABASE_URI")
        if not isinstance(uri, str) or not uri:
            raise RuntimeError("SQLALCHEMY_DATABASE_URI production assente")
        db_url = make_url(uri)
        if db_url.get_backend_name() != "sqlite" or not db_url.database:
            raise RuntimeError("La configurazione production deve utilizzare SQLite su file")
        if not Path(db_url.database).is_file():
            raise RuntimeError(f"Database production inesistente: {db_url.database}")

        log_file = app.config.get("FLASK_ERROR_LOG")
        if log_file:
            handler = FileHandler(log_file, encoding="utf-8")
            handler.setLevel(logging.ERROR)
            handler.setFormatter(Formatter(
                "%(asctime)s [%(levelname)s] %(message)s"
            ))
            app.logger.addHandler(handler)

    db.init_app(app)
    migrate.init_app(app, db)

    app.register_blueprint(bp_login)
    app.register_blueprint(bp_ordini_cliente)
    from flaskr.accessi import init_accessi
    init_accessi(app)

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    return app
