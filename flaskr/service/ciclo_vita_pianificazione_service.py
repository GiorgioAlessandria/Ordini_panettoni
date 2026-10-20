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


def calcola_surplus_teorico_storico(
    piano: PianificazioneProduzione, righe: list[PianificazioneOrdini]
) -> int | None:
    """Surplus *fisico* massimo ricavabile dai dati, non prova di accredito.

    Se il risultato e' 0, sicuramente non ci sono pezzi extra da stornare.
    Se >0, non sappiamo se le vecchie versioni abbiano accreditato lo stock.
    """
    if piano.PanettoniPianificati is None or not righe:
        return None
    produzione_per_ordini = 0
    for r in righe:
        if r.QtaPianificata is None:
            return None
        qta = int(r.QtaPianificata)
        da_stock = int(r.QtaDaStock or 0)
        if qta <= 0 or da_stock < 0 or da_stock > qta:
            return None
        produzione_per_ordini += qta - da_stock
    surplus = int(piano.PanettoniPianificati) - produzione_per_ordini
    return surplus if surplus >= 0 else None


def riconcilia_stock_storico(
    id_pianificazione: int, operatore: str, pezzi_accreditati: int, motivo: str
) -> None:
    """Registra il dato CONTABILE verificato, senza toccare la giacenza.

    Solo per pianificazioni legacy non riconciliate e ancora in produzione.
    Valore 0 significa che non furono effettuati accrediti, anche se
    la produzione avrebbe potuto creare surplus fisico.
    """
    motivo = motivo.strip()
    if len(motivo) < 10:
        raise ErroreCicloVita("Indicare come è stato verificato lo stock (almeno 10 caratteri)")
    try:
        piano, righe = _prepara(id_pianificazione)
        if piano.QtaStockGenerata is not None:
            raise ErroreCicloVita("Lo stock di questa pianificazione è già stato riconciliato")
        massimo = calcola_surplus_teorico_storico(piano, righe)
        if massimo is None:
            raise ErroreCicloVita("Impossibile ricostruire le quantità del piano: verifica manuale necessaria")
        if pezzi_accreditati < 0 or pezzi_accreditati > massimo:
            raise ErroreCicloVita(
                f"Quantità accreditata non valida: deve essere tra 0 e {massimo} pezzi"
            )
        aggiornamento = db.session.execute(
            update(PianificazioneProduzione)
            .where(
                PianificazioneProduzione.IdPianificazione == id_pianificazione,
                PianificazioneProduzione.StatoRigaOrdine == "In produzione",
                PianificazioneProduzione.QtaStockGenerata.is_(None),
            )
            .values(QtaStockGenerata=pezzi_accreditati)
        )
        if aggiornamento.rowcount != 1:
            raise ErroreCicloVita("Piano modificato da un altro operatore: ricarica la pagina")
        adesso = datetime.now()
        # Log append-only per audit; la riconciliazione NON cambia lo stato degli ordini.
        for r in righe:
            db.session.add(LogOperazioni(
                IdDocumento=r.IdDocumento,
                IdRigaDoc=r.IdRiga,
                Stato=f"Riconciliazione stock piano {id_pianificazione}: "
                      f"{pezzi_accreditati} pezzi accreditati - {motivo}",
                Operatore=operatore,
                synced_at=adesso,
            ))
        db.session.commit()
    except ErroreCicloVita:
        db.session.rollback()
        raise
    except SQLAlchemyError as err:
        db.session.rollback()
        raise ErroreCicloVita("Errore di salvataggio riconciliazione: nessuna modifica effettuata") from err


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
        stock_usato = sum(int(r.QtaDaStock or 0) for r in righe)
        if piano.QtaStockGenerata is None:
            surplus_teorico = calcola_surplus_teorico_storico(piano, righe)
            if surplus_teorico != 0:
                raise ErroreCicloVita(
                    "Surplus storico non riconciliato: verifica i pezzi accreditati "
                    "nella sezione Riconciliazione stock prima di annullare."
                )
            # Nessun surplus fisico: azzeramento certo, senza stock stornato.
            # Commit atomico insieme all'annullamento (se fallisce, torna NULL).
            piano.QtaStockGenerata = 0
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
