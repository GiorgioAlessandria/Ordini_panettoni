"""Ripartizione conservativa di più formati nelle cotte dello stesso forno.

La capienza di un formato in una cotta omogenea (CapacitaForno.Capienza)
è usata per stimarne l'occupazione frazionaria in una cotta mista.
QUESTA È UN'IPOTESI DI CALCOLO, non una convalida fisica del forno.
Il servizio non modifica né prenota risorse nel database.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from math import ceil


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
    minimo_stimato: int
    massimo_da_ordini: int
    cotte: tuple[dict[str, int], ...]
    assegnazioni: dict[tuple[int, int], int]
    residui: dict[tuple[int, int], int]
    occupazioni: tuple[Fraction, ...]


def _valida(righe: list[RigaCotta]) -> None:
    if not righe:
        raise ErroreCotteMiste('Nessun articolo destinato al forno selezionato')
    chiavi = [(r.documento, r.riga) for r in righe]
    if len(chiavi) != len(set(chiavi)):
        raise ErroreCotteMiste('Un ordine è stato inserito più volte')
    if any(r.richiesti <= 0 or r.capienza_omogenea <= 0 for r in righe):
        raise ErroreCotteMiste('Quantità o capacità del forno non valide')


def _riempi(righe: list[RigaCotta], numero_cotte: int) -> tuple[list[dict[str, int]], dict[tuple[int, int], int], tuple[Fraction, ...]]:
    """Assegna pezzi interi, prima per consegna, poi per maggiore occupazione.

    La verifica è sufficiente per la sicurezza del modello proporzionale:
    nessuna cotta supera 1. Non garantisce il numero minimo matematico.
    """
    contenuti = [defaultdict(int) for _ in range(numero_cotte)]
    utilizzato = [Fraction(0, 1) for _ in range(numero_cotte)]
    assegnati = defaultdict(int)
    for ordine in sorted(righe, key=lambda r: (r.priorita, -Fraction(1, r.capienza_omogenea), r.documento, r.riga)):
        remaining = ordine.richiesti
        for idx in range(numero_cotte):
            spazio_pezzi = ((1 - utilizzato[idx]) * ordine.capienza_omogenea).__floor__()
            n = min(remaining, spazio_pezzi)
            if n > 0:
                contenuti[idx][ordine.codart] += n
                utilizzato[idx] += Fraction(n, ordine.capienza_omogenea)
                assegnati[(ordine.documento, ordine.riga)] += n
                remaining -= n
            if remaining == 0:
                break
    return [dict(c) for c in contenuti], dict(assegnati), tuple(utilizzato)


def ripartisci_cotte(righe: list[RigaCotta], numero_cotte: int, limite_blocco: int) -> EsitoCotte:
    _valida(righe)
    if limite_blocco < 0 or not (1 <= numero_cotte <= limite_blocco):
        raise ErroreCotteMiste(f'Cotte richieste: {numero_cotte}. Limite disponibile: {limite_blocco}.')

    massimo_da_ordini = sum((r.richiesti + r.capienza_omogenea - 1) // r.capienza_omogenea for r in righe)
    if numero_cotte > massimo_da_ordini:
        raise ErroreCotteMiste(
            f'Cotte richieste: {numero_cotte}; il fabbisogno massimo degli ordini è {massimo_da_ordini}.'
        )

    # Calcolo prudenziale del numero di cotte: prova greedy senza imporre
    # un numero fissato, quindi non dichiara mai ammissibile una cotta piena.
    cap = sum(r.richiesti for r in righe)
    # Bound inferiore per occupazione più eventuale frammentazione.
    occupazione = sum((Fraction(r.richiesti, r.capienza_omogenea) for r in righe), Fraction(0))
    lower = max(1, ceil(occupazione))
    minimo = massimo_da_ordini
    for n in range(lower, massimo_da_ordini + 1):
        _, occupati, _ = _riempi(righe, n)
        if sum(occupati.values()) == cap:
            minimo = n
            break

    cotte, assegnate, occupazioni = _riempi(righe, numero_cotte)
    # Il numero inserito dall'operatore rappresenta cotte effettive.
    # Se il greedy ha accorpato tutte le quantità nei primi carichi,
    # distribuisci almeno un pezzo per ciascuna cotta scelta.
    occupazioni_mut = list(occupazioni)
    cap_by_code = {r.codart: r.capienza_omogenea for r in righe}
    for idx, contenuto in enumerate(cotte):
        if contenuto:
            continue
        donatore = next((j for j, c in enumerate(cotte) if sum(c.values()) > 1), None)
        if donatore is None:
            raise ErroreCotteMiste('Non ci sono abbastanza pezzi per utilizzare tutte le cotte indicate')
        codice = next(cod for cod, q in cotte[donatore].items() if q > 0)
        cotte[donatore][codice] -= 1
        if cotte[donatore][codice] == 0:
            del cotte[donatore][codice]
        cotte[idx][codice] = 1
        quota = Fraction(1, cap_by_code[codice])
        occupazioni_mut[donatore] -= quota
        occupazioni_mut[idx] += quota
    occupazioni = tuple(occupazioni_mut)
    if sum(occupazioni, Fraction(0)) > numero_cotte or any(o > 1 for o in occupazioni):
        raise ErroreCotteMiste('Sovraccarico in una delle cotte')
    residui = {(r.documento, r.riga): r.richiesti - assegnate.get((r.documento, r.riga), 0) for r in righe}
    return EsitoCotte(
        numero_cotte=numero_cotte, minimo_stimato=minimo,
        massimo_da_ordini=massimo_da_ordini, cotte=tuple(cotte),
        assegnazioni=assegnate, residui=residui, occupazioni=occupazioni,
    )
