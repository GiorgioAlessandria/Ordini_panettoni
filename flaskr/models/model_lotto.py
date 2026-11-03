"""Registro storico dei lotti di pianificazione, condiviso tra legacy e misti.

Ogni codice e' immutabile: una ripianificazione disattiva il precedente e
aggiunge un nuovo record. Corrente=0 NON libera mai il lotto.
"""
from datetime import date, datetime
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db


class LottoPianificazione(db.Model):
    __tablename__ = 'lotti_pianificazione'
    Id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    Lotto: Mapped[str] = mapped_column(db.String(10), nullable=False, unique=True)
    DataImpasto: Mapped[date] = mapped_column(nullable=False, index=True)
    Progressivo: Mapped[int] = mapped_column(nullable=False)
    TipoOwner: Mapped[str] = mapped_column(db.String(5), nullable=False)
    IdOwner: Mapped[int] = mapped_column(nullable=False)
    Corrente: Mapped[bool] = mapped_column(db.Boolean, nullable=False, server_default=text('1'))
    AssegnatoIl: Mapped[datetime] = mapped_column(nullable=False)
    Operatore: Mapped[str] = mapped_column(db.String(120), nullable=False)
    SostituitoIl: Mapped[datetime | None] = mapped_column(nullable=True)
    MotivoCambio: Mapped[str | None] = mapped_column(db.String(500), nullable=True)
    __table_args__ = (
        UniqueConstraint('DataImpasto', 'Progressivo', name='uq_lotti_data_progressivo'),
        CheckConstraint("length(Lotto) = 10 AND Lotto NOT GLOB '*[^0-9]*'", name='ck_lotti_formato'),
        CheckConstraint('Progressivo BETWEEN 1 AND 99', name='ck_lotti_progressivo'),
        CheckConstraint("TipoOwner IN ('TRAD', 'MISTA')", name='ck_lotti_tipo'),
        CheckConstraint('IdOwner > 0', name='ck_lotti_id_owner'),
        CheckConstraint('Corrente IN (0,1)', name='ck_lotti_corrente'),
        Index('uq_lotti_owner_corrente', 'TipoOwner', 'IdOwner', unique=True,
              sqlite_where=text('Corrente = 1')),
    )
