CREATE TABLE Anagrafica_clienti
(
    TipoAnagrafica INTEGER             NOT NULL,
    CodCliFor      INTEGER PRIMARY KEY NOT NULL,
    RagioneSociale TEXT                NOT NULL,
    Indirizzo      TEXT                NOT NULL,
    Cap            INTEGER             NOT NULL,
    Localita       TEXT                NOT NULL,
    Provincia      TEXT                NOT NULL,
    CodStato       TEXT                NOT NULL,
    synced_at      TEXT                NOT NULL
);
CREATE TABLE Ordini_cliente
(
    IdDocumento           INTEGER        NOT NULL,
    IdRigaDoc             INTEGER        NOT NULL,
    CodTipoDoc            INTEGER        NOT NULL,
    DataRegistrazione     TEXT           NOT NULL,
    CodSerie              INTEGER        NOT NULL,
    NumRegistraz          INTEGER        NOT NULL,
    NumDocOriginale       TEXT           NOT NULL,
    DataOriginale         TEXT           NOT NULL,
    CodCliFor             INTEGER        NOT NULL,
    TipoRigaDoc           INTEGER        NOT NULL,
    CodArt                TEXT           NOT NULL,
    DesArt                TEXT           NOT NULL,
    DesEstesa             TEXT           NOT NULL,
    DataConsegna          TEXT           NOT NULL,
    UmDoc                 TEXT           NOT NULL,
    QTA_ORD               INTEGER        NOT NULL,
    QTA_CONS              INTEGER        NOT NULL,
    QTA_SALDO_DOC         INTEGER        NOT NULL,
    Commento_Riga_Saldata TEXT           NOT NULL,
    NotaInterna           synced_at TEXT NOT NULL,
    FOREIGN KEY (CodCliFor) REFERENCES Anagrafica_clienti (CodCliFor)
)