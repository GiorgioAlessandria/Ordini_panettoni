import os

from flask import Flask, redirect, url_for

from flaskr import (
    routes_auth,
    routes_production,
)
from flaskr.extensions import db, migrate
from flaskr.models import model_accessi  # importa i modelli per SQLAlchemy/Alembic
from flaskr.routes_blueprint import bp_login, bp_ordini_cliente
from flaskr.routes_modules import (
    accessi_utenti,
    calendario,
    impostazioni,
    note_vendite,
    ordini,
)


def create_app():
    app = Flask(__name__)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY="development",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{os.path.join(app.instance_path, 'db_ordini.sqlite3')}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        FLASK_DEBUG=1,
    )
    app.config["IMPOSTAZIONI_ADMIN_USERS"] = ["admin"]

    db.init_app(app)
    migrate.init_app(app, db)
    # Per migrare nuovi dati nel db
    # flask --app flaskr:create_app db migrate --directory flaskr/migrations -m "xxx"
    # flask --app flaskr:create_app db upgrade --directory flaskr/migrations

    app.register_blueprint(bp_login)
    app.register_blueprint(bp_ordini_cliente)
    from flaskr.accessi import init_accessi

    init_accessi(app)

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    return app


#   routes_blueprint.py
#   CREA
#
#   routes_auth.py
#   routes_production.py
#   routes_modules/*.py
#   POPOLANO
#
#   __init__.py
#   ASSEMBLA E REGISTRA
