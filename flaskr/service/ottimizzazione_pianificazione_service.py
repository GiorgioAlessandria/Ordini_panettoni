"""Simulazione di produzione condivisa tra anteprima HTMX e conferma.

Il peso del prodotto si ottiene dal riepilogo creato da calcolo_riepilogo_ordini,
che applica i coefficienti di glassatura e impasto configurati a database.
Lo stesso peso unitario viene applicato a tutti i pezzi, surplus incluso.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from collections.abc import Sequence

from flaskr.models.model_ordini import RicettaFasi
from flaskr.service.ordini_service import calcolo_infornate
from flaskr.service.pianificazione_service import (
    ErroreCalcoloPianificazione, calcolo_max_panettoni,
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


def simula_ottimizzazione(
    riepilogo: dict[str, Any],
    fasi_ricetta: Sequence[RicettaFasi],
    ottimizza: bool,
    pezzi_stock_utilizzati: int = 0,
) -> SimulazioneProduzione:
    """Calcola produzione + surplus con gli stessi coefficienti del riepilogo.

    Nessuna scrittura a DB: si può invocare all'attivazione della checkbox.
    """
    fasi_cottura = [
        fase for fase in fasi_ricetta
        if ("forno" in str(fase.lavorazione.TipologiaLavorazione).casefold()
            or "cott" in str(fase.lavorazione.DescrizioneLavorazione).casefold()
            or "inforn" in str(fase.lavorazione.DescrizioneLavorazione).casefold())
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
    massimo_pezzi = massimo_cotte * capienza
    if pezzi_da_produrre > massimo_pezzi:
        raise ErroreCalcoloPianificazione(
            f"Sono da produrre {pezzi_da_produrre} pezzi; il forno ne può lavorare "
            f"al massimo {massimo_pezzi} in {massimo_cotte} cotte. "
            "Riduci la selezione degli ordini."
        )
    pezzi_produzione, pezzi_residui, pezzi_stock, _ = calcolo_max_panettoni(
        massimo_cotte, capienza, pezzi_da_produrre, ottimizza,
    )
    if pezzi_residui != 0 or pezzi_produzione < pezzi_da_produrre:
        raise ErroreCalcoloPianificazione("Il piano non copre completamente gli ordini selezionati")
    if pezzi_stock != pezzi_produzione - pezzi_da_produrre:
        raise ErroreCalcoloPianificazione("Quantità extra di ottimizzazione incoerente")
    numero_cotte, _ = calcolo_infornate(capienza, pezzi_produzione)
    if numero_cotte > massimo_cotte:
        raise ErroreCalcoloPianificazione("Il numero di cotte supera il limite del blocco")
    peso_base = Decimal(str(riepilogo["peso_totale_grammi"]))
    peso_unitario = peso_base / Decimal(pezzi_ordine)
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
        max_cotte=massimo_cotte,
        peso_unitario_grammi=peso_unitario,
        peso_totale_grammi=peso_produzione,
        peso_totale_kg=(peso_produzione / Decimal(1000)).quantize(Decimal("0.01")),
        ottimizzata=ottimizza,
    )
