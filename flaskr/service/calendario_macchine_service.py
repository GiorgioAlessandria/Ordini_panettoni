"""Calendario settimanale delle impastatrici prenotate per blocco M/P.

Dati da PianificazioneImpastatriceFase (kg/blocco) con fallback legacy
PianificazioneOrdiniImpastatrici (occupazione senza kg verificabili).

Le percentuali sono di CARICO PIANIFICATO, non misure reali di utilizzo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaImpastatrici,
    AnagraficaArticoli,
    PianificazioneFasi,
    PianificazioneImpastatriceFase,
    PianificazioneOrdiniImpastatrici,
    PianificazioneProduzione,
    TipologieLavorazione,
)
from flaskr.service.risorse_produzione_service import _blocchi_occupati, _capacita_kg

ZERO = Decimal("0")
CENTO = Decimal("100")


def _percentuale(parte: Decimal, totale: Decimal) -> Decimal:
    if totale <= 0:
        return ZERO
    return (parte * CENTO / totale).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _nome_macchina(macchina: AnagraficaImpastatrici) -> str:
    return (
        str(macchina.DescrizioneImpastatrice or "").strip()
        or str(macchina.ModelloImpastatrice or "").strip()
        or f"Impastatrice {macchina.IdImpastatrice}"
    )


def _descrizione_fase(categoria: object, codice: object, descrizione: object) -> str:
    return " ".join(str(v or "") for v in (categoria, codice, descrizione)).casefold()


def riepilogo_settimana_macchine(data_richiesta: date) -> dict:
    """Ritorna tutte le 14 celle settimana/blocco, con macchine e KPI.

    La stessa quota KgAssegnati viene mostrata per ogni blocco di una fase
    pluriblocco: non rappresenta nuovi kg prodotti in ogni blocco.
    """
    lunedi = data_richiesta - timedelta(days=data_richiesta.weekday())
    esclusiva_fine = lunedi + timedelta(days=7)
    macchine_db = db.session.scalars(
        db.select(AnagraficaImpastatrici).order_by(AnagraficaImpastatrici.IdImpastatrice)
    ).all()
    capacita = {m.IdImpastatrice: _capacita_kg(m) for m in macchine_db}
    macchina_per_id = {m.IdImpastatrice: m for m in macchine_db}
    totale_capacita = sum(capacita.values(), ZERO)
    prenotazioni: dict[tuple[date, str, int], dict] = {}

    # Prenotazioni con kg esatti: una macchina/blocco è UNIQUE nel database.
    nuove = db.session.execute(
        db.select(
            PianificazioneImpastatriceFase.IdImpastatrice,
            PianificazioneImpastatriceFase.DataFase,
            PianificazioneImpastatriceFase.Blocco,
            PianificazioneImpastatriceFase.KgAssegnati,
            PianificazioneFasi.IdPianificazione,
            PianificazioneProduzione.CodArt,
            AnagraficaArticoli.DesArt,
        )
        .join(
            PianificazioneFasi,
            PianificazioneFasi.IdPianificazioneFase == PianificazioneImpastatriceFase.IdPianificazioneFase,
        )
        .join(
            PianificazioneProduzione,
            PianificazioneProduzione.IdPianificazione == PianificazioneFasi.IdPianificazione,
        )
        .outerjoin(AnagraficaArticoli, AnagraficaArticoli.CodArt == PianificazioneProduzione.CodArt)
        .where(
            PianificazioneImpastatriceFase.DataFase >= lunedi,
            PianificazioneImpastatriceFase.DataFase < esclusiva_fine,
            PianificazioneProduzione.StatoRigaOrdine != "Annullato",
        )
    ).all()
    for id_macchina, giorno, blocco, kg, piano, cod_art, descrizione in nuove:
        if id_macchina not in macchina_per_id:
            continue
        prenotazioni[(giorno, blocco, id_macchina)] = {
            "id_piano": piano,
            "articolo": descrizione or cod_art or "Articolo non disponibile",
            "kg": Decimal(str(kg)),
            "legacy": False,
        }

    # Identifica i piani con prenotazioni moderne anche fuori settimana:
    # non duplicare le associazioni compatibili mantenute in tabella legacy.
    piani_moderni = set(db.session.scalars(
        db.select(PianificazioneFasi.IdPianificazione)
        .join(
            PianificazioneImpastatriceFase,
            PianificazioneImpastatriceFase.IdPianificazioneFase == PianificazioneFasi.IdPianificazioneFase,
        )
        .distinct()
    ).all())

    legacy = db.session.execute(
        db.select(
            PianificazioneOrdiniImpastatrici.IdImpastatrice,
            PianificazioneOrdiniImpastatrici.IdPianificazione,
            PianificazioneFasi.DataFase,
            PianificazioneFasi.Blocco,
            TipologieLavorazione.DurataBlocchi,
            TipologieLavorazione.TipologiaLavorazione,
            TipologieLavorazione.CodiceLavorazione,
            TipologieLavorazione.DescrizioneLavorazione,
            PianificazioneProduzione.CodArt,
            AnagraficaArticoli.DesArt,
        )
        .join(
            PianificazioneFasi,
            PianificazioneFasi.IdPianificazione == PianificazioneOrdiniImpastatrici.IdPianificazione,
        )
        .join(
            PianificazioneProduzione,
            PianificazioneProduzione.IdPianificazione == PianificazioneOrdiniImpastatrici.IdPianificazione,
        )
        .join(
            TipologieLavorazione,
            TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione,
        )
        .outerjoin(AnagraficaArticoli, AnagraficaArticoli.CodArt == PianificazioneProduzione.CodArt)
        .where(PianificazioneProduzione.StatoRigaOrdine != "Annullato")
    ).all()
    for id_macchina, piano, data, blocco, durata, categoria, codice, nomefase, cod_art, descrizione in legacy:
        if piano in piani_moderni or id_macchina not in macchina_per_id:
            continue
        if "impast" not in _descrizione_fase(categoria, codice, nomefase):
            continue
        for slot_giorno, slot_blocco in _blocchi_occupati(data, blocco, int(durata)):
            if not lunedi <= slot_giorno < esclusiva_fine:
                continue
            key = (slot_giorno, slot_blocco, id_macchina)
            prenotazioni.setdefault(key, {
                "id_piano": piano,
                "articolo": descrizione or cod_art or "Articolo non disponibile",
                "kg": None,
                "legacy": True,
            })

    # Espone le due viste separate per chiarezza: macchine occupate e carico kg.
    giorni = []
    slot_settimanali = []
    for offset in range(7):
        giorno = lunedi + timedelta(days=offset)
        blocchi = []
        for blocco, etichetta in (("M", "Mattina"), ("P", "Pomeriggio")):
            righe_macchina = []
            occupate = 0
            totale_kg_noti = ZERO
            contiene_legacy = False
            for macchina in macchine_db:
                id_macchina = macchina.IdImpastatrice
                attiva = prenotazioni.get((giorno, blocco, id_macchina))
                kg = attiva["kg"] if attiva else None
                legacy_senza_kg = bool(attiva and attiva["legacy"])
                if attiva:
                    occupate += 1
                if kg is not None:
                    totale_kg_noti += kg
                contiene_legacy |= legacy_senza_kg
                righe_macchina.append({
                    "id": id_macchina,
                    "nome": _nome_macchina(macchina),
                    "capacita_kg": capacita[id_macchina],
                    "occupata": attiva is not None,
                    "kg": kg,
                    "saturazione": _percentuale(kg, capacita[id_macchina]) if kg is not None else None,
                    "piano": attiva["id_piano"] if attiva else None,
                    "articolo": attiva["articolo"] if attiva else None,
                    "legacy": legacy_senza_kg,
                })
            numero_macchine = len(macchine_db)
            riepilogo_blocco = {
                "giorno": giorno,
                "blocco": blocco,
                "nome": etichetta,
                "macchine": righe_macchina,
                "numero_macchine": numero_macchine,
                "occupate": occupate,
                "libere": numero_macchine - occupate,
                "percentuale_occupazione": _percentuale(Decimal(occupate), Decimal(numero_macchine)),
                "kg_noti": totale_kg_noti,
                "capacita_totale": totale_capacita,
                "saturazione": None if contiene_legacy else _percentuale(totale_kg_noti, totale_capacita),
                "contiene_legacy": contiene_legacy,
            }
            blocchi.append(riepilogo_blocco)
            slot_settimanali.append(riepilogo_blocco)
        giorni.append({"data": giorno, "blocchi": blocchi})

    # KPI individuali su tutti i blocchi della settimana (compresi i weekend).
    statistiche_macchine = []
    for indice, macchina in enumerate(macchine_db):
        voci = [slot["macchine"][indice] for slot in slot_settimanali]
        occupazioni = sum(int(v["occupata"]) for v in voci)
        peso_kg_blocchi = sum((v["kg"] for v in voci if v["kg"] is not None), ZERO)
        incomplete = any(v["legacy"] for v in voci)
        statistiche_macchine.append({
            "id": macchina.IdImpastatrice,
            "nome": _nome_macchina(macchina),
            "capacita_kg": capacita[macchina.IdImpastatrice],
            "blocchi_occupati": occupazioni,
            "blocchi_totali": len(slot_settimanali),
            "percentuale_occupazione": _percentuale(Decimal(occupazioni), Decimal(len(slot_settimanali))),
            "saturazione_media": None if incomplete else _percentuale(
                peso_kg_blocchi, capacita[macchina.IdImpastatrice] * len(slot_settimanali)
            ),
            "dati_incompleti": incomplete,
        })

    totale_macchine_blocco = len(macchine_db) * len(slot_settimanali)
    totale_blocchi_occupati = sum(s["occupate"] for s in slot_settimanali)
    dato_kg_incompleto = any(s["contiene_legacy"] for s in slot_settimanali)
    kg_settimana = sum((s["kg_noti"] for s in slot_settimanali), ZERO)
    return {
        "lunedi": lunedi,
        "fine": lunedi + timedelta(days=6),
        "precedente": lunedi - timedelta(days=7),
        "successiva": lunedi + timedelta(days=7),
        "giorni": giorni,
        "statistiche_macchine": statistiche_macchine,
        "slot": slot_settimanali,
        "numero_macchine": len(macchine_db),
        "capacita_nominale": totale_capacita,
        "totale_macchine_blocco": totale_macchine_blocco,
        "blocchi_occupati": totale_blocchi_occupati,
        "occupazione_settimana": _percentuale(Decimal(totale_blocchi_occupati), Decimal(totale_macchine_blocco)),
        "carico_kg_blocchi": kg_settimana,
        "saturazione_settimana": None if dato_kg_incompleto else _percentuale(kg_settimana, totale_capacita * len(slot_settimanali)),
        "dati_kg_incompleti": dato_kg_incompleto,
    }
