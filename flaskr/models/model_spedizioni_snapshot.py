"""Snapshot locale di righe chiuse. Non dipende da ordini_cliente_aperti.

Un ordine scomparso dal feed ERP continua a essere visibile alla logistica;
la registrazione della partenza nella testata Spedizione lo esclude dai pronti.
"""
from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from flaskr.extensions import db


class OrdineProntoSpedizione(db.Model):
    __tablename__ = "ordini_pronti_spedizione"

    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key=True)
    CodCliFor: Mapped[int] = mapped_column(nullable=False)
    ClienteSnapshot: Mapped[str] = mapped_column(db.String(250), nullable=False)
    CodArt: Mapped[str] = mapped_column(db.String(100), nullable=False)
    DesArt: Mapped[str] = mapped_column(db.String(500), nullable=False)
    DataConsegna: Mapped[datetime | None] = mapped_column(nullable=True)
    QtaSaldoERPSnapshot: Mapped[int] = mapped_column(nullable=False)
    QtaAssegnataSpedizione: Mapped[int] = mapped_column(nullable=False)
    LottiSnapshot: Mapped[str | None] = mapped_column(db.Text, nullable=True)
    PresenteERP: Mapped[bool] = mapped_column(db.Boolean, nullable=False)
    ConfermatoChiuso: Mapped[bool] = mapped_column(db.Boolean, nullable=False)
    AggiornatoIl: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint("QtaAssegnataSpedizione >= 0", name="ck_pronti_qta_nonnegativa"),
        CheckConstraint("PresenteERP IN (0,1) AND ConfermatoChiuso IN (0,1)",
                        name="ck_pronti_booleani"),
    )
