"""Simulazione di produzione condivisa tra anteprima HTMX e conferma.

Il peso del prodotto si ottiene dal riepilogo creato da calcolo_riepilogo_ordini,
che applica i coefficienti di glassatura e impasto configurati a database.
Lo stesso peso unitario viene applicato a tutti i pezzi, surplus incluso.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from flaskr.models.model_ordini import RicettaFasi
from flaskr.service.ordini_service import calcolo_infornate
from flaskr.service.pianificazione_service import (
    ErroreCalcoloPianificazione,
)


@dataclass(frozen=True)
class SimulazioneProduzione:
    pezzi_ordine: int
    pezzi_produzione: int
    pezzi_da_produrre: int
    pezzi_stock_utilizzati: int
    pezzi_stock: int
    numero_cotte: int
    capienza_cotta: int
    max_cotte: int
    peso_unitario_grammi: Decimal
    peso_totale_grammi: Decimal
    peso_totale_kg: Decimal
    ottimizzata: bool
    pezzi_produzione_ordini: int
    pezzi_assegnati: int
    pezzi_residui: int
    capacita_forno_pezzi: int
    capacita_impastatrici_pezzi: int


def simula_ottimizzazione(
    riepilogo: dict[str, Any],
    fasi_ricetta: Sequence[RicettaFasi],
    ottimizza: bool,
    pezzi_stock_utilizzati: int = 0,
    *,
    cotte_libere: int,
    capacita_impastatrici_pezzi: int,
) -> SimulazioneProduzione:
    """Calcola produzione + surplus con gli stessi coefficienti del riepilogo.

    Nessuna scrittura a DB: si può invocare all'attivazione della checkbox.
    """
    fasi_cottura = [
        fase
        for fase in fasi_ricetta
        if (
            "forno" in str(fase.lavorazione.TipologiaLavorazione).casefold()
            or "cott" in str(fase.lavorazione.DescrizioneLavorazione).casefold()
            or "inforn" in str(fase.lavorazione.DescrizioneLavorazione).casefold()
        )
    ]
    if len(fasi_cottura) != 1:
        raise ErroreCalcoloPianificazione(
            f"La ricetta richiede una fase di cottura; trovate {len(fasi_cottura)}"
        )
    massimo_cotte = int(fasi_cottura[0].CapacitaMax or 0)
    capienza = int(riepilogo["capienza_cotta"])
    pezzi_ordine = int(riepilogo["qta_panettoni"])
    if pezzi_stock_utilizzati < 0 or pezzi_stock_utilizzati > pezzi_ordine:
        raise ErroreCalcoloPianificazione("Quantità prelevata dallo stock non valida")
    pezzi_da_produrre = pezzi_ordine - pezzi_stock_utilizzati
    if pezzi_da_produrre <= 0:
        raise ErroreCalcoloPianificazione(
            "Gli ordini sono interamente coperti dallo stock: utilizzare una procedura "
            "di evasione da stock, senza creare una pianificazione di produzione a zero pezzi"
        )
    if massimo_cotte <= 0 or capienza <= 0 or pezzi_ordine <= 0:
        raise ErroreCalcoloPianificazione("Capacità forno o quantità da pianificare non valide")
    if cotte_libere < 0 or capacita_impastatrici_pezzi < 0:
        raise ErroreCalcoloPianificazione("Capacità disponibili non valide")

    cotte_effettive = min(massimo_cotte, cotte_libere)
    capacita_forno_pezzi = cotte_effettive * capienza
    # Le quantità sono tutte espresse in PEZZI; il limite piu' restrittivo prevale.
    massimo_producibile = min(capacita_forno_pezzi, capacita_impastatrici_pezzi)
    pezzi_produzione_ordini = min(pezzi_da_produrre, massimo_producibile)
    if pezzi_produzione_ordini <= 0:
        raise ErroreCalcoloPianificazione(
            "Nessuna capacità produttiva disponibile nei blocchi selezionati "
            "per il forno e le impastatrici."
        )

    # Non generare surplus mentre esistono ancora ordini da pianificare.
    # Se gli ordini sono interamente coperti, una cotta completa e' ammessa
    # solo se rispetta ENTRAMBI i limiti (forno e impastatrici).
    pezzi_produzione = pezzi_produzione_ordini
    if ottimizza and pezzi_produzione_ordini == pezzi_da_produrre:
        cotte_ottimizzate, _ = calcolo_infornate(capienza, pezzi_produzione_ordini)
        pezzi_cotta_completa = cotte_ottimizzate * capienza
        if pezzi_cotta_completa <= massimo_producibile:
            pezzi_produzione = pezzi_cotta_completa
    pezzi_stock = pezzi_produzione - pezzi_produzione_ordini
    pezzi_assegnati = pezzi_stock_utilizzati + pezzi_produzione_ordini
    pezzi_residui = pezzi_ordine - pezzi_assegnati

    numero_cotte, _ = calcolo_infornate(capienza, pezzi_produzione)
    if numero_cotte > cotte_effettive:
        raise ErroreCalcoloPianificazione("Il numero di cotte supera quelle disponibili")

    peso_base = Decimal(str(riepilogo["peso_totale_grammi"]))
    peso_unitario = peso_base / Decimal(pezzi_ordine)
    if peso_unitario <= 0:
        raise ErroreCalcoloPianificazione("Peso unitario non valido")
    peso_produzione = (peso_unitario * Decimal(pezzi_produzione)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return SimulazioneProduzione(
        pezzi_ordine=pezzi_ordine,
        pezzi_produzione=pezzi_produzione,
        pezzi_da_produrre=pezzi_da_produrre,
        pezzi_stock_utilizzati=pezzi_stock_utilizzati,
        pezzi_stock=pezzi_stock if ottimizza else 0,
        numero_cotte=numero_cotte,
        capienza_cotta=capienza,
        max_cotte=cotte_effettive,
        peso_unitario_grammi=peso_unitario,
        peso_totale_grammi=peso_produzione,
        peso_totale_kg=(peso_produzione / Decimal(1000)).quantize(Decimal("0.01")),
        ottimizzata=ottimizza,
        pezzi_produzione_ordini=pezzi_produzione_ordini,
        pezzi_assegnati=pezzi_assegnati,
        pezzi_residui=pezzi_residui,
        capacita_forno_pezzi=capacita_forno_pezzi,
        capacita_impastatrici_pezzi=capacita_impastatrici_pezzi,
    )
