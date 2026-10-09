from datetime import date, datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flaskr.extensions import db


class OrdiniCliente(db.Model):
    __tablename__ = "ordini_cliente_aperti"
    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key=True)
    CodTipoDoc: Mapped[int]
    DataRegistrazione: Mapped[date]
    CodSerie: Mapped[int]
    NumRegistraz: Mapped[int]
    NumDocOriginale: Mapped[str]
    DataOriginale: Mapped[date]
    CodCliFor: Mapped[int] = mapped_column(ForeignKey("clienti_fornitori.CodCliFor"))
    Cliente: Mapped["AnagraficaClienti"] = relationship(back_populates="ordini")
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
    stato: Mapped["StatoRigaOrdine"] = relationship(back_populates="ordine", uselist=False)
    articolo: Mapped["AnagraficaArticoli"] = relationship(back_populates="ordini", uselist=False)


class AnagraficaClienti(db.Model):
    __tablename__ = "clienti_fornitori"
    TipoAnagrafica: Mapped[int]
    CodCliFor: Mapped[int] = mapped_column(primary_key=True)
    RagioneSociale: Mapped[str]
    Indirizzo: Mapped[str]
    Cap: Mapped[str]
    Localita: Mapped[str]
    Provincia: Mapped[str]
    CodStato: Mapped[str]
    synced_at: Mapped[datetime]
    ordini: Mapped[list["OrdiniCliente"]] = relationship(back_populates="Cliente", uselist=True)


class AnagraficaArticoli(db.Model):
    __tablename__ = "articoli"
    CodArt: Mapped[str] = mapped_column(primary_key=True)
    DesArt: Mapped[str]
    CodFamiglia: Mapped[str]
    Prezzatura: Mapped[int]
    Glassatura: Mapped[int]
    synced_at: Mapped[datetime]
    ordini: Mapped[list["OrdiniCliente"]] = relationship(back_populates="articolo", uselist=True)
    stock: Mapped["Stock"] = relationship(back_populates="articolo", uselist=False)


class StatoRigaOrdine(db.Model):
    __tablename__ = "stato_riga_ordine"
    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key=True)
    Stato: Mapped[str]
    Operatore: Mapped[str]
    DataRegistrazione: Mapped[date]
    synced_at: Mapped[datetime]
    ordine: Mapped[OrdiniCliente] = relationship(back_populates="stato", uselist=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["IdDocumento", "IdRigaDoc"],
            ["ordini_cliente_aperti.IdDocumento", "ordini_cliente_aperti.IdRigaDoc"],
            name="fk_stato_riga_ordine_ordini_cliente_aperti",
            ondelete="CASCADE",
        ),
    )


class LogOperazioni(db.Model):
    __tablename__ = "log_operazioni"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdDocumento: Mapped[int]
    IdRigaDoc: Mapped[int]
    Stato: Mapped[str]
    Operatore: Mapped[str]
    synced_at: Mapped[datetime]


class AnagraficaImpastatrici(db.Model):
    __tablename__ = "anagrafica_impastatrici"
    IdImpastatrice: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ModelloImpastatrice: Mapped[str]
    DescrizioneImpastatrice: Mapped[str]
    CapienzaImpastatrice: Mapped[int]
    UdM: Mapped[str]


class AnagraficaForni(db.Model):
    __tablename__ = "anagrafica_forni"
    IdForno: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ModelloForno: Mapped[str]
    DescrizioneForno: Mapped[str]
    capacita: Mapped[list["CapacitaForno"]] = relationship(back_populates="forno")


class CapacitaForno(db.Model):
    __tablename__ = "capacita_forni"
    IdForno: Mapped[int] = mapped_column(ForeignKey("anagrafica_forni.IdForno"), primary_key=True)
    DimensionePanettone: Mapped[int] = mapped_column(primary_key=True)
    TipologiaPanettone: Mapped[str] = mapped_column(primary_key=True)
    Capienza: Mapped[int]
    forno: Mapped["AnagraficaForni"] = relationship(back_populates="capacita")


class Coefficienti(db.Model):
    __tablename__ = "coefficienti"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    Coefficiente: Mapped[str]
    Valore: Mapped[int]
    UdM: Mapped[str]


class TipologiaRicette(db.Model):
    __tablename__ = "tipologia_ricette"
    IdRicetta: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    Ricetta: Mapped[str]
    Descrizione: Mapped[str]
    Standard: Mapped[int]
    fasi: Mapped[list["RicettaFasi"]] = relationship(
        back_populates="ricetta", uselist=True, order_by="RicettaFasi.IdFase"
    )


class RicettaFasi(db.Model):
    __tablename__ = "ricetta_fasi"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdFase: Mapped[int]
    IdRicetta: Mapped[int] = mapped_column(ForeignKey("tipologia_ricette.IdRicetta"))
    IdLavorazione: Mapped[int] = mapped_column(ForeignKey("tipologia_lavorazione.IdLavorazione"))
    OffsetBlocchi: Mapped[int]
    CapacitaMax: Mapped[int]
    ricetta: Mapped["TipologiaRicette"] = relationship(back_populates="fasi")
    __table_args__ = (UniqueConstraint("IdRicetta", "IdFase", name="uq_ricetta_fase"),)
    lavorazione: Mapped["TipologieLavorazione"] = relationship(back_populates="fasi")


class TipologieLavorazione(db.Model):
    __tablename__ = "tipologia_lavorazione"
    IdLavorazione: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    CodiceLavorazione: Mapped[str]
    DescrizioneLavorazione: Mapped[str]
    DurataBlocchi: Mapped[int]
    TipologiaLavorazione: Mapped[str]
    RichiedeOperatore: Mapped[int]
    fasi: Mapped[list["RicettaFasi"]] = relationship(back_populates="lavorazione")


class TipLavorazioniAnagForni(db.Model):
    __tablename__ = "tipologia_lavorazioni_anagrafica_forni"

    IdForno: Mapped[int] = mapped_column(ForeignKey("anagrafica_forni.IdForno"), primary_key=True)

    IdLavorazione: Mapped[int] = mapped_column(
        ForeignKey("tipologia_lavorazione.IdLavorazione"), primary_key=True
    )


class TipRicetteAnagArticoli(db.Model):
    __tablename__ = "tipologia_ricette_anagrafica_articoli"
    IdRicetta: Mapped[int] = mapped_column(
        ForeignKey("tipologia_ricette.IdRicetta"), primary_key=True
    )
    CodArt: Mapped[str] = mapped_column(ForeignKey("articoli.CodArt"), primary_key=True)


class Stock(db.Model):
    __tablename__ = "stock"
    CodArt: Mapped[str] = mapped_column(ForeignKey("articoli.CodArt"), primary_key=True)
    Quantita: Mapped[int]
    articolo: Mapped[AnagraficaArticoli] = relationship(back_populates="stock", uselist=False)


class Costanti(db.Model):
    __tablename__ = "costanti"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    Descrizione: Mapped[str]
    Valore: Mapped[int]


class PianificazioneProduzione(db.Model):
    __tablename__ = "pianificazione_produzione"
    IdPianificazione: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    DataProduzione: Mapped[date]
    IdForno: Mapped[int] = mapped_column(ForeignKey("anagrafica_forni.IdForno"))
    TipologiaPanettone: Mapped[str]
    StatoRigaOrdine: Mapped[str]
    PesoTotale: Mapped[int]
    NumeroCotte: Mapped[int]


class PianificazioneOrdini(db.Model):
    __tablename__ = "pianificazione_ordini"
    IdPianificazione: Mapped[int] = mapped_column(
        ForeignKey("pianificazione_produzione.IdPianificazione"), primary_key=True
    )
    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRiga: Mapped[int] = mapped_column(primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["IdDocumento", "IdRiga"],
            [
                "ordini_cliente_aperti.IdDocumento",
                "ordini_cliente_aperti.IdRigaDoc",
            ],
            name="fk_pianificazione_ordini_ordine",
        ),
    )


class PianificazioneOrdiniImpastatrici(db.Model):
    __tablename__ = "pianificazione_ordini_impastatrici"
    IdPianificazione: Mapped[int] = mapped_column(
        ForeignKey("pianificazione_produzione.IdPianificazione"), primary_key=True
    )
    IdImpastatrice: Mapped[int] = mapped_column(
        ForeignKey("anagrafica_impastatrici.IdImpastatrice"), primary_key=True
    )
