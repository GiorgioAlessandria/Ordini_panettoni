"""Snapshot/audit degli ordini ERP rimossi durante una pianificazione attiva.

Le chiavi non hanno ForeignKey verso ordini_cliente_aperti, ne' verso le
associazioni della pianificazione: devono sopravvivere a entrambe le rimozioni.
"""
from datetime import datetime
from sqlalchemy import CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db


class AnomaliaOrdineERP(db.Model):
    __tablename__ = 'anomalie_ordini_erp'
    TipoPiano: Mapped[str] = mapped_column(db.String(5), primary_key=True)
    IdPiano: Mapped[int] = mapped_column(primary_key=True)
    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key=True)
    Decisione: Mapped[str] = mapped_column(db.String(12), nullable=False, default='DaDecidere')
    CodArt: Mapped[str] = mapped_column(db.String(100), nullable=False)
    CodCliFor: Mapped[int] = mapped_column(nullable=False)
    ClienteSnapshot: Mapped[str] = mapped_column(db.String(250), nullable=False)
    DesArtSnapshot: Mapped[str] = mapped_column(db.String(500), nullable=False)
    DataConsegnaSnapshot: Mapped[datetime | None] = mapped_column(nullable=True)
    SaldoERPSnapshot: Mapped[int] = mapped_column(nullable=False)
    QtaPianificata: Mapped[int] = mapped_column(nullable=False)
    QtaDaStock: Mapped[int] = mapped_column(nullable=False, default=0)
    RilevataIl: Mapped[datetime] = mapped_column(nullable=False)
    DecisaIl: Mapped[datetime | None] = mapped_column(nullable=True)
    Operatore: Mapped[str | None] = mapped_column(db.String(120), nullable=True)
    Motivazione: Mapped[str | None] = mapped_column(db.String(500), nullable=True)
    DatiPrima: Mapped[str | None] = mapped_column(db.Text, nullable=True)
    DatiDopo: Mapped[str | None] = mapped_column(db.Text, nullable=True)
    __table_args__ = (
        CheckConstraint("TipoPiano IN ('TRAD','MISTA')", name='ck_anomalia_tipo'),
        CheckConstraint("Decisione IN ('DaDecidere','Continua','Escluso')", name='ck_anomalia_decisione'),
        CheckConstraint('QtaPianificata > 0 AND QtaDaStock >= 0 AND QtaDaStock <= QtaPianificata', name='ck_anomalia_quantita'),
        CheckConstraint('SaldoERPSnapshot >= 0', name='ck_anomalia_saldo'),
        Index('ix_anomalie_stato', 'Decisione', 'TipoPiano', 'IdPiano'),
    )
