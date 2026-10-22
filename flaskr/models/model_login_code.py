"""Credenziali senza username nel form e blocchi sui tentativi falliti.

Non si aggiungono colonne a User: l'installazione supporta sia `user` sia `users`.
"""
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CodiceLoginUtente(db.Model):
    __tablename__ = 'codici_login_utente'
    UserId: Mapped[int] = mapped_column(primary_key=True)
    Digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    GeneratoIl: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class TentativoLoginCodice(db.Model):
    __tablename__ = 'tentativi_login_codice'
    IpDigest: Mapped[str] = mapped_column(String(64), primary_key=True)
    Tentativi: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    InizioFinestra: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    BloccatoFino: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
