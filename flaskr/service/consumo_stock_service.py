"""Ripartizione stock su ordini già ordinati per priorità/consegna.

QtaPianificata mantiene la semantica di quantità complessiva IMPEGNATA dell'ordine.
QtaDaStock contiene i pezzi già disponibili che non devono essere prodotti.
"""
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def distribuisci_stock_ordini(ordini: Sequence[tuple[T, int]], stock_usato: int) -> list[tuple[T, int, int]]:
    """Restituisce (ordine, pezzi_assegnati, pezzi_da_stock), senza effetti collaterali."""
    if stock_usato < 0 or stock_usato > sum(q for _, q in ordini):
        raise ValueError("Quantità di stock non coerente con gli ordini selezionati")
    residuo_stock = stock_usato
    risultato = []
    for ordine, quantita in ordini:
        if quantita <= 0:
            continue
        prelievo = min(residuo_stock, quantita)
        risultato.append((ordine, quantita, prelievo))
        residuo_stock -= prelievo
    if residuo_stock:
        raise ValueError("Stock non completamente ripartito")
    return risultato
