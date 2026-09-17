from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from flaskr.extensions import db
class OrdiniCliente(db.Model):
    __tablename__ = 'ordini_cliente_aperti'
    IdDocumento:Mapped[int] = mapped_column(primary_key = True)
    IdRigaDoc:Mapped[int] = mapped_column(primary_key = True)
    CodTipoDoc:Mapped[int]
    DataRegistrazione:Mapped[str]
    CodSerie:Mapped[int]
    NumRegistraz:Mapped[int]
    NumDocOriginale:Mapped[str]
    DataOriginale:Mapped[str]
    CodCliFor:Mapped[int] = mapped_column(ForeignKey("clienti_fornitori.CodCliFor"))
    Cliente:Mapped["AnagraficaClienti"] = relationship(back_populates= "ordini")
    TipoRigaDoc:Mapped[int]
    CodArt:Mapped[str]
    DesArt:Mapped[str]
    DesEstesa:Mapped[str]
    DataConsegna:Mapped[str]
    UmDoc:Mapped[str]
    QTA_ORD:Mapped[int]
    QTA_CONS:Mapped[int]
    QTA_SALDO_DOC:Mapped[int]
    Commento_Riga_Saldata:Mapped[str]
    NotaInterna:Mapped[str]
    synced_at:Mapped[str]

class AnagraficaClienti(db.Model):
    __tablename__ = 'clienti_fornitori'
    TipoAnagrafica:Mapped[int]
    CodCliFor:Mapped[int] = mapped_column(primary_key=True)
    RagioneSociale:Mapped[str]
    Indirizzo:Mapped[str]
    Cap:Mapped[int]
    Localita:Mapped[str]
    Provincia:Mapped[str]
    CodStato:Mapped[str]
    synced_at:Mapped[str]
    ordini:Mapped[list["OrdiniCliente"]] = relationship(back_populates="Cliente")