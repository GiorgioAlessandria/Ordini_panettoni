from flask import (
    Blueprint,
)

bp_ordini_cliente = Blueprint('ordini_cliente', __name__, url_prefix = '/')

from flaskr.routes_modules import calendario, ordini