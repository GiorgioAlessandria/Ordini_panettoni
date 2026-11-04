"""Consuntivi locali immutabili, indipendenti dal feed ERP."""
from datetime import datetime
from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db

class ConsuntivoProduzioneArticolo(db.Model):
    __tablename__ = "consuntivi_produzione_articoli"
    TipoPiano: Mapped[str] = mapped_column(String(5), primary_key=True)
    IdPiano: Mapped[int] = mapped_column(primary_key=True)
    CodArt: Mapped[str] = mapped_column(String(100), primary_key=True)
    QtaSecondaScelta: Mapped[int] = mapped_column(nullable=False)
    QtaMagazzino: Mapped[int] = mapped_column(nullable=False)
    StockAnticipato: Mapped[int] = mapped_column(nullable=False)
    LottoSnapshot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    Operatore: Mapped[str] = mapped_column(String(120), nullable=False)
    CreatoIl: Mapped[datetime] = mapped_column(nullable=False)
    __table_args__ = (
        CheckConstraint("TipoPiano IN ('TRAD','MISTA')", name="ck_cons_art_tipo"),
        CheckConstraint("QtaSecondaScelta >= 0 AND QtaMagazzino >= 0 AND StockAnticipato >= 0", name="ck_cons_art_qta"),
    )

class ConsuntivoProduzioneOrdine(db.Model):
    __tablename__ = "consuntivi_produzione_ordini"
    TipoPiano: Mapped[str] = mapped_column(String(5), primary_key=True)
    IdPiano: Mapped[int] = mapped_column(primary_key=True)
    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key=True)
    CodArt: Mapped[str] = mapped_column(String(100), nullable=False)
    QtaPianificataSnapshot: Mapped[int] = mapped_column(nullable=False)
    QtaProdotta: Mapped[int] = mapped_column(nullable=False)
    LottoSnapshot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    __table_args__ = (
        CheckConstraint("TipoPiano IN ('TRAD','MISTA')", name="ck_cons_ord_tipo"),
        CheckConstraint("QtaProdotta >= 0 AND QtaPianificataSnapshot > 0", name="ck_cons_ord_qta"),
        Index("ix_cons_ord_erp", "IdDocumento", "IdRigaDoc"),
    )

class GiacenzaSecondaScelta(db.Model):
    __tablename__ = "giacenze_seconda_scelta"
    CodArt: Mapped[str] = mapped_column(String(100), primary_key=True)
    Quantita: Mapped[int] = mapped_column(nullable=False, default=0)
    __table_args__ = (CheckConstraint("Quantita >= 0", name="ck_giacenza_seconda_nonneg"),)

class MovimentoStockConsuntivo(db.Model):
    __tablename__ = "movimenti_stock_consuntivi"
    TipoPiano: Mapped[str] = mapped_column(String(5), primary_key=True)
    IdPiano: Mapped[int] = mapped_column(primary_key=True)
    CodArt: Mapped[str] = mapped_column(String(100), primary_key=True)
    TipoStock: Mapped[str] = mapped_column(String(8), primary_key=True)
    Variazione: Mapped[int] = mapped_column(nullable=False)
    Operatore: Mapped[str] = mapped_column(String(120), nullable=False)
    RegistratoIl: Mapped[datetime] = mapped_column(nullable=False)
    __table_args__ = (
        CheckConstraint("TipoPiano IN ('TRAD','MISTA')", name="ck_mov_cons_tipo"),
        CheckConstraint("TipoStock IN ('PRIMA','SECONDA')", name="ck_mov_cons_stock"),
    )
