"""Cotte miste: registrazione del numero dichiarato dall'operatore.

NON calcola la capienza fisica delle cotte, NON distribuisce i formati nelle
singole cotte e NON modifica il numero indicato. Valida unicamente la
positività e la disponibilità di slot del forno. I quantitativi vengono
riferiti agli ordini; una ripartizione fisica non dichiarata non si inventa.
"""
from __future__ import annotations

from dataclasses import dataclass


class ErroreCotteMiste(ValueError):
    pass


@dataclass(frozen=True)
class RigaCotta:
    documento: int
    riga: int
    codart: str
    richiesti: int
    capienza_omogenea: int
    priorita: int = 0


@dataclass(frozen=True)
class EsitoCotte:
    numero_cotte: int
    minimo_stimato: int | None
    massimo_da_ordini: int | None
    cotte: tuple[dict[str, int], ...]
    assegnazioni: dict[tuple[int, int], int]
    residui: dict[tuple[int, int], int]
    occupazioni: tuple


def ripartisci_cotte(righe: list[RigaCotta], numero_cotte: int, limite_blocco: int) -> EsitoCotte:
    """Valida la dichiarazione manuale, senza stimare carichi o numero di cotte.

    Il nome resta compatibile con i chiamanti esistenti. Il campo `cotte` è
    volutamente vuoto: il programma NON conosce la composizione per singola
    infornata. L'assenza delle righe in `bozze_pianificazione_mista_cotte`
    identifica un nuovo piano con sola dichiarazione del numero di cotte.
    """
    if not righe:
        raise ErroreCotteMiste('Nessun articolo destinato al forno selezionato.')
    chiavi = [(r.documento, r.riga) for r in righe]
    if len(chiavi) != len(set(chiavi)):
        raise ErroreCotteMiste('Un ordine è stato inserito più volte.')
    if any(not r.codart or r.richiesti <= 0 or r.capienza_omogenea <= 0 for r in righe):
        raise ErroreCotteMiste('Ordini o anagrafiche di capacità non validi.')
    if type(numero_cotte) is not int or type(limite_blocco) is not int:
        raise ErroreCotteMiste('Il numero di cotte deve essere un intero.')
    if numero_cotte < 1 or limite_blocco < 1 or numero_cotte > limite_blocco:
        raise ErroreCotteMiste(
            f'Cotte dichiarate: {numero_cotte}; cotte disponibili nel blocco: {limite_blocco}.'
        )
    return EsitoCotte(
        numero_cotte=numero_cotte,
        minimo_stimato=None,
        massimo_da_ordini=None,
        cotte=(),  # Composizione fisica NON fornita, nessuna distribuzione stimata.
        assegnazioni={(r.documento, r.riga): int(r.richiesti) for r in righe},
        residui={(r.documento, r.riga): 0 for r in righe},
        occupazioni=(),
    )
