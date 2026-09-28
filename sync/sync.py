from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from models_sync import VwESArticoli, VwESClientiFornitori, VwESOrdiniClienteAperti
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker

from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    AnagraficaClienti,
    OrdiniCliente,
)

sql_params: str = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=SERVERSPRING02;DATABASE=BernardiProd;UID=Produzione;PWD=Produzione2025.;Encrypt=no;TrustServerCertificate=no;Pooling=no;MultipleActiveResultSets=False;"

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
            "Glassa": articolo.Glassatura,
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

def main():
    articoli_raw = leggi_view(VwESArticoli, SessionSQLServer)
    ordini_raw = leggi_view(VwESOrdiniClienteAperti, SessionSQLServer)
    clienti_raw = leggi_view(VwESClientiFornitori, SessionSQLServer)

    sync_time = datetime.now(ZoneInfo("Europe/Rome"))
    articoli_elaborati = articoli(articoli_raw, sync_time)
    ordini_elaborati = ordini(ordini_raw, articoli_elaborati, sync_time)
    clienti_elaborati = clienti(clienti_raw, ordini_elaborati, sync_time)

    with SessionSqlite() as sess:
        sess.execute(insert(AnagraficaArticoli), articoli_elaborati)
        sess.execute(insert(OrdiniCliente), ordini_elaborati)
        sess.execute(insert(AnagraficaClienti), clienti_elaborati)
        sess.commit()

if __name__ == "__main__":
    main()