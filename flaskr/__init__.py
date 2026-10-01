import os

from flask import Flask, redirect, url_for

from flaskr.extensions import db, migrate


def create_app():
    app = Flask(__name__)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(SECRET_KEY = 'development',
                            SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(app.instance_path, 'db_ordini.sqlite3')}",
                            SQLALCHEMY_TRACK_MODIFICATIONS = False,
                            FLASK_DEBUG = 1,)

    db.init_app(app)
    migrate.init_app(app, db)
    # Per migrare nuovi dati nel db
    # flask --app flaskr:create_app db migrate -m "Titolo migrazione"
    # flask --app flaskr:create_app db upgrade

    from flaskr import routes_auth
    from flaskr import routes_production
    from flaskr.routes_modules import ordini
    from flaskr.routes_modules import calendario
    from flaskr.routes_blueprint import bp_login, bp_ordini_cliente
    app.register_blueprint(bp_login)
    app.register_blueprint(bp_ordini_cliente)


    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))
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