"""Sottogruppi effettivamente compatibili per selezione visuale.

Lo stesso gruppo commerciale può contenere ricette con impasti non compatibili:
si colori soltanto i sottoinsiemi con >=2 CodArt effettivamente pianificabili.
Il POST conferma sempre nuovamente questa compatibilità sul database.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256

from flaskr.extensions import db
from flaskr.models.model_ordini import GruppoProduzione, GruppoProduzioneArticolo
from flaskr.service.anteprima_impasto_misto_service import ErroreAnteprimaMista, _fasi_e_ricetta


def gruppi_compatibili_visibili(codici_visibili: set[str]) -> dict[str, tuple[int, str, str]]:
    """CodArt -> (IdGruppo, NomeGruppo, ChiaveFirma). Solo candidati misti reali."""
    if len(codici_visibili) < 2:
        return {}
    associati = db.session.execute(
        db.select(GruppoProduzioneArticolo.CodArt,
                  GruppoProduzioneArticolo.IdGruppo,
                  GruppoProduzione.Nome)
        .join(GruppoProduzione, GruppoProduzione.IdGruppo == GruppoProduzioneArticolo.IdGruppo)
        .where(GruppoProduzione.Attivo == 1,
               GruppoProduzioneArticolo.CodArt.in_(codici_visibili))
    ).all()
    gruppi = defaultdict(list)
    for codice, id_gruppo, nome in associati:
        try:
            _fasi, firma, _forno = _fasi_e_ricetta(codice)
        except (ErroreAnteprimaMista, ValueError):
            # Non presentare come selezionabile un articolo dalla ricetta irregolare.
            continue
        if not firma:
            continue
        gruppi[(id_gruppo, tuple(firma))].append((codice, nome))

    risposta = {}
    for (id_gruppo, firma), righe in gruppi.items():
        if len({cod for cod, _ in righe}) < 2:
            continue
        # Valore deterministico fra refresh HTMX; non contiene la descrizione della ricetta.
        token = sha256(repr((id_gruppo, firma)).encode('utf8')).hexdigest()[:12]
        for codice, nome in righe:
            risposta[codice] = (id_gruppo, nome, f'{id_gruppo}-{token}')
    return risposta
