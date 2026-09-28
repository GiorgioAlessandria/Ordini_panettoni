from datetime import date, datetime

from sqlalchemy.orm import Mapped, mapped_column

from flaskr.extensions import db


class VwESArticoli(db.Model):
    __tablename__ = "vwESArticoli"
    CodArt: Mapped[str] = mapped_column(primary_key = True)
    DesArt: Mapped[str]
    GestioneLotto: Mapped[str]
    GestioneMatricola: Mapped[str]
    GestioneQualità: Mapped[str]
    CodFamiglia: Mapped[str]
    CodClassifTecnica: Mapped[str]
    MagUM: Mapped[str]
    TecniciUm: Mapped[str]
    TecniciCoeffUmDen: Mapped[int]
    TecniciCoeffUmNum: Mapped[int]
    IndiceModifica: Mapped[str]
    PuntoRiordino: Mapped[int]
    LottoRiordino: Mapped[int]
    PianTempoApprovFisso: Mapped[int]


class VwESOrdiniClienteAperti(db.Model):
    __tablename__ = "vwESOrdiniClienteAperti"
    IdDocumento: Mapped[int] = mapped_column(primary_key = True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key = True)
    CodTipoDoc: Mapped[int]
    DataRegistrazione: Mapped[date]
    CodSerie: Mapped[int]
    NumRegistraz: Mapped[int]
    NumDocOriginale: Mapped[str]
    DataOriginale: Mapped[date]
    CodCliFor: Mapped[int]
    TipoRigaDoc: Mapped[int]
    CodArt: Mapped[str]
    DesArt: Mapped[str]
    DesEstesa: Mapped[str]
    DataConsegna: Mapped[datetime]
    UmDoc: Mapped[str]
    QTA_ORD: Mapped[int]
    QTA_CONS: Mapped[int]
    QTA_SALDO_DOC: Mapped[int]
    Commento_Riga_Saldata: Mapped[str]
    NotaInterna: Mapped[str]


class VwESClientiFornitori(db.Model):
    __tablename__ = "vwESClientiFornitori"
    TipoAnagrafica: Mapped[int]
    CodCliFor: Mapped[int] = mapped_column(primary_key = True)
    RagioneSociale: Mapped[str]
    Indirizzo: Mapped[str]
    Cap: Mapped[str]
    Localita: Mapped[str]
    Provincia: Mapped[str]
    CodStato: Mapped[str]
