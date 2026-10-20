from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Numeric, UniqueConstraint
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
    # NULL per pianificazioni antecedenti alla migrazione: il surplus storico va riconciliato.
    QtaStockGenerata: Mapped[int | None] = mapped_column(nullable=True)
    DataChiusura: Mapped[datetime | None] = mapped_column(nullable=True)
    DataAnnullamento: Mapped[datetime | None] = mapped_column(nullable=True)
    OperatoreChiusura: Mapped[str | None] = mapped_column(nullable=True)
    OperatoreAnnullamento: Mapped[str | None] = mapped_column(nullable=True)
    MotivoAnnullamento: Mapped[str | None] = mapped_column(nullable=True)
    PesoTotale: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    NumeroCotte: Mapped[int]
    PanettoniPianificati: Mapped[int]
    CodArt: Mapped[str] = mapped_column(ForeignKey("articoli.CodArt"))
    BloccoIniziale: Mapped[str]
    IdRicetta: Mapped[int] = mapped_column(ForeignKey("tipologia_ricette.IdRicetta"))
    fasi: Mapped[list["PianificazioneFasi"]] = relationship(
        back_populates="pianificazione", order_by="PianificazioneFasi.OffsetBlocchi"
    )
    __table_args__ = (
        CheckConstraint("QtaStockGenerata IS NULL OR QtaStockGenerata >= 0", name="ck_piano_stock_generato"),
        CheckConstraint("BloccoIniziale IN ('M', 'P')", name="ck_pianificazione_blocco_iniziale"),
    )


class PianificazioneOrdini(db.Model):
    __tablename__ = "pianificazione_ordini"
    IdPianificazione: Mapped[int] = mapped_column(
        ForeignKey("pianificazione_produzione.IdPianificazione"), primary_key=True
    )
    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRiga: Mapped[int] = mapped_column(primary_key=True)
    QtaPianificata: Mapped[int]
    QtaDaStock: Mapped[int] = mapped_column(server_default="0", nullable=False)
    Stato: Mapped[str] = mapped_column(server_default="In produzione")
    DataChiusura: Mapped[datetime | None]
    __table_args__ = (
        ForeignKeyConstraint(
            ["IdDocumento", "IdRiga"],
            [
                "ordini_cliente_aperti.IdDocumento",
                "ordini_cliente_aperti.IdRigaDoc",
            ],
            name="fk_pianificazione_ordini_ordine",
        ),
        CheckConstraint("QtaPianificata > 0", name="ck_qta_pianificata_positiva"),
        CheckConstraint("QtaDaStock >= 0 AND QtaDaStock <= QtaPianificata", name="ck_qta_da_stock_valida"),
        CheckConstraint(
            "Stato IN ('In produzione', 'Chiuso', 'Annullato')",
            name="ck_stato_pianificazione_ordini",
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


class PianificazioneFasi(db.Model):
    __tablename__ = "pianificazione_fasi"
    IdPianificazioneFase: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdPianificazione: Mapped[int] = mapped_column(
        ForeignKey("pianificazione_produzione.IdPianificazione")
    )
    IdRicettaFase: Mapped[int] = mapped_column(ForeignKey("ricetta_fasi.id"))
    IdLavorazione: Mapped[int] = mapped_column(ForeignKey("tipologia_lavorazione.IdLavorazione"))
    OffsetBlocchi: Mapped[int]
    DataFase: Mapped[date]
    Blocco: Mapped[str]
    pianificazione: Mapped["PianificazioneProduzione"] = relationship(back_populates="fasi")
    __table_args__ = (
        UniqueConstraint(
            "IdPianificazione", "DataFase", "Blocco", name="uq_pianificazione_data_blocco"
        ),
        UniqueConstraint(
            "IdPianificazione", "IdRicettaFase", name="uq_pianificazione_ricetta_fase"
        ),
        CheckConstraint("Blocco IN ('M', 'P')", name="ck_pianificazione_fasi_blocco"),
    )


class PianificazioneImpastatriceFase(db.Model):
    """Riserva una macchina in un singolo blocco della fase di impasto.

    La UNIQUE impedisce, anche in caso di richieste concorrenti,
    l'assegnazione della stessa macchina a due produzioni nel medesimo blocco.
    """
    __tablename__ = "pianificazione_impastatrice_fasi"
    Id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdPianificazioneFase: Mapped[int] = mapped_column(
        ForeignKey("pianificazione_fasi.IdPianificazioneFase"), nullable=False
    )
    IdImpastatrice: Mapped[int] = mapped_column(
        ForeignKey("anagrafica_impastatrici.IdImpastatrice"), nullable=False
    )
    DataFase: Mapped[date] = mapped_column(nullable=False)
    Blocco: Mapped[str] = mapped_column(nullable=False)
    KgAssegnati: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    __table_args__ = (
        UniqueConstraint("IdImpastatrice", "DataFase", "Blocco", name="uq_impastatrice_data_blocco"),
        CheckConstraint("Blocco IN ('M', 'P')", name="ck_impastatrice_fase_blocco"),
        CheckConstraint("KgAssegnati >= 0", name="ck_impastatrice_kg_non_negativi"),
    )


class PianificazioneOperazioni(db.Model):
    """Registro immutabile degli eventi di chiusura/annullamento."""
    __tablename__ = "pianificazione_operazioni"
    Id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    IdPianificazione: Mapped[int] = mapped_column(
        ForeignKey("pianificazione_produzione.IdPianificazione"), nullable=False
    )
    Azione: Mapped[str] = mapped_column(nullable=False)
    Operatore: Mapped[str] = mapped_column(nullable=False)
    DataOperazione: Mapped[datetime] = mapped_column(nullable=False)
    Motivo: Mapped[str | None] = mapped_column(nullable=True)
    StockRestituito: Mapped[int] = mapped_column(nullable=False, default=0)
    StockStornato: Mapped[int] = mapped_column(nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("Azione IN ('Chiuso','Annullato')", name="ck_piano_operazione_azione"),
    )


class OrdiniPriorita(db.Model):
    __tablename__ = "ordini_priorita"
    IdDocumento: Mapped[int] = mapped_column(primary_key=True)
    IdRigaDoc: Mapped[int] = mapped_column(primary_key=True)
    Livello: Mapped[int] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint("Livello BETWEEN 1 AND 3", name="ck_ordini_priorita_livello"),
    )
