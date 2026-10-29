"""Commenti del reparto produzione per singola riga ordine cliente."""
from datetime import datetime, timezone
from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column
from flaskr.extensions import db


class NotaProduzioneOrdine(db.Model):
    __tablename__ = "note_produzione_ordine"

    Id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdDocumento: Mapped[int] = mapped_column(nullable=False)
    IdRigaDoc: Mapped[int] = mapped_column(nullable=False)
    AutoreId: Mapped[int] = mapped_column(nullable=False)
    AutoreNome: Mapped[str] = mapped_column(db.String(150), nullable=False)
    Testo: Mapped[str] = mapped_column(db.Text, nullable=False)
    CreatoIl: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    ModificatoIl: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_note_produzione_ordine_doc_riga", "IdDocumento", "IdRigaDoc"),
    )
