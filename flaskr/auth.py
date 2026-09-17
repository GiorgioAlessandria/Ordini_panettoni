import functools
from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
    )
from werkzeug.security import check_password_hash, generate_password_hash
from flaskr.extensions import db
from flaskr.models.model_auth import User

bp_login = Blueprint('auth', __name__, url_prefix= '/auth')

@bp_login.route('/register', methods = ['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form["username"]
        password = request.form["password"]
        if username and password:
            user = User(username = username, password = generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
        else:
            error = "Incorrect username or password."
            flash(error)
        return redirect(url_for("auth.login"))
    else:
        return render_template("login/reg_utente.j2")


@bp_login.route("/login", methods = ("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        error = None
        if not username:
            error = "Username is required."
        elif not password:
            error = "Password is required."
        else:
            stmt = db.select(User).where(
                    User.username == username
                    )
            user = db.session.execute(
                    stmt
                    ).scalar_one_or_none()
            if user is None:
                error = "Incorrect username."
            elif not check_password_hash(
                    user.password,
                    password
                    ):
                error = "Incorrect password."
        if error is None:
            session.clear()
            session["user_id"] = user.id
            return redirect("ordini_cliente/")
        flash(error)

    return render_template("login/login.j2")

@bp_login.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = db.session.get(
                User,
                user_id
                )

def login_required(f):
    @functools.wraps(f)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return wrapped_view
