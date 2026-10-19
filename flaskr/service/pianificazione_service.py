# identificazione ricetta
# recupero delle fasi
# calcolo data e blocco ogni fase
# validazione delle fasi
# creazione oggetti
# salvataggio pianificazione completa
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import tuple_
from sqlalchemy.exc import SQLAlchemyError

from flaskr import db
from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    OrdiniCliente,
    PianificazioneFasi,
    PianificazioneProduzione,
    RicettaFasi,
    TipologiaRicette,
    TipRicetteAnagArticoli,
)


class ErroreCalcoloPianificazione(Exception):
    pass


def recupero_ricetta_articolo(list_ordini: list[OrdiniCliente]) -> TipologiaRicette:
    codici_articolo_complessivo = {ordine.CodArt for ordine in list_ordini}
    if len(codici_articolo_complessivo) != 1:
        raise ErroreCalcoloPianificazione("Il numero di codici selezionati è eterogeneo")
    stmt_articolo = db.select(AnagraficaArticoli).where(
        AnagraficaArticoli.CodArt == next(iter(codici_articolo_complessivo))
    )
    articolo = db.session.execute(stmt_articolo).scalar_one_or_none()
    if articolo is None:
        raise ErroreCalcoloPianificazione("Articolo non trovato")
    stmt_ricetta_articoli = db.select(TipRicetteAnagArticoli).where(
        TipRicetteAnagArticoli.CodArt == articolo.CodArt
    )
    articolo_ricetta = db.session.execute(stmt_ricetta_articoli).scalar_one_or_none()
    if articolo_ricetta is None:
        raise ErroreCalcoloPianificazione("Associazione articolo ricetta non trovato")
    stmt_ricetta = db.select(TipologiaRicette).where(
        TipologiaRicette.IdRicetta == articolo_ricetta.IdRicetta
    )
    ricetta = db.session.execute(stmt_ricetta).scalar_one_or_none()
    if ricetta is None:
        raise ErroreCalcoloPianificazione("Ricetta non trovata")
    return ricetta


def recupero_fasi_per_ricetta(ricetta: TipologiaRicette) -> Sequence[RicettaFasi]:
    stmt_fasi = (
        db.select(RicettaFasi)
        .where(RicettaFasi.IdRicetta == ricetta.IdRicetta)
        .order_by(RicettaFasi.IdFase)
    )
    fasi_ricetta = db.session.scalars(stmt_fasi).all()
    if not fasi_ricetta:
        raise ErroreCalcoloPianificazione("Fasi ricetta non trovate")
    return fasi_ricetta


def calcola_blocchi_fase(
    blocco_iniziale: int, offset_blocchi_fase: int, data_lavorazione: date
) -> tuple[date, int]:
    if blocco_iniziale not in (0, 1):
        raise ErroreCalcoloPianificazione("Blocco iniziale non 0 o 1")
    if offset_blocchi_fase < 0:
        raise ErroreCalcoloPianificazione("Offset blocchi fase negativi")
    if not data_lavorazione:
        raise ErroreCalcoloPianificazione("Data lavorazione non inserita")
    blocco_fase = blocco_iniziale + offset_blocchi_fase
    giorni_trascorsi = blocco_fase // 2
    data_lavorazione_calcolata = data_lavorazione + timedelta(days=giorni_trascorsi)
    blocco_risultante = blocco_fase % 2
    return data_lavorazione_calcolata, blocco_risultante


def blocchi_fase(
    fasi_ricetta: Sequence[RicettaFasi],
    pianificazione_produzione: PianificazioneProduzione,
    data_inizio_lavorazione: str,
    blocco_iniziale: int,
) -> list[PianificazioneFasi]:
    if not data_inizio_lavorazione:
        raise ErroreCalcoloPianificazione("Data di inizio lavorazione non trovata")
    data_lavorazione = date.fromisoformat(data_inizio_lavorazione)
    lista_fasi_pianificate: list[PianificazioneFasi] = []
    for fase in fasi_ricetta:
        data_lavorazione_calcolata, indice_blocco = calcola_blocchi_fase(
            blocco_iniziale, fase.OffsetBlocchi, data_lavorazione
        )
        blocco_iniziale_db = "M" if indice_blocco == 0 else "P"
        pianificazione_fasi = PianificazioneFasi(
            IdPianificazione=pianificazione_produzione.IdPianificazione,
            IdRicettaFase=fase.id,
            IdLavorazione=fase.IdLavorazione,
            OffsetBlocchi=fase.OffsetBlocchi,
            DataFase=data_lavorazione_calcolata,
            Blocco=blocco_iniziale_db,
        )
        lista_fasi_pianificate.append(pianificazione_fasi)
    return lista_fasi_pianificate


def validazione_linearita_offset(lista_fasi: list[PianificazioneFasi]) -> bool:
    if not lista_fasi:
        return False
    if lista_fasi[0].OffsetBlocchi != 0:
        return False
    for n in range(1, len(lista_fasi)):
        if lista_fasi[n].OffsetBlocchi <= lista_fasi[n - 1].OffsetBlocchi:
            return False
    return True


def ottimizzazione_infornata(
    panettoni_previsti: int, panettoni_per_infornata: int, ottimizzazione: bool
) -> int:
    intero_infornate = panettoni_previsti // panettoni_per_infornata
    scarto_infornate = panettoni_previsti % panettoni_per_infornata
    if scarto_infornate and ottimizzazione:
        intero_infornate += 1
    return intero_infornate


def calcolo_max_panettoni(
    costante_infornate: int,
    panettoni_per_infornata: int,
    panettoni_previsti: int,
    ottimizza_infornata: bool,
) -> tuple[int, int, int, int]:
    if costante_infornate <= 0:
        raise ErroreCalcoloPianificazione(
            "La costante infornate non è stata configurata correttamente"
        )
    if panettoni_per_infornata <= 0:
        raise ErroreCalcoloPianificazione(
            "Il numero di panettoni per infornata non sono stati configurati correttamente"
        )
    if panettoni_previsti < 0:
        raise ErroreCalcoloPianificazione("Il numero di panettoni previsti è minore di ZERO")
    panettoni_massimi_infornata = costante_infornate * panettoni_per_infornata
    if panettoni_massimi_infornata >= panettoni_previsti:
        panettoni_residui = 0
        if ottimizza_infornata:
            numero_infornate_previste = ottimizzazione_infornata(
                panettoni_previsti, panettoni_per_infornata, ottimizza_infornata
            )
            panettoni_previsti_infornata = panettoni_per_infornata * numero_infornate_previste
            panettoni_surplus = panettoni_previsti_infornata - panettoni_previsti
        else:
            numero_infornate_previste = ottimizzazione_infornata(
                panettoni_previsti, panettoni_per_infornata, ottimizza_infornata
            )
            panettoni_previsti_infornata = panettoni_previsti
            panettoni_surplus = 0
        return (
            panettoni_previsti_infornata,
            panettoni_residui,
            panettoni_surplus,
            numero_infornate_previste,
        )
    else:
        panettoni_surplus = 0
        numero_infornate_previste = ottimizzazione_infornata(
            panettoni_previsti, panettoni_per_infornata, ottimizza_infornata
        )
        panettoni_residui = panettoni_previsti - panettoni_massimi_infornata
        return (
            panettoni_massimi_infornata,
            panettoni_residui,
            panettoni_surplus,
            numero_infornate_previste,
        )


def validazione_conflitti_idlavorazione(lista_fasi: Sequence[PianificazioneFasi]) -> bool:
    for fase in lista_fasi:
        stmt_conflitto = db.select(PianificazioneFasi.IdPianificazioneFase).where(
            PianificazioneFasi.IdLavorazione == fase.IdLavorazione,
            PianificazioneFasi.DataFase == fase.DataFase,
            PianificazioneFasi.Blocco == fase.Blocco,
        )
        fase_esistente = db.session.scalar(stmt_conflitto)
        if fase_esistente is not None:
            return False
    return True


def calcola_quantita_residua_ordine(
    qta_saldo_documento: int,
    qta_gia_pianificata: int,
) -> int:

    return max(
        0,
        qta_saldo_documento - qta_gia_pianificata,
    )


def recupera_quantita_gia_pianificate(
    ordini: Sequence[OrdiniCliente],
) -> dict[tuple[int, int], int]:

    # Chiavi composte degli ordini selezionati
    chiavi_ordini = [(ordine.IdDocumento, ordine.IdRigaDoc) for ordine in ordini]

    if not chiavi_ordini:
        return {}

    stmt = (
        db.select(
            PianificazioneOrdini.IdDocumento,
            PianificazioneOrdini.IdRiga,
            db.func.coalesce(
                db.func.sum(PianificazioneOrdini.QtaPianificata),
                0,
            ).label("totale_pianificato"),
        )
        .where(
            tuple_(
                PianificazioneOrdini.IdDocumento,
                PianificazioneOrdini.IdRiga,
            ).in_(chiavi_ordini)
        )
        .group_by(
            PianificazioneOrdini.IdDocumento,
            PianificazioneOrdini.IdRiga,
        )
    )

    righe = db.session.execute(stmt).all()

    return {(id_documento, id_riga): totale for id_documento, id_riga, totale in righe}


def distribuisci_quantita_ordini(
    ordini_disponibili: Sequence[tuple[OrdiniCliente, int]],
    capacita_assegnabile: int,
) -> list[tuple[OrdiniCliente, int]]:

    if capacita_assegnabile < 0:
        raise ErroreCalcoloPianificazione("La capacità assegnabile non può essere negativa")

    assegnazioni: list[tuple[OrdiniCliente, int]] = []
    capacita_residua = capacita_assegnabile

    for ordine, quantita_disponibile in ordini_disponibili:
        if capacita_residua <= 0:
            break

        quantita_da_assegnare = min(
            quantita_disponibile,
            capacita_residua,
        )

        if quantita_da_assegnare <= 0:
            continue

        assegnazioni.append((ordine, quantita_da_assegnare))

        capacita_residua -= quantita_da_assegnare

    return assegnazioni
