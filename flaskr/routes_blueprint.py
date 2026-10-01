from flask import Blueprint

bp_login = Blueprint("auth", __name__, url_prefix="/auth")

bp_ordini_cliente = Blueprint("ordini_cliente", __name__, url_prefix="/ordini")