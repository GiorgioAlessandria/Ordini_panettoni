"""Eventi proposti nell'agenda mista: una cottura logica per pianificazione.

Il record di cottura include TUTTI i forni e le loro date/blocchi, anche quando
non coincidono. NON e' un controllo di disponibilita': la simulazione li verifica.
"""
from __future__ import annotations
from collections import defaultdict


def eventi_proposta_mista(esito: dict) -> list[dict]:
    if not esito or not esito.get('risultati'):
        return []
    eventi = []
    for fase in sorted(esito['fasi_impasto'], key=lambda f: (f['DataFase'], f['Blocco'], f['OffsetBlocchi'])):
        eventi.append(dict(tipo='impasto', data=fase['DataFase'], blocco=fase['Blocco'],
                           lavorazione=fase['IdLavorazione'], forni=()))
    forni = tuple(dict(id_forno=int(id_forno), data=r['data'], blocco=r['blocco'],
                       cotte=int(r['esito'].numero_cotte))
                  for id_forno, r in sorted(esito['risultati'].items()))
    primo = min(forni, key=lambda f: (f['data'], f['blocco']))
    eventi.append(dict(tipo='cottura', data=primo['data'], blocco=primo['blocco'],
                       lavorazione=None, forni=forni))
    return sorted(eventi, key=lambda e: (e['data'], e['blocco'], 0 if e['tipo'] == 'impasto' else 1))
