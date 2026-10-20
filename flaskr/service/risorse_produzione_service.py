"""Validazione e assegnazione risorse produttive per data/blocco.

Convenzioni del progetto:
- PesoTotale di PianificazioneProduzione è in GRAMMI.
- CapienzaImpastatrice è nell'unità specificata in UdM.
- Capienza di CapacitaForno è espressa in PEZZI PER COTTA.
- CapacitaMax della fase forno è il massimo di COTTE nel suo blocco.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaImpastatrici,
    PianificazioneFasi,
    PianificazioneImpastatriceFase,
    PianificazioneOrdiniImpastatrici,
    PianificazioneProduzione,
    RicettaFasi,
    TipologieLavorazione,
)
from flaskr.service.pianificazione_service import ErroreCalcoloPianificazione


def _decimale(valore: object) -> Decimal:
    return Decimal(str(valore))


def _capacita_kg(impastatrice: AnagraficaImpastatrici) -> Decimal:
    valore = _decimale(impastatrice.CapienzaImpastatrice)
    unita = str(impastatrice.UdM or "").strip().lower()
    if unita in ("kg", "chilogrammi", "chilogrammo"):
        capacita = valore
    elif unita in ("g", "gr", "grammi", "grammo"):
        capacita = valore / Decimal(1000)
    else:
        raise ErroreCalcoloPianificazione(
            f"Unità di misura non supportata per l'impastatrice {impastatrice.IdImpastatrice}: "
            f"{impastatrice.UdM!r}. Usare kg oppure g."
        )
    if capacita <= 0:
        raise ErroreCalcoloPianificazione(
            f"Capacità non valida per l'impastatrice {impastatrice.IdImpastatrice}"
        )
    return capacita


def _nome_lavorazione(lavorazione: TipologieLavorazione) -> str:
    return " ".join(
        str(valore or "") for valore in (
            lavorazione.TipologiaLavorazione,
            lavorazione.CodiceLavorazione,
            lavorazione.DescrizioneLavorazione,
        )
    ).casefold()


def _fase_impasto(lavorazione: TipologieLavorazione) -> bool:
    return "impast" in _nome_lavorazione(lavorazione)


def _fase_forno(lavorazione: TipologieLavorazione) -> bool:
    nome = _nome_lavorazione(lavorazione)
    return "forno" in nome or "cottura" in nome or "inforn" in nome


def _blocchi_occupati(data_fase: date, blocco: str, durata_blocchi: int) -> list[tuple[date, str]]:
    """Espande la durata di una lavorazione (M -> P -> M del giorno seguente)."""
    if blocco not in ("M", "P") or durata_blocchi <= 0:
        raise ErroreCalcoloPianificazione("Blocco o durata della lavorazione non validi")
    indice_inizio = 0 if blocco == "M" else 1
    return [
        (data_fase + timedelta(days=(indice_inizio + i) // 2),
         "M" if (indice_inizio + i) % 2 == 0 else "P")
        for i in range(durata_blocchi)
    ]


def _dati_fasi(fasi: Sequence[PianificazioneFasi]) -> list[tuple[PianificazioneFasi, TipologieLavorazione, RicettaFasi]]:
    if not fasi:
        raise ErroreCalcoloPianificazione("Nessuna fase pianificata")
    ids_ricetta_fase = {f.IdRicettaFase for f in fasi}
    ricette = {
        r.id: r
        for r in db.session.scalars(
            db.select(RicettaFasi).where(RicettaFasi.id.in_(ids_ricetta_fase))
        ).all()
    }
    ids_lavorazione = {f.IdLavorazione for f in fasi}
    lavorazioni = {
        l.IdLavorazione: l
        for l in db.session.scalars(
            db.select(TipologieLavorazione).where(
                TipologieLavorazione.IdLavorazione.in_(ids_lavorazione)
            )
        ).all()
    }
    try:
        return [(f, lavorazioni[f.IdLavorazione], ricette[f.IdRicettaFase]) for f in fasi]
    except KeyError as err:
        raise ErroreCalcoloPianificazione(f"Fase o lavorazione non configurata: {err}") from err


def individua_fasi_risorse(
    fasi: Sequence[PianificazioneFasi],
) -> tuple[tuple[PianificazioneFasi, TipologieLavorazione],
           tuple[PianificazioneFasi, TipologieLavorazione, RicettaFasi]]:
    dati = _dati_fasi(fasi)
    impasti = [(f, l) for f, l, _ in dati if _fase_impasto(l)]
    forni = [(f, l, r) for f, l, r in dati if _fase_forno(l)]
    if len(impasti) != 1:
        raise ErroreCalcoloPianificazione(
            f"La ricetta deve avere esattamente una fase di impasto (trovate {len(impasti)}). "
            "Verificare TipologiaLavorazione/DescrizioneLavorazione."
        )
    if len(forni) != 1:
        raise ErroreCalcoloPianificazione(
            f"La ricetta deve avere esattamente una fase di cottura (trovate {len(forni)})."
        )
    return impasti[0], forni[0]


def distribuisci_peso_impastatrici(
    peso_kg: Decimal,
    capacita_per_macchina: dict[int, Decimal],
) -> dict[int, Decimal]:
    """Usa il minor numero di macchine; a parità, minimizza capacità inutilizzata.

    Per n impastatrici fino a 16 cerca la combinazione ottima. Oltre 16 usa
    un algoritmo deterministico di ripiego, che resta corretto sulla capacità.
    """
    peso_kg = _decimale(peso_kg)
    if peso_kg <= 0:
        raise ErroreCalcoloPianificazione("Peso dell'impasto non positivo")
    capacita = {i: _decimale(kg) for i, kg in capacita_per_macchina.items() if _decimale(kg) > 0}
    totale = sum(capacita.values(), Decimal(0))
    if totale < peso_kg:
        raise ErroreCalcoloPianificazione(
            f"Impasto da produrre: {peso_kg:.2f} kg; capacità libera selezionata: "
            f"{totale:.2f} kg. Mancano {peso_kg - totale:.2f} kg."
        )
    macchine = sorted(capacita)
    if len(macchine) <= 16:
        scelta: tuple[int, ...] | None = None
        for n in range(1, len(macchine) + 1):
            possibili = (
                gruppo for gruppo in combinations(macchine, n)
                if sum((capacita[m] for m in gruppo), Decimal(0)) >= peso_kg
            )
            scelta = min(
                possibili,
                key=lambda gruppo: (sum((capacita[m] for m in gruppo), Decimal(0)), gruppo),
                default=None,
            )
            if scelta is not None:
                break
        assert scelta is not None
    else:
        scelta_lista = []
        restante = peso_kg
        for m in sorted(macchine, key=lambda x: (-capacita[x], x)):
            scelta_lista.append(m)
            restante -= capacita[m]
            if restante <= 0:
                break
        scelta = tuple(scelta_lista)
    assegnazioni = {}
    restante = peso_kg
    for m in sorted(scelta, key=lambda x: (-capacita[x], x)):
        quota = min(restante, capacita[m])
        if quota > 0:
            assegnazioni[m] = quota
            restante -= quota
    if restante != 0:
        raise ErroreCalcoloPianificazione("Errore nella distribuzione dell'impasto")
    return assegnazioni


def _occupazioni_impastatrici_storiche(
    ids_macchine: set[int],
) -> set[tuple[int, date, str]]:
    """Protegge le pianificazioni precedenti prive di prenotazioni dettagliate.

    Conserviamo PianificazioneOrdiniImpastatrici per compatibilità con calendario
    e versioni precedenti. Ne controlliamo l'occupazione finché esistono dati legacy.
    """
    righe = db.session.execute(
        db.select(
            PianificazioneOrdiniImpastatrici.IdImpastatrice,
            PianificazioneFasi.DataFase,
            PianificazioneFasi.Blocco,
            TipologieLavorazione.DurataBlocchi,
            TipologieLavorazione.TipologiaLavorazione,
            TipologieLavorazione.CodiceLavorazione,
            TipologieLavorazione.DescrizioneLavorazione,
        )
        .join(
            PianificazioneProduzione,
            PianificazioneProduzione.IdPianificazione == PianificazioneOrdiniImpastatrici.IdPianificazione,
        )
        .join(
            PianificazioneFasi,
            PianificazioneFasi.IdPianificazione == PianificazioneProduzione.IdPianificazione,
        )
        .join(
            TipologieLavorazione,
            TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione,
        )
        .where(
            PianificazioneOrdiniImpastatrici.IdImpastatrice.in_(ids_macchine),
            PianificazioneProduzione.StatoRigaOrdine != "Annullato",
        )
    ).all()
    occupate = set()
    for macchina, giorno, blocco, durata, categoria, codice, descrizione in righe:
        nome = " ".join(str(v or "") for v in (categoria, codice, descrizione)).casefold()
        if "impast" not in nome:
            continue
        for data_slot, blocco_slot in _blocchi_occupati(giorno, blocco, int(durata)):
            occupate.add((macchina, data_slot, blocco_slot))
    return occupate


def verifica_e_distribuisci_impasto(
    impastatrici: Sequence[AnagraficaImpastatrici],
    peso_totale_grammi: Decimal,
    fase: PianificazioneFasi,
    lavorazione: TipologieLavorazione,
) -> tuple[dict[int, Decimal], list[tuple[date, str]]]:
    slots = _blocchi_occupati(fase.DataFase, fase.Blocco, int(lavorazione.DurataBlocchi))
    ids = {m.IdImpastatrice for m in impastatrici}
    if not ids:
        raise ErroreCalcoloPianificazione("Seleziona almeno un'impastatrice")
    occupate = set(
        db.session.execute(
            db.select(
                PianificazioneImpastatriceFase.IdImpastatrice,
                PianificazioneImpastatriceFase.DataFase,
                PianificazioneImpastatriceFase.Blocco,
            ).where(PianificazioneImpastatriceFase.IdImpastatrice.in_(ids))
        ).all()
    )
    occupate |= _occupazioni_impastatrici_storiche(ids)
    disponibili = {
        m.IdImpastatrice: _capacita_kg(m)
        for m in impastatrici
        if all((m.IdImpastatrice, giorno, blocco) not in occupate for giorno, blocco in slots)
    }
    if not disponibili:
        raise ErroreCalcoloPianificazione(
            "Le impastatrici selezionate sono già occupate nei blocchi previsti per l'impasto"
        )
    peso_kg = _decimale(peso_totale_grammi) / Decimal(1000)
    return distribuisci_peso_impastatrici(peso_kg, disponibili), slots


def verifica_capacita_forno(
    fase_forno: PianificazioneFasi,
    lavorazione_forno: TipologieLavorazione,
    ricetta_fase_forno: RicettaFasi,
    id_forno: int,
    capienza_cotta: int,
    numero_cotte: int,
    peso_totale_grammi: Decimal,
    peso_unitario_grammi: Decimal,
) -> dict[str, object]:
    max_cotte = int(ricetta_fase_forno.CapacitaMax)
    if max_cotte <= 0 or capienza_cotta <= 0 or numero_cotte <= 0 or peso_unitario_grammi <= 0:
        raise ErroreCalcoloPianificazione("Parametri di capacità forno non validi")
    slots_nuovi = set(_blocchi_occupati(
        fase_forno.DataFase, fase_forno.Blocco, int(lavorazione_forno.DurataBlocchi)
    ))
    # Verifica esplicita anche in massa, senza assumere che il peso unitario sia 1 kg.
    capacita_peso_grammi = _decimale(peso_unitario_grammi) * capienza_cotta * max_cotte
    if _decimale(peso_totale_grammi) > capacita_peso_grammi:
        raise ErroreCalcoloPianificazione(
            f"Impasto {(_decimale(peso_totale_grammi) / 1000):.2f} kg superiore "
            f"alla capacità di {max_cotte} cotte "
            f"({(capacita_peso_grammi / 1000):.2f} kg)."
        )
    if numero_cotte > max_cotte:
        raise ErroreCalcoloPianificazione(
            f"Sono previste {numero_cotte} cotte ma il limite è {max_cotte} per blocco"
        )

    # Il limite delle cotte è condiviso da TUTTI i piani sullo stesso forno/blocco.
    righe = db.session.execute(
        db.select(
            PianificazioneFasi.IdPianificazioneFase,
            PianificazioneFasi.DataFase,
            PianificazioneFasi.Blocco,
            PianificazioneProduzione.NumeroCotte,
            RicettaFasi.CapacitaMax,
            TipologieLavorazione.DurataBlocchi,
            TipologieLavorazione.TipologiaLavorazione,
            TipologieLavorazione.CodiceLavorazione,
            TipologieLavorazione.DescrizioneLavorazione,
        )
        .join(
            PianificazioneProduzione,
            PianificazioneProduzione.IdPianificazione == PianificazioneFasi.IdPianificazione,
        )
        .join(
            RicettaFasi,
            RicettaFasi.id == PianificazioneFasi.IdRicettaFase,
        )
        .join(
            TipologieLavorazione,
            TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione,
        )
        .where(
            PianificazioneProduzione.IdForno == id_forno,
            PianificazioneProduzione.StatoRigaOrdine != "Annullato",
        )
    ).all()
    impiegate_per_slot = {slot: 0 for slot in slots_nuovi}
    limite_effettivo = max_cotte
    for _id, data_fase, blocco, cotte, limite_esistente, durata, categoria, codice, descrizione in righe:
        nome = " ".join(str(v or "") for v in (categoria, codice, descrizione)).casefold()
        if not any(s in nome for s in ("forno", "cottura", "inforn")):
            continue
        for slot in _blocchi_occupati(data_fase, blocco, int(durata)):
            if slot in impiegate_per_slot:
                impiegate_per_slot[slot] += int(cotte)
                if limite_esistente is not None:
                    limite_effettivo = min(limite_effettivo, int(limite_esistente))
    for (data, blocco), gia_usate in sorted(impiegate_per_slot.items()):
        if gia_usate + numero_cotte > limite_effettivo:
            raise ErroreCalcoloPianificazione(
                f"Forno {id_forno} occupato il {data:%d/%m/%Y} ({blocco}): "
                f"{gia_usate} cotte già pianificate + {numero_cotte} richieste "
                f"superano il massimo condiviso di {limite_effettivo}."
            )
    return {
        "cotte_previste": numero_cotte,
        "max_cotte": limite_effettivo,
        "cotte_libere": min(limite_effettivo - n for n in impiegate_per_slot.values()),
        "peso_max_grammi": capacita_peso_grammi,
    }
