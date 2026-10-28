"""Ripartizione stock su ordini già ordinati per priorità/consegna.

QtaPianificata mantiene la semantica di quantità complessiva IMPEGNATA dell'ordine.
QtaDaStock contiene i pezzi già disponibili che non devono essere prodotti.
"""
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def distribuisci_stock_ordini(
    ordini: Sequence[tuple[T, int]],
    stock_usato: int,
    capacita_assegnabile: int,
) -> list[tuple[T, int, int]]:
    """Restituisce (ordine, pezzi_assegnati, pezzi_da_stock), senza effetti collaterali."""
    if stock_usato < 0 or stock_usato > sum(q for _, q in ordini):
        raise ValueError("Quantità di stock non coerente con gli ordini selezionati")
    totale_disponibile = sum(q for _, q in ordini if q > 0)
    if not 0 < capacita_assegnabile <= totale_disponibile:
        raise ValueError("Quantità assegnabile non coerente con gli ordini")
    if stock_usato > capacita_assegnabile:
        raise ValueError("Lo stock utilizzato supera la quantità assegnabile")

    residuo_stock = stock_usato
    residuo_da_assegnare = capacita_assegnabile
    risultato = []
    for ordine, quantita in ordini:
        if residuo_da_assegnare <= 0:
            break
        pezzi = min(max(0, quantita), residuo_da_assegnare)
        if pezzi <= 0:
            continue
        prelievo = min(residuo_stock, pezzi)
        risultato.append((ordine, pezzi, prelievo))
        residuo_stock -= prelievo
        residuo_da_assegnare -= pezzi
    if residuo_stock or residuo_da_assegnare:
        raise ValueError("Ripartizione quantità/stock incompleta")
    return risultato
