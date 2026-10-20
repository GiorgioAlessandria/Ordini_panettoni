"""Ruoli e note vendite locali: nessuna modifica ai dati sincronizzati ERP."""
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db

RUOLI = ("admin", "utente_produzione", "utente_vendite")

class RuoloUtente(db.Model):
    __tablename__ = "ruoli_utente"
    # Senza FK al modello User: versioni del progetto hanno 'users' oppure 'user'.
    UserId: Mapped[int] = mapped_column(primary_key=True)
    Ruolo: Mapped[str] = mapped_column(String(32), nullable=False)
    __table_args__ = (CheckConstraint(
        "Ruolo IN ('admin', 'utente_produzione', 'utente_vendite')",
        name="ck_ruoli_utente_ruolo"),)

class NotaVenditeOrdine(db.Model):
    __tablename__ = "note_vendite_ordine"
    Id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdDocumento: Mapped[int] = mapped_column(nullable=False)
    IdRigaDoc: Mapped[int] = mapped_column(nullable=False)
    AutoreId: Mapped[int] = mapped_column(nullable=False)
    AutoreNome: Mapped[str] = mapped_column(String(120), nullable=False)
    Testo: Mapped[str] = mapped_column(Text, nullable=False)
    CreatoIl: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), nullable=False)
    ModificatoIl: Mapped[datetime | None] = mapped_column(nullable=True)
    __table_args__ = (
        Index("ix_note_vendite_ordine_identificativo", "IdDocumento", "IdRigaDoc"),
    )
