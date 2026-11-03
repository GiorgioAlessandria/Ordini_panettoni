from datetime import datetime
from time import sleep
from typing import Any
from zoneinfo import ZoneInfo

import dateutil.utils
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    AnagraficaClienti,
    LogOperazioni,
    OrdiniCliente,
    StatoRigaOrdine,
)
from sync.models_sync import VwESArticoli, VwESClientiFornitori, VwESOrdiniClienteAperti
from pathlib import Path
from runpy import run_path
from sqlalchemy.engine import URL
import logging
from logging.handlers import RotatingFileHandler
from time import monotonic
BASE_DIR = Path(r"C:\ProgramData\OrdiniPanettoni")
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents = True, exist_ok = True)

logger = logging.getLogger("ordini_panettoni.sync")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    handler = RotatingFileHandler(
            LOG_DIR / "sync.log",
            maxBytes = 5 * 1024 * 1024,
            backupCount = 5,
            encoding = "utf-8",
            )

    handler.setFormatter(
            logging.Formatter(
                    "%(asctime)s | %(levelname)-8s | %(message)s",
                    datefmt = "%Y-%m-%d %H:%M:%S",
                    )
            )

    logger.addHandler(handler)
config = run_path(
    str(BASE_DIR / "config" / "production.py")
)

SQLALCHEMY_DATABASE_URI = config["SQLALCHEMY_DATABASE_URI"]

sql_params = (
    BASE_DIR / "config" / "sqlserver_odbc.txt"
).read_text(encoding="utf-8").strip()
TimeZone = "Europe/Rome"

try:
    sqlserver_engine_app = create_engine(
    URL.create(
        "mssql+pyodbc",
        query={"odbc_connect": sql_params}
    ),
    pool_pre_ping=True,)

    with sqlserver_engine_app.connect() as conn:
        conn.exec_driver_sql("SELECT 1")

    logger.info("Connessione SQL Server verificata")
    SessionSQLServer = sessionmaker(bind=sqlserver_engine_app)
except Exception:
    logger.exception("Errore connessione SQL Server")
    raise

try:
    sqlite_engine_app = create_engine(SQLALCHEMY_DATABASE_URI, connect_args={"timeout":30})
    SessionSqlite = sessionmaker(bind=sqlite_engine_app)
    logger.info("Connessione SQLite verificata")
except Exception:
    logger.exception("Errore connessione SQLite")
    raise


@event.listens_for(sqlite_engine_app, "connect")
def enable_sqlite_foreign_keys(
    dbapi_connection,
    connection_record,
):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def leggi_view(
    table_lette_name: type[VwESArticoli]
    | type[VwESClientiFornitori]
    | type[VwESOrdiniClienteAperti],
    session_lettura: sessionmaker,
):
    stmt = select(table_lette_name)
    with session_lettura() as sess:
        table_lette = sess.scalars(stmt).all()
        return table_lette


def articoli(table_articoli: list[VwESArticoli], sync_time: datetime) -> list[dict[str, Any]]:
    import re

    articoli_filtrati = {}
    for articolo in table_articoli:
        if articolo.CodFamiglia == "PNT":
            glassatura = 1 if articolo.CodArt.endswith("G") else 0
            match = re.search(r"\d{3,4}", articolo.CodArt)
            if match is None:
                continue
            prezzatura = int(match.group())
            articolo.Glassatura = glassatura
            articolo.Prezzatura = prezzatura
            articoli_filtrati[articolo.CodArt] = articolo
    articoli_elaborati = [
        {
            "CodArt": articolo.CodArt,
            "DesArt": articolo.DesArt,
            "CodFamiglia": articolo.CodFamiglia,
            "Prezzatura": articolo.Prezzatura,
            "Glassatura": articolo.Glassatura,
            "synced_at": sync_time,
        }
        for articolo in articoli_filtrati.values()
    ]
    return articoli_elaborati


def ordini(
    table_ordini: list[VwESOrdiniClienteAperti],
    table_articoli: list[dict[str, Any]],
    sync_time: datetime,
) -> list[dict[str, Any]]:

    codici_articoli = {articolo["CodArt"] for articolo in table_articoli}

    ordini_filtrati = {
        (ordine.IdDocumento, ordine.IdRigaDoc): ordine
        for ordine in table_ordini
        if ordine.CodArt in codici_articoli
    }
    ordini_elaborati = [
        {
            "IdDocumento": int(ordine.IdDocumento),
            "IdRigaDoc": int(ordine.IdRigaDoc),
            "CodTipoDoc": ordine.CodTipoDoc,
            "DataRegistrazione": ordine.DataRegistrazione,
            "CodSerie": ordine.CodSerie,
            "NumRegistraz": ordine.NumRegistraz,
            "NumDocOriginale": ordine.NumDocOriginale,
            "DataOriginale": ordine.DataOriginale,
            "CodCliFor": ordine.CodCliFor,
            "TipoRigaDoc": ordine.TipoRigaDoc,
            "CodArt": ordine.CodArt,
            "DesArt": ordine.DesArt,
            "DesEstesa": ordine.DesEstesa,
            "DataConsegna": ordine.DataConsegna,
            "UmDoc": ordine.UmDoc,
            "QTA_ORD": int(ordine.QTA_ORD),
            "QTA_CONS": int(ordine.QTA_CONS),
            "QTA_SALDO_DOC": int(ordine.QTA_SALDO_DOC),
            "Commento_Riga_Saldata": ordine.Commento_Riga_Saldata,
            "NotaInterna": ordine.NotaInterna,
            "synced_at": sync_time,
        }
        for ordine in ordini_filtrati.values()
    ]
    return ordini_elaborati


def clienti(
    table_clienti: list[VwESClientiFornitori],
    table_ordini: list[dict[str, Any]],
    sync_time: datetime,
) -> list[dict[str, Any]]:

    codici_clienti = {ordine["CodCliFor"] for ordine in table_ordini}

    clienti_filtrati = {
        cliente.CodCliFor: cliente
        for cliente in table_clienti
        if cliente.TipoAnagrafica == 1 and cliente.CodCliFor in codici_clienti
    }
    clienti_elaborati = [
        {
            "TipoAnagrafica": cliente.TipoAnagrafica,
            "CodCliFor": cliente.CodCliFor,
            "RagioneSociale": cliente.RagioneSociale,
            "Indirizzo": cliente.Indirizzo,
            "Cap": cliente.Cap,
            "Localita": cliente.Localita,
            "Provincia": cliente.Provincia,
            "CodStato": cliente.CodStato,
            "synced_at": sync_time,
        }
        for cliente in clienti_filtrati.values()
    ]

    return clienti_elaborati


def log_ordini(ordini_nuovi: list[dict[str, Any]], sync_time: datetime):
    elaborazione = [
        {
            "IdDocumento": ordine["IdDocumento"],
            "IdRigaDoc": ordine["IdRigaDoc"],
            "Stato": "Nuovo",
            "Operatore": "sync",
            "synced_at": sync_time,
        }
        for ordine in ordini_nuovi
    ]
    return elaborazione


def stato_riga(ordini_nuovi: list[dict[str, Any]], sync_time: datetime):
    elaborazione = [
        {
            "IdDocumento": ordine["IdDocumento"],
            "IdRigaDoc": ordine["IdRigaDoc"],
            "Stato": "Nuovo",
            "Operatore": "sync",
            "DataRegistrazione": dateutil.utils.today(tzinfo=ZoneInfo(TimeZone)),
            "synced_at": sync_time,
        }
        for ordine in ordini_nuovi
    ]
    return elaborazione


# Lo stato viene conservato soltanto nel processo di sync.
# Evita di ripetere la stessa segnalazione ogni 30 secondi.
_ultimi_ordini_assenti_erp: frozenset[tuple[int, int]] | None = None


def rileva_ordini_assenti_erp(
    chiavi_sqlite: set[tuple[int, int]],
    ordini_erp: list[dict[str, Any]],
) -> frozenset[tuple[int, int]]:
    """Ordini presenti localmente ma assenti dall'ultimo feed ERP.

    Il risultato e' SOLO diagnostico: non implica che l'ordine sia eliminabile.
    Ordini pianificati, note e storico potrebbero ancora dipendere dal record.
    """
    chiavi_erp = {
        (ordine["IdDocumento"], ordine["IdRigaDoc"])
        for ordine in ordini_erp
    }
    return frozenset(chiavi_sqlite - chiavi_erp)


def segnala_ordini_assenti_erp(
    ordini_assenti: frozenset[tuple[int, int]],
) -> None:
    """Segnala una variazione senza spammare il log a ogni ciclo."""
    global _ultimi_ordini_assenti_erp
    if ordini_assenti == _ultimi_ordini_assenti_erp:
        return

    if ordini_assenti:
        primi = sorted(ordini_assenti)[:10]
        logger.warning(
            "Ordini presenti in SQLite ma assenti dal feed ERP: %d | "
            "Prime chiavi: %s | Nessuna eliminazione automatica "
            "(verificare pianificazioni, stati, note e storico).",
            len(ordini_assenti),
            primi,
        )
    elif _ultimi_ordini_assenti_erp:
        logger.info("Rientrati tutti gli ordini prima assenti dal feed ERP.")

    _ultimi_ordini_assenti_erp = ordini_assenti


def main():
    inizio = monotonic()
    logger.info("Inizio sincronizzazione SQL Server -> SQLite")
    ordini_esistenti: set[tuple[int, int]] = set()
    try:
        with SessionSqlite() as sess:
            ordini_esistenti = set(
                sess.execute(select(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc)).all()
            )
    except Exception:
        logger.exception("Errore durante la lettura degli ordini SQLite")
        raise
    articoli_raw = leggi_view(VwESArticoli, SessionSQLServer)
    ordini_raw = leggi_view(VwESOrdiniClienteAperti, SessionSQLServer)
    clienti_raw = leggi_view(VwESClientiFornitori, SessionSQLServer)

    sync_time = datetime.now(ZoneInfo(TimeZone))
    articoli_elaborati = articoli(articoli_raw, sync_time)
    ordini_elaborati = ordini(ordini_raw, articoli_elaborati, sync_time)
    clienti_elaborati = clienti(clienti_raw, ordini_elaborati, sync_time)
    logger.info(
            "Dati elaborati | Articoli: %d | Ordini: %d | Clienti: %d",
            len(articoli_elaborati),
            len(ordini_elaborati),
            len(clienti_elaborati),
            )
    # La lista ERP e' gia' filtrata per gli articoli PNT gestiti.
    # Confrontiamo i codici, ma non cancelliamo i record con storia locale.
    ordini_assenti_erp = rileva_ordini_assenti_erp(
        ordini_esistenti, ordini_elaborati
    )

    ordini_nuovi = [
        ordine
        for ordine in ordini_elaborati
        if (
            ordine["IdDocumento"],
            ordine["IdRigaDoc"],
        )
        not in ordini_esistenti
    ]
    ordini_nuovi_log = log_ordini(ordini_nuovi, sync_time)
    ordini_stato_riga = stato_riga(ordini_nuovi, sync_time)
    logger.info("Nuovi ordini individuati: %d", len(ordini_nuovi))

    if not articoli_elaborati:
        raise RuntimeError(
            "Sync interrotto: nessun articolo trovato."
        )

    if not ordini_elaborati:
        raise RuntimeError(
            "Sync interrotto: nessun ordine trovato."
        )

    if not clienti_elaborati:
        raise RuntimeError(
            "Sync interrotto: nessun cliente trovato."
        )

    stmt_articoli_upsert = insert(AnagraficaArticoli).values(articoli_elaborati)
    stmt_articoli_upsert = stmt_articoli_upsert.on_conflict_do_update(
        index_elements=["CodArt"],
        set_={
            "DesArt": stmt_articoli_upsert.excluded.DesArt,
            "CodFamiglia": stmt_articoli_upsert.excluded.CodFamiglia,
            "Prezzatura": stmt_articoli_upsert.excluded.Prezzatura,
            "Glassatura": stmt_articoli_upsert.excluded.Glassatura,
            "synced_at": stmt_articoli_upsert.excluded.synced_at,
        },
    )

    stmt_ordini_upsert = insert(OrdiniCliente).values(ordini_elaborati)
    stmt_ordini_upsert = stmt_ordini_upsert.on_conflict_do_update(
        index_elements=["IdDocumento", "IdRigaDoc"],
        set_={
            "CodTipoDoc": stmt_ordini_upsert.excluded.CodTipoDoc,
            "DataRegistrazione": stmt_ordini_upsert.excluded.DataRegistrazione,
            "CodSerie": stmt_ordini_upsert.excluded.CodSerie,
            "NumRegistraz": stmt_ordini_upsert.excluded.NumRegistraz,
            "NumDocOriginale": stmt_ordini_upsert.excluded.NumDocOriginale,
            "DataOriginale": stmt_ordini_upsert.excluded.DataOriginale,
            "CodCliFor": stmt_ordini_upsert.excluded.CodCliFor,
            "TipoRigaDoc": stmt_ordini_upsert.excluded.TipoRigaDoc,
            "CodArt": stmt_ordini_upsert.excluded.CodArt,
            "DesArt": stmt_ordini_upsert.excluded.DesArt,
            "DesEstesa": stmt_ordini_upsert.excluded.DesEstesa,
            "DataConsegna": stmt_ordini_upsert.excluded.DataConsegna,
            "UmDoc": stmt_ordini_upsert.excluded.UmDoc,
            "QTA_ORD": stmt_ordini_upsert.excluded.QTA_ORD,
            "QTA_CONS": stmt_ordini_upsert.excluded.QTA_CONS,
            "QTA_SALDO_DOC": stmt_ordini_upsert.excluded.QTA_SALDO_DOC,
            "Commento_Riga_Saldata": stmt_ordini_upsert.excluded.Commento_Riga_Saldata,
            "NotaInterna": stmt_ordini_upsert.excluded.NotaInterna,
            "synced_at": stmt_ordini_upsert.excluded.synced_at,
        },
    )

    stmt_clienti_upsert = insert(AnagraficaClienti).values(clienti_elaborati)
    stmt_clienti_upsert = stmt_clienti_upsert.on_conflict_do_update(
        index_elements=["CodCliFor"],
        set_={
            "TipoAnagrafica": stmt_clienti_upsert.excluded.TipoAnagrafica,
            "RagioneSociale": stmt_clienti_upsert.excluded.RagioneSociale,
            "Indirizzo": stmt_clienti_upsert.excluded.Indirizzo,
            "Cap": stmt_clienti_upsert.excluded.Cap,
            "Localita": stmt_clienti_upsert.excluded.Localita,
            "Provincia": stmt_clienti_upsert.excluded.Provincia,
            "CodStato": stmt_clienti_upsert.excluded.CodStato,
            "synced_at": stmt_clienti_upsert.excluded.synced_at,
        },
    )

    stmt_log_operazioni_upsert = insert(LogOperazioni).values(ordini_nuovi_log)
    stmt_stato_riga_upsert = insert(StatoRigaOrdine).values(ordini_stato_riga)
    stmt_stato_riga_upsert = stmt_stato_riga_upsert.on_conflict_do_nothing(
        index_elements=["IdDocumento", "IdRigaDoc"]
    )

    try:
        with SessionSqlite() as sess:
            sess.execute(stmt_clienti_upsert)
            sess.execute(stmt_articoli_upsert)
            sess.execute(stmt_ordini_upsert)
            if ordini_nuovi_log:
                sess.execute(stmt_log_operazioni_upsert)
            if ordini_stato_riga:
                sess.execute(stmt_stato_riga_upsert)
            sess.commit()
            # Segnaliamo solo dopo un commit effettivamente riuscito.
            segnala_ordini_assenti_erp(ordini_assenti_erp)
            durata = monotonic()-inizio
            logger.info(
                    "Sincronizzazione completata | "
                    "Articoli: %d | Ordini: %d | Clienti: %d | "
                    "Nuovi ordini: %d | Durata: %.2f secondi",
                    len(articoli_elaborati),
                    len(ordini_elaborati),
                    len(clienti_elaborati),
                    len(ordini_nuovi),
                    durata,
                    )
            durata = monotonic() - inizio
    except IntegrityError:
        logger.exception("Errore di integrità durante il salvataggio SQLite")
        raise


if __name__ == "__main__":
    counter = 0

    while True:
        try:
            main()
        except Exception:
            logger.exception(
                    "Sincronizzazione fallita | Ciclo: %d",
                    counter,
                    )
        finally:
            counter += 1
            sleep(30)
