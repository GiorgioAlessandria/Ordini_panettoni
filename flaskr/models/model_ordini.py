from datetime import date, datetime

from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flaskr.extensions import db


class OrdiniCliente(db.Model):
    __tablename__ = 'ordini_cliente_aperti'
    IdDocumento: Mapped[int] = mapped_column(primary_key = True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key = True)
    CodTipoDoc: Mapped[int]
    DataRegistrazione: Mapped[date]
    CodSerie: Mapped[int]
    NumRegistraz: Mapped[int]
    NumDocOriginale: Mapped[str]
    DataOriginale: Mapped[date]
    CodCliFor: Mapped[int] = mapped_column(ForeignKey("clienti_fornitori.CodCliFor"))
    Cliente: Mapped["AnagraficaClienti"] = relationship(back_populates = "ordini")
    TipoRigaDoc: Mapped[int]
    CodArt: Mapped[str] = mapped_column(ForeignKey("articoli.CodArt"))
    DesArt: Mapped[str]
    DesEstesa: Mapped[str]
    DataConsegna: Mapped[datetime]
    UmDoc: Mapped[str]
    QTA_ORD: Mapped[int]
    QTA_CONS: Mapped[int]
    QTA_SALDO_DOC: Mapped[int]
    Commento_Riga_Saldata: Mapped[str]
    NotaInterna: Mapped[str]
    synced_at: Mapped[datetime]
    stato: Mapped["StatoRigaOrdine"] = relationship(back_populates = "ordine", uselist = False)
    articolo: Mapped["AnagraficaArticoli"] = relationship(back_populates = "ordini", uselist = False)
    log: Mapped[list["LogOperazioni"]] = relationship(back_populates = "ordine", uselist = True)


class AnagraficaClienti(db.Model):
    __tablename__ = 'clienti_fornitori'
    TipoAnagrafica: Mapped[int]
    CodCliFor: Mapped[int] = mapped_column(primary_key = True)
    RagioneSociale: Mapped[str]
    Indirizzo: Mapped[str]
    Cap: Mapped[str]
    Localita: Mapped[str]
    Provincia: Mapped[str]
    CodStato: Mapped[str]
    synced_at: Mapped[datetime]
    ordini: Mapped[list["OrdiniCliente"]] = relationship(back_populates = "Cliente", uselist = True)


class AnagraficaArticoli(db.Model):
    __tablename__ = 'articoli'
    CodArt: Mapped[str] = mapped_column(primary_key = True)
    DesArt: Mapped[str]
    CodFamiglia: Mapped[str]
    Prezzatura: Mapped[int]
    Glassatura: Mapped[bool]
    synced_at: Mapped[datetime]
    ordini: Mapped[list[OrdiniCliente]] = relationship(back_populates = "articolo", uselist = True)


class StatoRigaOrdine(db.Model):
    __tablename__ = 'stato_riga_ordine'
    IdDocumento: Mapped[int] = mapped_column(primary_key = True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key = True)
    Stato: Mapped[str]
    Operatore: Mapped[str]
    DataRegistrazione: Mapped[date]
    synced_at: Mapped[datetime]
    ordine: Mapped[OrdiniCliente] = relationship(back_populates = "stato", uselist = False)
    __table_args__ = (ForeignKeyConstraint(
            ["IdDocumento", "IdRigaDoc"],
            [
                "ordini_cliente_aperti.IdDocumento",
                "ordini_cliente_aperti.IdRigaDoc"
                ]
            , ondelete = "CASCADE"),)


class LogOperazioni(db.Model):
    __tablename__ = 'log_operazioni'
    id: Mapped[int] = mapped_column(primary_key = True, autoincrement = True)
    IdDocumento: Mapped[int]
    IdRigaDoc: Mapped[int]
    Stato: Mapped[str]
    Operatore: Mapped[str]
    synced_at: Mapped[datetime]
    ordine: Mapped[OrdiniCliente] = relationship(back_populates = "log")
    __table_args__ = (ForeignKeyConstraint(
            ["IdDocumento", "IdRigaDoc"],
            [
                "ordini_cliente_aperti.IdDocumento",
                "ordini_cliente_aperti.IdRigaDoc"
                ]
            ),)


class AnagraficaImpastatrici(db.Model):
    __tablename__ = 'anagrafica_impastatrici'
    IdImpastatrice: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, unique=True
    )
    ModelloImpastatrice: Mapped[str]
    DescrizioneImpastatrice: Mapped[str]
    CapienzaImpastatrice: Mapped[int]
    UdM: Mapped[str]


class AnagraficaForni(db.Model):
    __tablename__ = 'anagrafica_forni'
    IdForno: Mapped[int] = mapped_column(primary_key = True, autoincrement = True)
    ModelloForno: Mapped[str]
    DescrizioneForno: Mapped[str]
    CapienzaForno: Mapped[int]
    UdM: Mapped[str]
    DimensionePanettone: Mapped[int]
    TipologiaPanettone: Mapped[str]


class Coefficienti(db.Model):
    __tablename__ = 'coefficienti'
    id: Mapped[int] = mapped_column(primary_key = True, autoincrement = True)
    Coefficiente: Mapped[str]
    Valore: Mapped[int]
    UdM: Mapped[str]


class TipologiaRicette(db.Model):
    __tablename__ = 'tipologia_ricette'
    IdRicette: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True
    )
    CodArt: Mapped[str]
    DesArt: Mapped[str]
    TempisticheLavorazione: Mapped[dict] = mapped_column(JSON)


class TipologieLavorazione(db.Model):
    __tablename__ = 'tipologia_lavorazione'
    IdLavorazione: Mapped[int] = mapped_column(primary_key = True, autoincrement = True)
    CodiceLavorazione: Mapped[str]
    DescrizioneLavorazione: Mapped[str]
    DurataLavorazione: Mapped[int]


class TipLavorazioniAnagForni(db.Model):
    __tablename__ = "tipologia_lavorazioni_anagrafica_forni"

    IdForno: Mapped[int] = mapped_column(
        ForeignKey("anagrafica_forni.IdForno"), primary_key=True
    )

    IdLavorazione: Mapped[int] = mapped_column(
        ForeignKey("tipologia_lavorazione.IdLavorazione"), primary_key=True
    )

class TipLavorazioniAnagImpastatrici(db.Model):
    __tablename__ = "tipologia_lavorazioni_anagrafica_impastatrici"

    IdImpastatrice: Mapped[int] = mapped_column(
        ForeignKey("anagrafica_impastatrici.IdImpastatrice"), primary_key=True
    )

    IdLavorazione: Mapped[int] = mapped_column(
        ForeignKey("tipologia_lavorazione.IdLavorazione"), primary_key=True
    )


class TipRicetteAnagArticoli(db.Model):
    __tablename__ = "tipologia_ricette_anagrafica_articoli"

    IdRicette: Mapped[int] = mapped_column(
        ForeignKey("tipologia_ricette.IdRicette"), primary_key=True
    )

    CodArt: Mapped[str] = mapped_column(ForeignKey("articoli.CodArt"), primary_key=True)
