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
from decimal import ROUND_HALF_UP, Decimal
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
) -> tuple[list[tuple[PianificazioneFasi, TipologieLavorazione]],
           tuple[PianificazioneFasi, TipologieLavorazione, RicettaFasi]]:
    """Trova *tutte* le fasi di impasto, anche con IdLavorazione ripetuto.

    La singola occorrenza e' IdRicettaFase, non IdLavorazione: la stessa
    lavorazione puo' tornare nella ricetta in blocchi successivi.
    """
    dati = sorted(_dati_fasi(fasi), key=lambda item: item[0].OffsetBlocchi)
    impasti = [(f, l) for f, l, _ in dati if _fase_impasto(l)]
    forni = [(f, l, r) for f, l, r in dati if _fase_forno(l)]
    if not impasti:
        raise ErroreCalcoloPianificazione("La ricetta non contiene fasi di impasto")
    if len(forni) != 1:
        raise ErroreCalcoloPianificazione(
            f"La ricetta deve avere esattamente una fase di cottura (trovate {len(forni)})."
        )

    # La stessa produzione non puo' svolgere due fasi nello stesso blocco,
    # considerando anche le durate maggiori di un blocco.
    occupazione_piano: dict[tuple[date, str], PianificazioneFasi] = {}
    for fase, lavorazione, _ in dati:
        for slot in _blocchi_occupati(
            fase.DataFase, fase.Blocco, int(lavorazione.DurataBlocchi)
        ):
            altra = occupazione_piano.get(slot)
            if altra is not None:
                giorno, blocco = slot
                raise ErroreCalcoloPianificazione(
                    f"Due fasi della stessa produzione si sovrappongono il "
                    f"{giorno:%d/%m/%Y} ({blocco}): fasi ricetta "
                    f"{altra.IdRicettaFase} e {fase.IdRicettaFase}. "
                    "Correggere gli offset o la durata delle lavorazioni."
                )
            occupazione_piano[slot] = fase
    return impasti, forni[0]


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
    escludi_id_pianificazione: int | None = None,
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
            *([PianificazioneProduzione.IdPianificazione != escludi_id_pianificazione]
              if escludi_id_pianificazione is not None else []),
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


def impastatrici_libere(
    impastatrici: Sequence[AnagraficaImpastatrici],
    fasi_impasto: Sequence[tuple[PianificazioneFasi, TipologieLavorazione]],
    escludi_id_pianificazione: int | None = None,
) -> tuple[dict[int, Decimal], list[tuple[PianificazioneFasi, list[tuple[date, str]]]]]:
    """Capacita' in kg delle sole macchine libere in TUTTI i blocchi di impasto.

    Mantiene esattamente la stessa regola di occupazione usata al salvataggio.
    """
    if not fasi_impasto:
        raise ErroreCalcoloPianificazione("La ricetta non contiene fasi di impasto")

    ids = {macchina.IdImpastatrice for macchina in impastatrici}
    if not ids:
        raise ErroreCalcoloPianificazione("Seleziona almeno un'impastatrice")

    blocchi_per_fase: list[tuple[PianificazioneFasi, list[tuple[date, str]]]] = []
    slots_usati: set[tuple[date, str]] = set()
    for fase, lavorazione in fasi_impasto:
        slots = _blocchi_occupati(
            fase.DataFase, fase.Blocco, int(lavorazione.DurataBlocchi)
        )
        if slots_usati.intersection(slots):
            raise ErroreCalcoloPianificazione(
                "Due fasi di impasto si sovrappongono: "
                "la stessa impastatrice non puo' lavorare in due fasi nello stesso blocco"
            )
        slots_usati.update(slots)
        blocchi_per_fase.append((fase, slots))

    occupate = set(
        db.session.execute(
            db.select(
                PianificazioneImpastatriceFase.IdImpastatrice,
                PianificazioneImpastatriceFase.DataFase,
                PianificazioneImpastatriceFase.Blocco,
            )
            .join(PianificazioneFasi,
                  PianificazioneFasi.IdPianificazioneFase == PianificazioneImpastatriceFase.IdPianificazioneFase)
            .where(
                PianificazioneImpastatriceFase.IdImpastatrice.in_(ids),
                *([PianificazioneFasi.IdPianificazione != escludi_id_pianificazione]
                  if escludi_id_pianificazione is not None else []),
            )
        ).all()
    )
    occupate |= _occupazioni_impastatrici_storiche(ids, escludi_id_pianificazione)
    disponibili = {
        macchina.IdImpastatrice: _capacita_kg(macchina)
        for macchina in impastatrici
        if all((macchina.IdImpastatrice, giorno, blocco) not in occupate
               for giorno, blocco in slots_usati)
    }
    return disponibili, blocchi_per_fase


def capacita_impastatrici_pezzi(
    impastatrici: Sequence[AnagraficaImpastatrici],
    fasi_impasto: Sequence[tuple[PianificazioneFasi, TipologieLavorazione]],
    peso_unitario_grammi: Decimal,
) -> int:
    """Numero massimo di pezzi lavorabili con le impastatrici selezionate e libere."""
    peso_unitario = _decimale(peso_unitario_grammi)
    if peso_unitario <= 0:
        raise ErroreCalcoloPianificazione("Peso unitario non valido")
    disponibili, _ = impastatrici_libere(impastatrici, fasi_impasto)
    grammi_liberi = sum(disponibili.values(), Decimal(0)) * Decimal(1000)
    # Nessun arrotondamento in eccesso: non superare i kg disponibili.
    pezzi = int(grammi_liberi // peso_unitario)
    # Il peso di piano e' arrotondato a 0,01 g: protegge anche dai casi limite.
    while pezzi > 0 and (peso_unitario * pezzi).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) > grammi_liberi:
        pezzi -= 1
    return pezzi


def verifica_e_distribuisci_impasti(
    impastatrici: Sequence[AnagraficaImpastatrici],
    peso_totale_grammi: Decimal,
    fasi_impasto: Sequence[tuple[PianificazioneFasi, TipologieLavorazione]],
) -> tuple[dict[int, Decimal], list[tuple[PianificazioneFasi, list[tuple[date, str]]]]]:
    """Prenota la *stessa* selezione di macchine per ogni fase di impasto.

    La massa viene verificata per OGNI passaggio, non sommata fra i passaggi:
    lo stesso impasto puo' essere lavorato una seconda volta. Le occupazioni
    vengono prenotate invece per ogni IdRicettaFase e per ogni blocco.
    """
    disponibili, blocchi_per_fase = impastatrici_libere(impastatrici, fasi_impasto)
    if not disponibili:
        raise ErroreCalcoloPianificazione(
            "Le impastatrici selezionate sono gia' occupate in uno dei blocchi "
            "previsti per le fasi di impasto"
        )

    peso_kg = _decimale(peso_totale_grammi) / Decimal(1000)
    assegnazioni = distribuisci_peso_impastatrici(peso_kg, disponibili)
    return assegnazioni, blocchi_per_fase


def verifica_e_distribuisci_impasto(
    impastatrici: Sequence[AnagraficaImpastatrici],
    peso_totale_grammi: Decimal,
    fase: PianificazioneFasi,
    lavorazione: TipologieLavorazione,
) -> tuple[dict[int, Decimal], list[tuple[date, str]]]:
    """Compatibilita' con i chiamanti che hanno un'unica fase di impasto."""
    assegnazioni, blocchi = verifica_e_distribuisci_impasti(
        impastatrici, peso_totale_grammi, [(fase, lavorazione)]
    )
    return assegnazioni, blocchi[0][1]


def disponibilita_forno(
    fase_forno: PianificazioneFasi,
    lavorazione_forno: TipologieLavorazione,
    ricetta_fase_forno: RicettaFasi,
    id_forno: int,
    escludi_id_pianificazione: int | None = None,
) -> tuple[int, int]:
    """Restituisce (cotte_libere, limite_condiviso) nel blocco piu' saturo.

    Considera anche le occupazioni su piu' blocchi e le pianificazioni esistenti.
    Deve essere invocata prima dell'INSERT e nuovamente prima del commit.
    """
    max_cotte = int(ricetta_fase_forno.CapacitaMax or 0)
    if max_cotte <= 0:
        raise ErroreCalcoloPianificazione("Limite cotte non valido")
    slots_nuovi = set(_blocchi_occupati(
        fase_forno.DataFase, fase_forno.Blocco, int(lavorazione_forno.DurataBlocchi)
    ))
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
        .join(RicettaFasi, RicettaFasi.id == PianificazioneFasi.IdRicettaFase)
        .join(TipologieLavorazione, TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione)
        .where(
            PianificazioneProduzione.IdForno == id_forno,
            PianificazioneProduzione.StatoRigaOrdine != "Annullato",
            *([PianificazioneProduzione.IdPianificazione != escludi_id_pianificazione]
              if escludi_id_pianificazione is not None else []),
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
    return max(0, min(limite_effettivo - n for n in impiegate_per_slot.values())), limite_effettivo


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

    cotte_libere, limite_effettivo = disponibilita_forno(
        fase_forno, lavorazione_forno, ricetta_fase_forno, id_forno
    )
    if numero_cotte > cotte_libere:
        raise ErroreCalcoloPianificazione(
            f"Forno {id_forno}: richieste {numero_cotte} cotte, "
            f"disponibili {cotte_libere} nei blocchi previsti."
        )
    return {
        "cotte_previste": numero_cotte,
        "max_cotte": limite_effettivo,
        "cotte_libere": cotte_libere,
        "peso_max_grammi": capacita_peso_grammi,
    }
