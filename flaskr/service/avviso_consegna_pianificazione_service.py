"""Stima consegna: giorno seguente all'ULTIMO blocco occupato dalle fasi.

La verifica è informativa e non modifica scadenze ERP, blocchi o pianificazioni.
Le date sono di calendario (weekend inclusi), in accordo con gli offset M/P.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable


class ErroreAvvisoConsegna(ValueError):
    pass


@dataclass(frozen=True)
class EsitoAvvisoConsegna:
    ultimo_blocco_data: date
    ultimo_blocco: str
    consegna_prevista: date
    scadenza_ordine: date
    giorni_margine: int
    stato: str  # in_tempo / al_limite / in_ritardo
    ordini_al_limite: int
    ordini_in_ritardo: int
    numero_ordini: int
    ordine_scadenza: str


def solo_data(valore: date | datetime | None) -> date:
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    raise ErroreAvvisoConsegna('Data di consegna ERP non disponibile o non valida.')


def calcola_avviso_consegna(
    data_inizio: date,
    blocco_iniziale: str,
    fasi: Iterable[object],
    ordini: Iterable[object],
) -> EsitoAvvisoConsegna:
    """Una fase occupa OffsetBlocchi ... OffsetBlocchi + DurataBlocchi - 1.

    `fasi` contiene RicettaFasi con relazione `.lavorazione` precaricata.
    Le ricette degli articoli misti possono essere passate insieme: prevale
    l'ultimo blocco effettivamente occupato da una qualunque lavorazione.
    """
    if not isinstance(data_inizio, date) or isinstance(data_inizio, datetime):
        raise ErroreAvvisoConsegna('Seleziona una data iniziale valida.')
    if blocco_iniziale not in ('M', 'P'):
        raise ErroreAvvisoConsegna('Seleziona un blocco iniziale valido.')

    base = 0 if blocco_iniziale == 'M' else 1
    ultimo_indice = None
    for fase in fasi:
        try:
            offset = int(fase.OffsetBlocchi)
            durata = int(fase.lavorazione.DurataBlocchi)
        except (AttributeError, ValueError, TypeError) as errore:
            raise ErroreAvvisoConsegna('Una fase non dispone di offset o durata validi.') from errore
        if offset < 0 or durata <= 0:
            raise ErroreAvvisoConsegna('Le fasi devono avere offset non negativo e durata positiva.')
        indice = base + offset + durata - 1
        ultimo_indice = indice if ultimo_indice is None else max(ultimo_indice, indice)
    if ultimo_indice is None:
        raise ErroreAvvisoConsegna('Nessuna fase configurata per gli articoli selezionati.')

    ultimo_giorno = data_inizio + timedelta(days=ultimo_indice // 2)
    ultimo_blocco = 'M' if ultimo_indice % 2 == 0 else 'P'
    prevista = ultimo_giorno + timedelta(days=1)

    ordini_elenco = list(ordini)
    if not ordini_elenco:
        raise ErroreAvvisoConsegna('Seleziona almeno un ordine.')
    scadenze = []
    for ordine in ordini_elenco:
        scadenze.append((solo_data(ordine.DataConsegna), ordine))
    prima_scadenza, primo_ordine = min(
        scadenze, key=lambda item: (item[0], item[1].IdDocumento, item[1].IdRigaDoc)
    )
    margine = (prima_scadenza - prevista).days
    stato = 'in_ritardo' if margine < 0 else ('al_limite' if margine == 0 else 'in_tempo')
    return EsitoAvvisoConsegna(
        ultimo_blocco_data=ultimo_giorno,
        ultimo_blocco=ultimo_blocco,
        consegna_prevista=prevista,
        scadenza_ordine=prima_scadenza,
        giorni_margine=margine,
        stato=stato,
        ordini_al_limite=sum(g == prevista for g, _ in scadenze),
        ordini_in_ritardo=sum(g < prevista for g, _ in scadenze),
        numero_ordini=len(scadenze),
        ordine_scadenza=f'{primo_ordine.IdDocumento}-{primo_ordine.IdRigaDoc}',
    )
