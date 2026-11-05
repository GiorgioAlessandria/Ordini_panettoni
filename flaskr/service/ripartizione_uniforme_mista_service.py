"""Ripartizione uniforme (water filling) del peso sui soli mixer misti.
Non sostituisce il servizio della pianificazione classica.
"""
from decimal import Decimal


def distribuisci_uniformemente(peso_kg: Decimal, capacita_per_macchina: dict[int, Decimal]) -> dict[int, Decimal]:
    peso_kg = Decimal(str(peso_kg))
    capacita = {int(i): Decimal(str(v)) for i, v in capacita_per_macchina.items()}
    if peso_kg <= 0:
        raise ValueError('Peso impasto non valido.')
    if not capacita or any(v <= 0 for v in capacita.values()):
        raise ValueError('Seleziona impastatrici con capacità positiva.')
    if sum(capacita.values(), Decimal(0)) < peso_kg:
        mancano = peso_kg - sum(capacita.values(), Decimal(0))
        raise ValueError(f'Capacità impastatrici insufficiente: mancano {mancano:.2f} kg.')
    restanti = peso_kg
    libere = set(capacita)
    risultato = {i: Decimal(0) for i in sorted(capacita)}
    while libere and restanti > 0:
        quota = restanti / Decimal(len(libere))
        sature = {i for i in libere if capacita[i] - risultato[i] <= quota}
        if sature:
            for i in sorted(sature):
                spazio = capacita[i] - risultato[i]
                risultato[i] += spazio
                restanti -= spazio
            libere.difference_update(sature)
        else:
            ordinati = sorted(libere)
            for i in ordinati[:-1]:
                risultato[i] += quota
                restanti -= quota
            risultato[ordinati[-1]] += restanti
            restanti = Decimal(0)
    if restanti != 0 or sum(risultato.values(), Decimal(0)) != peso_kg:
        raise ValueError('Ripartizione impasto non coerente.')
    return risultato
