"""Catena storica, immutabile, delle ripianificazioni miste."""
from datetime import date, datetime
from sqlalchemy import ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db


class StoricoRipianificazioneMista(db.Model):
    __tablename__ = 'storico_ripianificazioni_miste'
    Id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdBozzaPrecedente: Mapped[int] = mapped_column(
        ForeignKey('pianificazioni_miste_attive.IdBozza', ondelete='RESTRICT'), nullable=False)
    IdBozzaNuova: Mapped[int] = mapped_column(
        ForeignKey('pianificazioni_miste_attive.IdBozza', ondelete='RESTRICT'), nullable=False)
    DataPrecedente: Mapped[date] = mapped_column(nullable=False)
    DataNuova: Mapped[date] = mapped_column(nullable=False)
    LottoPrecedente: Mapped[str] = mapped_column(db.String(10), nullable=False)
    LottoNuovo: Mapped[str] = mapped_column(db.String(10), nullable=False)
    Operatore: Mapped[str] = mapped_column(db.String(120), nullable=False)
    Motivo: Mapped[str] = mapped_column(db.String(500), nullable=False)
    DataOperazione: Mapped[datetime] = mapped_column(nullable=False)
    __table_args__ = (
        UniqueConstraint('IdBozzaPrecedente', name='uq_ripian_mista_vecchia'),
        UniqueConstraint('IdBozzaNuova', name='uq_ripian_mista_nuova'),
        CheckConstraint('IdBozzaPrecedente != IdBozzaNuova', name='ck_ripian_mista_distinti'),
        CheckConstraint('DataPrecedente != DataNuova', name='ck_ripian_mista_date'),
        CheckConstraint('length(Motivo) BETWEEN 5 AND 500', name='ck_ripian_mista_motivo'),
    )
