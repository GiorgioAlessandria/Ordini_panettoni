"""Chiusura e annullamento compensativo di una pianificazione produttiva.

Operazioni atomiche. NON cancellare il piano o gli ordini associati:
lo stato Annullato li esclude dagli impegni produttivi e conserva lo storico.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, update
from sqlalchemy.exc import SQLAlchemyError

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    LogOperazioni,
    OrdiniCliente,
    PianificazioneFasi,
    PianificazioneImpastatriceFase,
    PianificazioneOperazioni,
    PianificazioneOrdini,
    PianificazioneProduzione,
    StatoRigaOrdine,
    Stock,
)


class ErroreCicloVita(Exception):
    """Anomalia di dominio: nessuna modifica deve essere confermata."""


def _rileggi_stato_ordine(id_documento: int, id_riga: int, adesso: datetime, operatore: str) -> None:
    """Lo stato del documento riflette TUTTE le pianificazioni, non solo quella chiusa.

    Se rimane una parte da pianificare la riga ERP resta 'Nuovo' anche se
    qualche sotto-pianificazione e' stata gia' chiusa.
    """
    originale = db.session.get(OrdiniCliente, (id_documento, id_riga))
    if originale is None:
        raise ErroreCicloVita(f"Ordine {id_documento}-{id_riga} non trovato: aggiornamento interrotto")
    if originale.QTA_SALDO_DOC is None:
        raise ErroreCicloVita(f"Saldo ERP mancante per l'ordine {id_documento}-{id_riga}")
    righe = db.session.scalars(
        db.select(PianificazioneOrdini).where(
            PianificazioneOrdini.IdDocumento == id_documento,
            PianificazioneOrdini.IdRiga == id_riga,
            PianificazioneOrdini.Stato.in_(("In produzione", "Chiuso")),
        )
    ).all()
    impegnato = sum(int(r.QtaPianificata) for r in righe)
    if any(r.Stato == "In produzione" for r in righe) and impegnato >= int(originale.QTA_SALDO_DOC):
        stato = "In produzione"
    elif impegnato >= int(originale.QTA_SALDO_DOC) and all(r.Stato == "Chiuso" for r in righe):
        stato = "Chiuso"
    else:
        stato = "Nuovo"
    riga_stato = db.session.get(StatoRigaOrdine, (id_documento, id_riga))
    if riga_stato is None:
        riga_stato = StatoRigaOrdine(
            IdDocumento=id_documento, IdRigaDoc=id_riga,
            Stato=stato, Operatore=operatore, DataRegistrazione=adesso.date(), synced_at=adesso
        )
        db.session.add(riga_stato)
    else:
        riga_stato.Stato = stato
        riga_stato.Operatore = operatore
        riga_stato.DataRegistrazione = adesso.date()
        riga_stato.synced_at = adesso


def _prepara(id_pianificazione: int) -> tuple[PianificazioneProduzione, list[PianificazioneOrdini]]:
    piano = db.session.get(PianificazioneProduzione, id_pianificazione)
    if piano is None:
        raise ErroreCicloVita(f"Pianificazione #{id_pianificazione} inesistente")
    if piano.StatoRigaOrdine != "In produzione":
        raise ErroreCicloVita(
            f"La pianificazione #{id_pianificazione} è già {piano.StatoRigaOrdine}. "
            "Non è possibile eseguire due volte questa operazione."
        )
    righe = db.session.scalars(
        db.select(PianificazioneOrdini).where(
            PianificazioneOrdini.IdPianificazione == id_pianificazione
        )
    ).all()
    if not righe or any(r.Stato != "In produzione" for r in righe):
        raise ErroreCicloVita("Le righe associate non hanno uno stato coerente con la pianificazione")
    if any(r.QtaPianificata is None or int(r.QtaPianificata) <= 0 for r in righe):
        raise ErroreCicloVita("Quantità impegnata mancante in una o più righe: correggere prima di procedere")
    if any(int(r.QtaDaStock or 0) > int(r.QtaPianificata) for r in righe):
        raise ErroreCicloVita("Quantità da stock superiore a quella impegnata")
    return piano, righe


def _scrivi_log(
    piano: PianificazioneProduzione,
    righe: list[PianificazioneOrdini],
    azione: str,
    operatore: str,
    istante: datetime,
    motivo: str | None = None,
    stock_restituito: int = 0,
    stock_stornato: int = 0,
) -> None:
    db.session.add(PianificazioneOperazioni(
        IdPianificazione=piano.IdPianificazione,
        Azione=azione, Operatore=operatore, DataOperazione=istante,
        Motivo=motivo, StockRestituito=stock_restituito,
        StockStornato=stock_stornato,
    ))
    for r in righe:
        db.session.add(LogOperazioni(
            IdDocumento=r.IdDocumento,
            IdRigaDoc=r.IdRiga,
            Stato=azione,
            Operatore=operatore,
            synced_at=istante,
        ))


def chiudi_pianificazione(id_pianificazione: int, operatore: str) -> None:
    """Conclude il piano e le sue righe, mantenendo le occupazioni nello storico.

    Non accredita nuovamente il surplus: il sistema lo ha già accreditato
    al momento della conferma iniziale (comportamento legacy).
    """
    try:
        piano, righe = _prepara(id_pianificazione)
        adesso = datetime.now()
        risultato = db.session.execute(
            update(PianificazioneProduzione)
            .where(
                PianificazioneProduzione.IdPianificazione == id_pianificazione,
                PianificazioneProduzione.StatoRigaOrdine == "In produzione",
            )
            .values(StatoRigaOrdine="Chiuso", DataChiusura=adesso, OperatoreChiusura=operatore)
        )
        if risultato.rowcount != 1:
            raise ErroreCicloVita("La pianificazione è stata modificata da un altro operatore")
        chiavi = {(r.IdDocumento, r.IdRiga) for r in righe}
        for r in righe:
            r.Stato = "Chiuso"
            r.DataChiusura = adesso
        db.session.flush()
        for documento, nriga in chiavi:
            _rileggi_stato_ordine(documento, nriga, adesso, operatore)
        _scrivi_log(piano, righe, "Chiuso", operatore, adesso)
        db.session.commit()
    except ErroreCicloVita:
        db.session.rollback()
        raise
    except SQLAlchemyError as err:
        db.session.rollback()
        raise ErroreCicloVita("Errore database durante la chiusura. Nessun dato modificato.") from err


def annulla_pianificazione(id_pianificazione: int, operatore: str, motivo: str) -> None:
    """Storno contabile dello stock e rilascio delle prenotazioni di una pianificazione.

    Una transazione già committata non può essere annullata con session.rollback():
    questa funzione applica movimenti COMPENSATIVI e marca il piano Annullato.
    """
    if len(motivo.strip()) < 5:
        raise ErroreCicloVita("Indicare un motivo di annullamento di almeno 5 caratteri")
    try:
        piano, righe = _prepara(id_pianificazione)
        if piano.QtaStockGenerata is None:
            raise ErroreCicloVita(
                "Surplus storico sconosciuto: prima di annullare occorre riconciliare "
                "QtaStockGenerata per questa pianificazione. Nessuna modifica eseguita."
            )
        stock_usato = sum(int(r.QtaDaStock or 0) for r in righe)
        stock_generato = int(piano.QtaStockGenerata)
        if stock_generato < 0:
            raise ErroreCicloVita("Valore di surplus non valido")
        variazione = stock_usato - stock_generato
        adesso = datetime.now()

        # Condizionale: impedisce stock negativo se surplus generato e' stato
        # usato altrove. In quel caso va riconciliata la pianificazione dipendente.
        if variazione:
            riga_stock = db.session.get(Stock, piano.CodArt)
            if riga_stock is None:
                if stock_generato > 0 or variazione < 0:
                    raise ErroreCicloVita("Stock del piano non disponibile per lo storno: riconciliare le giacenze")
                db.session.add(Stock(CodArt=piano.CodArt, Quantita=variazione))
            else:
                esito_stock = db.session.execute(
                    update(Stock)
                    .where(Stock.CodArt == piano.CodArt, Stock.Quantita + variazione >= 0)
                    .values(Quantita=Stock.Quantita + variazione)
                )
                if esito_stock.rowcount != 1:
                    raise ErroreCicloVita(
                        "Stock insufficiente per stornare il surplus generato: "
                        "potrebbe essere stato consumato da un'altra pianificazione."
                    )

        esito_piano = db.session.execute(
            update(PianificazioneProduzione)
            .where(
                PianificazioneProduzione.IdPianificazione == id_pianificazione,
                PianificazioneProduzione.StatoRigaOrdine == "In produzione",
            )
            .values(
                StatoRigaOrdine="Annullato",
                DataAnnullamento=adesso,
                OperatoreAnnullamento=operatore,
                MotivoAnnullamento=motivo,
            )
        )
        if esito_piano.rowcount != 1:
            raise ErroreCicloVita("La pianificazione è stata modificata da un altro operatore")

        chiavi = {(r.IdDocumento, r.IdRiga) for r in righe}
        for r in righe:
            r.Stato = "Annullato"  # Conserviamo quantità originali per audit
            r.DataChiusura = None
        db.session.flush()

        # Rimuoviamo SOLO le prenotazioni (UNIQUE sulla macchina/blocco),
        # mantenendo fasi e associazioni storiche. Il calendario filtra gli annullati.
        ids_fase = db.session.scalars(
            db.select(PianificazioneFasi.IdPianificazioneFase).where(
                PianificazioneFasi.IdPianificazione == id_pianificazione
            )
        ).all()
        if ids_fase:
            db.session.execute(
                delete(PianificazioneImpastatriceFase).where(
                    PianificazioneImpastatriceFase.IdPianificazioneFase.in_(ids_fase)
                )
            )

        for documento, nriga in chiavi:
            _rileggi_stato_ordine(documento, nriga, adesso, operatore)
        _scrivi_log(piano, righe, "Annullato", operatore, adesso, motivo,
                    stock_restituito=stock_usato, stock_stornato=stock_generato)
        db.session.commit()
    except ErroreCicloVita:
        db.session.rollback()
        raise
    except SQLAlchemyError as err:
        db.session.rollback()
        raise ErroreCicloVita("Errore database durante l'annullamento. Nessun dato modificato.") from err
