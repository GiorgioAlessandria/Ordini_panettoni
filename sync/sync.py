from datetime import datetime
from time import sleep
from typing import Any
from zoneinfo import ZoneInfo

import dateutil.utils
import sqlalchemy
from sqlalchemy.orm.sync import update

from models_sync import VwESArticoli, VwESClientiFornitori, VwESOrdiniClienteAperti
from sqlalchemy import create_engine, exc, select, delete, tuple_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event

from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    AnagraficaClienti,
    OrdiniCliente,
    LogOperazioni,
    StatoRigaOrdine,
)

sql_params: str = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=SERVERSPRING02;DATABASE=BernardiProd;UID=Produzione;PWD=Produzione2025.;Encrypt=no;TrustServerCertificate=no;Pooling=no;MultipleActiveResultSets=False;"
TimeZone = "Europe/Rome"

try:
    sqlserver_engine_app = create_engine(
        f"mssql+pyodbc:///?odbc_connect={sql_params}", echo=False)
    print("SQLServer connected")
    SessionSQLServer = sessionmaker(bind=sqlserver_engine_app)
    print("SQLServer session created")
except Exception as exc:
    print(f"Errore connessione SQL Server: {exc}")
    raise

try:
    sqlite_engine_app = create_engine("sqlite://///Serverspring02/PythonDB/Ordini_panettoni/instance/db_ordini.sqlite3")
    print("Sqlite connected")
    SessionSqlite = sessionmaker(bind = sqlite_engine_app)
    print("Sqlite session created")
except Exception as exc:
    print(f"Errore connessione Sqlite: {exc}")
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
    table_lette_name: type[VwESArticoli]| type[VwESClientiFornitori]| type[VwESOrdiniClienteAperti],
    session_lettura: sessionmaker,
):
    stmt = select(table_lette_name)
    with session_lettura() as sess:
        table_lette = sess.scalars(stmt).all()
        return table_lette


def articoli(table_articoli: list[VwESArticoli],
             sync_time: datetime
) -> list[dict[str, Any]]:
    import re
    articoli_filtrati = {}
    for articolo in table_articoli:
        if articolo.CodFamiglia == "PNT" and (articolo.CodArt.startswith("PNT") or articolo.CodArt.startswith("CLB")):
            glassatura = articolo.CodArt.endswith("G")
            match = re.search(r"\d{3,4}", articolo.CodArt)
            if match is None:
                continue
            prezzatura = str(match.group())
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
    sync_time:datetime
) -> list[dict[str, Any]]:

    codici_articoli = {articolo["CodArt"] for articolo in table_articoli}

    ordini_filtrati = {
        (ordine.IdDocumento, ordine.IdRigaDoc): ordine
        for ordine in table_ordini
        if ordine.CodArt in codici_articoli
    }
    ordini_elaborati = [{
        "IdDocumento" : int(ordine.IdDocumento),
        "IdRigaDoc" : int(ordine.IdRigaDoc),
        "CodTipoDoc" : ordine.CodTipoDoc,
        "DataRegistrazione" : ordine.DataRegistrazione,
        "CodSerie" : ordine.CodSerie,
        "NumRegistraz" : ordine.NumRegistraz,
        "NumDocOriginale" : ordine.NumDocOriginale,
        "DataOriginale" : ordine.DataOriginale,
        "CodCliFor" : ordine.CodCliFor,
        "TipoRigaDoc" : ordine.TipoRigaDoc,
        "CodArt" : ordine.CodArt,
        "DesArt" : ordine.DesArt,
        "DesEstesa" : ordine.DesEstesa,
        "DataConsegna" : ordine.DataConsegna,
        "UmDoc" : ordine.UmDoc,
        "QTA_ORD" : int(ordine.QTA_ORD),
        "QTA_CONS" : int(ordine.QTA_CONS),
        "QTA_SALDO_DOC" : int(ordine.QTA_SALDO_DOC),
        "Commento_Riga_Saldata" : ordine.Commento_Riga_Saldata,
        "NotaInterna" : ordine.NotaInterna,
        "synced_at" : sync_time,
        } for ordine in ordini_filtrati.values()]
    return ordini_elaborati

def clienti(
    table_clienti: list[VwESClientiFornitori],
    table_ordini: list[dict[str, Any]],
    sync_time: datetime
) -> list[dict[str, Any]]:

    codici_clienti = {ordine["CodCliFor"] for ordine in table_ordini}

    clienti_filtrati = {
        cliente.CodCliFor: cliente
        for cliente in table_clienti
        if cliente.TipoAnagrafica == 1 and cliente.CodCliFor in codici_clienti
    }
    clienti_elaborati = [{
        "TipoAnagrafica" : cliente.TipoAnagrafica,
        "CodCliFor" : cliente.CodCliFor,
        "RagioneSociale" : cliente.RagioneSociale,
        "Indirizzo" : cliente.Indirizzo,
        "Cap" : cliente.Cap,
        "Localita" : cliente.Localita,
        "Provincia" : cliente.Provincia,
        "CodStato" : cliente.CodStato,
        "synced_at" : sync_time,
        } for cliente in clienti_filtrati.values()]

    return clienti_elaborati

def log_ordini(ordini_nuovi: list[dict[str, Any]], sync_time: datetime):
    elaborazione = [
        {
            "IdDocumento": list(ordine.values())[0],
            "IdRigaDoc": list(ordine.values())[1],
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
            "IdDocumento": list(ordine.values())[0],
            "IdRigaDoc": list(ordine.values())[1],
            "Stato": "Nuovo",
            "Operatore": "sync",
            "DataRegistrazione": dateutil.utils.today(tzinfo=ZoneInfo(TimeZone)),
            "synced_at": sync_time,
        }
        for ordine in ordini_nuovi
    ]
    return elaborazione


def main():
    try:
        with SessionSqlite() as sess:
            ordini_esistenti = set(sess.scalars(select(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc)).all())
    except sqlalchemy.exc.IntegrityError as e:
        print(f"Errore di lettura dati {e}")
    articoli_raw = leggi_view(VwESArticoli, SessionSQLServer)
    ordini_raw = leggi_view(VwESOrdiniClienteAperti, SessionSQLServer)
    clienti_raw = leggi_view(VwESClientiFornitori, SessionSQLServer)

    sync_time = datetime.now(ZoneInfo(TimeZone))
    articoli_elaborati = articoli(articoli_raw, sync_time)
    ordini_elaborati = ordini(ordini_raw, articoli_elaborati, sync_time)
    clienti_elaborati = clienti(clienti_raw, ordini_elaborati, sync_time)

    ordini_nuovi = [ordine for ordine in ordini_elaborati if (ordine["IdDocumento"], ordine["IdRigaDoc"],) not in ordini_esistenti]
    ordini_nuovi_log = log_ordini(ordini_nuovi, sync_time)
    ordini_stato_riga = stato_riga(ordini_nuovi, sync_time)

    stmt_articoli_upsert = insert(AnagraficaArticoli).values(articoli_elaborati)
    stmt_articoli_upsert = stmt_articoli_upsert.on_conflict_do_update(
            index_elements = ["CodArt"],
            set_={
        "DesArt":       stmt_articoli_upsert.excluded.DesArt,
        "CodFamiglia":  stmt_articoli_upsert.excluded.CodFamiglia,
        "Prezzatura":   stmt_articoli_upsert.excluded.Prezzatura,
        "Glassatura":   stmt_articoli_upsert.excluded.Glassatura,
        "synced_at":    stmt_articoli_upsert.excluded.synced_at,
        },
    )
    articoli_presenti = [
        articolo["CodArt"]
        for articolo in articoli_elaborati
        ]
    stmt_articoli_delete = delete(AnagraficaArticoli).where(AnagraficaArticoli.CodArt.not_in(articoli_presenti))

    stmt_ordini_upsert = insert(OrdiniCliente).values(ordini_elaborati)
    stmt_ordini_upsert = stmt_ordini_upsert.on_conflict_do_update(
            index_elements = ["IdDocumento", "IdRigaDoc"],
            set_ = {
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
    ordini_presenti = [
        (ordine["IdDocumento"], ordine["IdRigaDoc"])
        for ordine in ordini_elaborati
        ]
    stmt_ordini_delete = delete(OrdiniCliente).where(tuple_(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc).not_in(ordini_presenti))

    stmt_clienti_upsert = insert(AnagraficaClienti).values(clienti_elaborati)
    stmt_clienti_upsert = stmt_clienti_upsert.on_conflict_do_update(
            index_elements = ["CodCliFor"],
            set_ = {
                "TipoAnagrafica" : stmt_clienti_upsert.excluded.TipoAnagrafica,
                "RagioneSociale" : stmt_clienti_upsert.excluded.RagioneSociale,
                "Indirizzo" : stmt_clienti_upsert.excluded.Indirizzo,
                "Cap" : stmt_clienti_upsert.excluded.Cap,
                "Localita" : stmt_clienti_upsert.excluded.Localita,
                "Provincia" : stmt_clienti_upsert.excluded.Provincia,
                "CodStato" : stmt_clienti_upsert.excluded.CodStato,
                "synced_at" : stmt_clienti_upsert.excluded.synced_at,
                },
            )

    stmt_log_operazioni_upsert = insert(LogOperazioni).values(ordini_nuovi_log)
    stmt_stato_riga_upsert = insert(StatoRigaOrdine).values(ordini_stato_riga)
    stmt_stato_riga_upsert = stmt_stato_riga_upsert.on_conflict_do_nothing(
        index_elements=["IdDocumento", "IdRigaDoc"]
    )
    stmt_stato_riga_delete = delete(StatoRigaOrdine).where(tuple_(StatoRigaOrdine.IdDocumento, StatoRigaOrdine.IdRigaDoc).not_in(ordini_presenti))

    try:
        with SessionSqlite() as sess:
            sess.execute(stmt_clienti_upsert)
            sess.execute(stmt_articoli_upsert)
            sess.execute(stmt_ordini_upsert)
            sess.execute(stmt_ordini_delete)
            sess.execute(stmt_articoli_delete)
            sess.execute(stmt_log_operazioni_upsert)
            sess.execute(stmt_stato_riga_upsert)
            sess.execute(stmt_stato_riga_delete)
            sess.commit()
    except sqlalchemy.exc.IntegrityError as e:
        print(f"Errore di inserimento dati {e}")


if __name__ == "__main__":
    a = False
    counter = 0
    while not a:
        main()
        sleep(30)
        print(f"Ciclo {counter} completato")
        counter+=1
