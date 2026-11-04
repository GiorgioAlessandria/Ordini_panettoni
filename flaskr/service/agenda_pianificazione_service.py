"""Agenda compatta dei soli giorni coinvolti dalla nuova pianificazione.

Non crea prenotazioni: le fasi provvisorie sono disegnate a partire da
blocchi_fase(), mentre gli eventi esistenti vengono letti dal database.
Espande le durate con la stessa funzione dei controlli sulle risorse.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    PianificazioneFasi,
    PianificazioneProduzione,
    RicettaFasi,
    TipologieLavorazione,
)
from flaskr.service.risorse_produzione_service import _blocchi_occupati
from flaskr.service.calendario_misto_service import righe_fasi_miste
from flaskr.service.scadenze_giornaliere_service import articolo_cnf

_GIORNI = ("Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom")
_MESI = ("", "gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")


def _categoria_lavorazione(lavorazione: TipologieLavorazione) -> str:
    testo = " ".join(
        str(x or "") for x in (
            lavorazione.TipologiaLavorazione,
            lavorazione.CodiceLavorazione,
            lavorazione.DescrizioneLavorazione,
        )
    ).casefold()
    if "impast" in testo:
        return "impasto"
    if any(parola in testo for parola in ("forno", "cottura", "inforn")):
        return "forno"
    return "altro"


def _descrizione_lavorazione(lavorazione: TipologieLavorazione) -> str:
    return (
        str(lavorazione.DescrizioneLavorazione or "").strip()
        or str(lavorazione.TipologiaLavorazione or "").strip()
        or "Lavorazione"
    )


def organizza_eventi_giorni_interessati(
    fasi_proposte: Sequence[PianificazioneFasi],
    lavorazioni_proposte: Mapping[int, TipologieLavorazione],
    righe_esistenti: Sequence[tuple[PianificazioneFasi, PianificazioneProduzione, TipologieLavorazione]],
    cod_art_proposto: str,
    *,
    mostra_articoli_cnf: bool = True,
    descrizioni_articoli: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Mostra solo i giorni toccati dalle fasi proposte, incluse durate > 1.

    In ognuno mostra entrambi i blocchi per confrontare la bozza con le
    occupazioni già confermate. La bozza non possiede IdPianificazione.
    """
    descrizioni_articoli = descrizioni_articoli or {}
    date_coinvolte: set[date] = set()
    for fase in fasi_proposte:
        lavorazione = lavorazioni_proposte[fase.IdLavorazione]
        date_coinvolte.update(
            giorno for giorno, _ in _blocchi_occupati(
                fase.DataFase, fase.Blocco, int(lavorazione.DurataBlocchi)
            )
        )

    giorni = [
        {
            "data": giorno,
            "giorno": _GIORNI[giorno.weekday()],
            "mese": _MESI[giorno.month],
            "slot": {"M": [], "P": []},
        }
        for giorno in sorted(date_coinvolte)
    ]
    per_data = {giorno["data"]: giorno for giorno in giorni}

    for fase, piano, lavorazione in righe_esistenti:
        if not mostra_articoli_cnf and articolo_cnf(piano.CodArt):
            continue
        categoria = _categoria_lavorazione(lavorazione)
        evento = {
            "bozza": False,
            "id_pianificazione": piano.IdPianificazione,
            "cod_art": piano.CodArt or "",
            "descrizione_articolo": descrizioni_articoli.get(piano.CodArt) or piano.CodArt or "Descrizione non disponibile",
            "descrizione": _descrizione_lavorazione(lavorazione),
            "categoria": categoria,
            "cotte": piano.NumeroCotte if categoria == "forno" else None,
            "forno": piano.IdForno if categoria == "forno" else None,
        }
        for giorno_slot, blocco in _blocchi_occupati(
            fase.DataFase, fase.Blocco, int(lavorazione.DurataBlocchi)
        ):
            if giorno_slot in per_data:
                per_data[giorno_slot]["slot"][blocco].append(evento)

    for fase in fasi_proposte:
        lavorazione = lavorazioni_proposte[fase.IdLavorazione]
        evento = {
            "bozza": True,
            "id_pianificazione": None,
            "cod_art": cod_art_proposto,
            "descrizione_articolo": descrizioni_articoli.get(cod_art_proposto) or cod_art_proposto or "Descrizione non disponibile",
            "descrizione": _descrizione_lavorazione(lavorazione),
            "categoria": _categoria_lavorazione(lavorazione),
            "fase_ricetta": fase.IdRicettaFase,
            "cotte": None,
            "forno": None,
        }
        for giorno_slot, blocco in _blocchi_occupati(
            fase.DataFase, fase.Blocco, int(lavorazione.DurataBlocchi)
        ):
            per_data[giorno_slot]["slot"][blocco].append(evento)

    for giorno in giorni:
        for blocco in ("M", "P"):
            giorno["slot"][blocco].sort(
                key=lambda e: (e["bozza"], e["id_pianificazione"] or 0, e["descrizione"])
            )
    return giorni


def carica_agenda_giorni_interessati(
    fasi_proposte: Sequence[PianificazioneFasi],
    fasi_ricetta: Sequence[RicettaFasi],
    cod_art_proposto: str,
    *,
    mostra_articoli_cnf: bool = True,
) -> dict[str, Any]:
    """Legge gli eventi che si sovrappongono ai giorni della nuova ricetta.

    La data/blocco di ogni fase proposta è già stata calcolata da blocchi_fase.
    Nessun INSERT/UPDATE; le fasi arancioni sono esclusivamente un'anteprima.
    """
    lavorazioni = {fase.IdLavorazione: fase.lavorazione for fase in fasi_ricetta}
    date_interessate = {
        giorno
        for fase in fasi_proposte
        for giorno, _ in _blocchi_occupati(
            fase.DataFase,
            fase.Blocco,
            int(lavorazioni[fase.IdLavorazione].DurataBlocchi),
        )
    }
    if not date_interessate:
        return {"giorni": [], "selezionata": None}

    prima = min(date_interessate)
    ultima = max(date_interessate)
    durata_massima = db.session.scalar(db.select(db.func.max(TipologieLavorazione.DurataBlocchi)))
    giorni_precedenti = (max(1, int(durata_massima or 1)) + 1) // 2

    stmt = (
        db.select(PianificazioneFasi, PianificazioneProduzione, TipologieLavorazione)
        .join(
            PianificazioneProduzione,
            PianificazioneProduzione.IdPianificazione == PianificazioneFasi.IdPianificazione,
        )
        .join(
            TipologieLavorazione,
            TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione,
        )
        .where(
            PianificazioneFasi.DataFase >= prima - timedelta(days=giorni_precedenti),
            PianificazioneFasi.DataFase <= ultima,
            PianificazioneProduzione.StatoRigaOrdine != "Annullato",
        )
        .order_by(
            PianificazioneFasi.DataFase,
            PianificazioneFasi.Blocco,
            PianificazioneFasi.IdPianificazione,
        )
    )
    righe = db.session.execute(stmt).all()
    # Un'unica query per le descrizioni degli articoli dei piani presenti e della bozza.
    codici = {piano.CodArt for _, piano, _ in righe if piano.CodArt}
    if cod_art_proposto:
        codici.add(cod_art_proposto)
    descrizioni_articoli = {}
    if codici:
        descrizioni_articoli = {
            a.CodArt: a.DesArt
            for a in db.session.scalars(
                db.select(AnagraficaArticoli).where(AnagraficaArticoli.CodArt.in_(codici))
            ).all()
        }
    giorni = organizza_eventi_giorni_interessati(
        fasi_proposte, lavorazioni, righe, cod_art_proposto,
        mostra_articoli_cnf=mostra_articoli_cnf,
        descrizioni_articoli=descrizioni_articoli,
    )
    per_giorno = {giorno["data"]: giorno for giorno in giorni}
    # Le prenotazioni miste sono gia' espanse per slot. Il servizio del
    # calendario raggruppa le cotture dei forni nello stesso blocco.
    ruolo_misto = 'utente_produzione' if not mostra_articoli_cnf else ''
    for evento in righe_fasi_miste(prima, ultima + timedelta(days=1), ruolo_misto):
        giorno = per_giorno.get(evento['giorno'])
        if giorno is None:
            continue
        giorno['slot'][evento['blocco']].append({
            'bozza': False, 'tipo_misto': True,
            'id_bozza': evento['id_bozza'],
            'id_pianificazione': evento['id_grafico'],
            'cod_art': '',
            'descrizione_articolo': evento['descrizione'],
            'descrizione': evento['lavorazione'],
            'categoria': 'forno' if evento.get('forni_dettaglio') else 'impasto',
            'cotte': evento['numero_cotte'] if evento.get('forni_dettaglio') else None,
            'forno': evento['forno'] if evento.get('forni_dettaglio') else None,
        })
    for giorno in giorni:
        for blocco in ('M', 'P'):
            giorno['slot'][blocco].sort(
                key=lambda e: (e['bozza'], e['id_pianificazione'] or 0, e['descrizione'])
            )
    return {'selezionata': min(f.DataFase for f in fasi_proposte), 'giorni': giorni}
