from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db
class User(db.Model):
    __tablename__ = 'users'
    id:Mapped[int]= mapped_column(db.Integer, primary_key=True, autoincrement=True)
    username:Mapped[str]
    password:Mapped[str]