-- Applicare su copia/staging dopo backup. Nessuna modifica alle tabelle ERP.
CREATE TABLE IF NOT EXISTS consuntivi_produzione_articoli (
 "TipoPiano" VARCHAR(5) NOT NULL CHECK ("TipoPiano" IN ('TRAD','MISTA')),
 "IdPiano" INTEGER NOT NULL, "CodArt" VARCHAR(100) NOT NULL,
 "QtaSecondaScelta" INTEGER NOT NULL CHECK ("QtaSecondaScelta" >= 0),
 "QtaMagazzino" INTEGER NOT NULL CHECK ("QtaMagazzino" >= 0),
 "StockAnticipato" INTEGER NOT NULL CHECK ("StockAnticipato" >= 0),
 "LottoSnapshot" VARCHAR(40), "Operatore" VARCHAR(120) NOT NULL,
 "CreatoIl" DATETIME NOT NULL, PRIMARY KEY ("TipoPiano", "IdPiano", "CodArt")
);
CREATE TABLE IF NOT EXISTS consuntivi_produzione_ordini (
 "TipoPiano" VARCHAR(5) NOT NULL CHECK ("TipoPiano" IN ('TRAD','MISTA')),
 "IdPiano" INTEGER NOT NULL, "IdDocumento" INTEGER NOT NULL, "IdRigaDoc" INTEGER NOT NULL,
 "CodArt" VARCHAR(100) NOT NULL,
 "QtaPianificataSnapshot" INTEGER NOT NULL CHECK ("QtaPianificataSnapshot" > 0),
 "QtaProdotta" INTEGER NOT NULL CHECK ("QtaProdotta" >= 0),
 "LottoSnapshot" VARCHAR(40),
 PRIMARY KEY ("TipoPiano", "IdPiano", "IdDocumento", "IdRigaDoc")
);
CREATE INDEX IF NOT EXISTS ix_cons_ord_erp ON consuntivi_produzione_ordini ("IdDocumento", "IdRigaDoc");
CREATE TABLE IF NOT EXISTS giacenze_seconda_scelta (
 "CodArt" VARCHAR(100) PRIMARY KEY, "Quantita" INTEGER NOT NULL CHECK ("Quantita" >= 0)
);
CREATE TABLE IF NOT EXISTS movimenti_stock_consuntivi (
 "TipoPiano" VARCHAR(5) NOT NULL CHECK ("TipoPiano" IN ('TRAD','MISTA')),
 "IdPiano" INTEGER NOT NULL, "CodArt" VARCHAR(100) NOT NULL,
 "TipoStock" VARCHAR(8) NOT NULL CHECK ("TipoStock" IN ('PRIMA','SECONDA')),
 "Variazione" INTEGER NOT NULL, "Operatore" VARCHAR(120) NOT NULL,
 "RegistratoIl" DATETIME NOT NULL,
 PRIMARY KEY ("TipoPiano", "IdPiano", "CodArt", "TipoStock")
);
