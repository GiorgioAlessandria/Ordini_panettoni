import os
from flask import Flask

def create_app():
    app = Flask(__name__)
    app.config.from_mapping(secret_key = 'development',DB_ORDINI=os.path.join(app.instance_path,'db_ordini.sqlite3'))
    os.makedirs(app.instance_path)


    from . import db_conn
    db_conn.init_app(app)

    from . import auth
    app.register_blueprint(auth.bp)

    return app
