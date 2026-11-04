"""Esportazione calendario settimanale in un solo PDF A3 orizzontale.

I dati sono letti dal DB, inclusi gli eventi che sul monitor scorrono nella griglia.
Nessuna seconda pagina: il numero di righe e la tipografia si adattano alla densità.
Dipendenza aggiuntiva: reportlab (uv pip install reportlab).
"""
from __future__ import annotations

import colorsys
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    AnagraficaForni,
    AnagraficaImpastatrici,
    PianificazioneFasi,
    PianificazioneImpastatriceFase,
    PianificazioneOrdini,
    PianificazioneOrdiniImpastatrici,
    PianificazioneProduzione,
    RicettaFasi,
    TipologieLavorazione,
)
from flaskr.service.scadenze_giornaliere_service import articolo_cnf
from flaskr.service.calendario_misto_service import righe_pdf_miste

MARGINE = 14
ASSE_ORE = 37
GAP_GIORNI = 2
GIORNI = ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica")


def _nome_macchina(macchina: AnagraficaImpastatrici) -> str:
    """Preferisce la descrizione; mostra modello o ID solo come alternativa."""
    descr = (macchina.DescrizioneImpastatrice or "").strip()
    modello = (macchina.ModelloImpastatrice or "").strip()
    return descr or modello or f"Impastatrice #{macchina.IdImpastatrice}"


def _nome_forno(forno: AnagraficaForni) -> str:
    """Preferisce la descrizione; mostra modello o ID solo come alternativa."""
    descr = (forno.DescrizioneForno or "").strip()
    modello = (forno.ModelloForno or "").strip()
    return descr or modello or f"Forno #{forno.IdForno}"


def leggi_fasi_settimana(inizio: date, fine: date, ruolo: str) -> list[dict]:
    """Stessa semantica di visibilità del calendario HTML, in poche query."""
    stmt = (
        db.select(
            PianificazioneFasi,
            PianificazioneProduzione,
            TipologieLavorazione,
            AnagraficaArticoli.DesArt,
            RicettaFasi,
        )
        .join(
            PianificazioneProduzione,
            PianificazioneProduzione.IdPianificazione == PianificazioneFasi.IdPianificazione,
        )
        .join(
            TipologieLavorazione,
            TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione,
        )
        .outerjoin(
            AnagraficaArticoli,
            AnagraficaArticoli.CodArt == PianificazioneProduzione.CodArt,
        )
        .outerjoin(RicettaFasi, RicettaFasi.id == PianificazioneFasi.IdRicettaFase)
        .where(
            PianificazioneFasi.DataFase >= inizio,
            PianificazioneFasi.DataFase < fine,
            PianificazioneProduzione.StatoRigaOrdine != "Annullato",
        )
        .order_by(
            PianificazioneFasi.DataFase,
            PianificazioneFasi.Blocco,
            PianificazioneProduzione.IdPianificazione,
        )
    )
    righe = db.session.execute(stmt).all()
    ids = {p.IdPianificazione for _, p, _, _, _ in righe}
    if not ids:
        return righe_pdf_miste(inizio, fine, ruolo)
    # Una query per i forni, una per le assegnazioni puntuali per fase e
    # una per gli abbinamenti storici per piano: nessuna query per scheda PDF.
    ids_forni = {p.IdForno for _, p, _, _, _ in righe if p.IdForno is not None}
    forni = {}
    if ids_forni:
        for forno in db.session.scalars(
            db.select(AnagraficaForni).where(AnagraficaForni.IdForno.in_(ids_forni))
        ):
            forni[forno.IdForno] = _nome_forno(forno)

    macchine_per_fase = defaultdict(list)
    ids_fasi = {f.IdPianificazioneFase for f, _, _, _, _ in righe}
    if ids_fasi:
        stmt_macchine_fase = (
            db.select(PianificazioneImpastatriceFase.IdPianificazioneFase, AnagraficaImpastatrici)
            .join(AnagraficaImpastatrici,
                  AnagraficaImpastatrici.IdImpastatrice == PianificazioneImpastatriceFase.IdImpastatrice)
            .where(PianificazioneImpastatriceFase.IdPianificazioneFase.in_(ids_fasi))
            .order_by(PianificazioneImpastatriceFase.IdPianificazioneFase,
                      AnagraficaImpastatrici.IdImpastatrice)
        )
        for id_fase, macchina in db.session.execute(stmt_macchine_fase):
            nome = _nome_macchina(macchina)
            if nome not in macchine_per_fase[id_fase]:
                macchine_per_fase[id_fase].append(nome)

    macchine_per_piano = defaultdict(list)
    stmt_macchine_piano = (
        db.select(PianificazioneOrdiniImpastatrici.IdPianificazione, AnagraficaImpastatrici)
        .join(AnagraficaImpastatrici,
              AnagraficaImpastatrici.IdImpastatrice == PianificazioneOrdiniImpastatrici.IdImpastatrice)
        .where(PianificazioneOrdiniImpastatrici.IdPianificazione.in_(ids))
        .order_by(PianificazioneOrdiniImpastatrici.IdPianificazione,
                  AnagraficaImpastatrici.IdImpastatrice)
    )
    for id_piano, macchina in db.session.execute(stmt_macchine_piano):
        nome = _nome_macchina(macchina)
        if nome not in macchine_per_piano[id_piano]:
            macchine_per_piano[id_piano].append(nome)

    associazioni = db.session.scalars(
        db.select(PianificazioneOrdini).where(
            PianificazioneOrdini.IdPianificazione.in_(ids),
            PianificazioneOrdini.Stato.in_(("In produzione", "Chiuso")),
        )
    ).all()
    quantita = defaultdict(int)
    num_ordini = defaultdict(int)
    for link in associazioni:
        quantita[link.IdPianificazione] += int(link.QtaPianificata or 0)
        num_ordini[link.IdPianificazione] += 1
    eventi = []
    for fase, piano, lavorazione, descrizione, ricetta_fase in righe:
        if not num_ordini[piano.IdPianificazione]:
            continue
        if ruolo == "utente_produzione" and articolo_cnf(piano.CodArt):
            continue
        if fase.Blocco not in ("M", "P"):
            continue
        colore = getattr(ricetta_fase, "Colore", None)
        eventi.append({
            "giorno": fase.DataFase,
            "blocco": fase.Blocco,
            "piano": piano.IdPianificazione,
            "fase": getattr(ricetta_fase, "IdFase", None),
            "id_lavorazione": fase.IdLavorazione,
            "lavorazione": lavorazione.DescrizioneLavorazione or "Lavorazione",
            "descrizione": descrizione or piano.CodArt or "Articolo",
            "quantita": quantita[piano.IdPianificazione],
            "numero_ordini": num_ordini[piano.IdPianificazione],
            "colore_fase": colore,
            "impastatrici": macchine_per_fase.get(fase.IdPianificazioneFase)
                             or macchine_per_piano.get(piano.IdPianificazione, []),
            "forno": forni.get(piano.IdForno, f"Forno #{piano.IdForno}"),
        })
    eventi.extend(righe_pdf_miste(inizio, fine, ruolo))
    return eventi


def _fase_e_cottura(evento: dict) -> bool:
    """La fase forno (ID 2) o una lavorazione denominata cottura non va stampata."""
    testo = str(evento.get("lavorazione") or "").casefold()
    return str(evento.get("id_lavorazione") or "") == "2" or "cott" in testo or "forno" in testo


def giorni_da_esportare(inizio: date, fasi: list[dict], mostra_weekend: bool) -> list[date]:
    """I weekend con produzione devono essere visibili anche con toggle disattivato."""
    con_produzione = {e["giorno"] for e in fasi}
    return [
        inizio + timedelta(days=i)
        for i in range(7)
        if i < 5 or mostra_weekend or (inizio + timedelta(days=i)) in con_produzione
    ]


def _rgb(hex_value: str, fallback: colors.Color) -> colors.Color:
    if not isinstance(hex_value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", hex_value):
        return fallback
    return colors.HexColor(hex_value)


def _piano_colori(id_piano: int) -> tuple[colors.Color, colors.Color]:
    hue = (int(id_piano) * 137.50776405) % 360 / 360.0
    bordo = colors.Color(*colorsys.hls_to_rgb(hue, .34, .76))
    sfondo = colors.Color(*colorsys.hls_to_rgb(hue, .77, .83))
    return bordo, sfondo


def _fit(canvas: Canvas, testo: object, x: float, y: float, larghezza: float,
         font: str = "Helvetica", grandezza: float = 8.0, minimo: float = 3.1) -> None:
    """Adatta la scritta alla cella, senza lasciarla uscire dal riquadro."""
    stringa = str(testo or "").replace("\n", " ").strip()
    if not stringa or larghezza <= 0:
        return
    misura = pdfmetrics.stringWidth(stringa, font, grandezza)
    if misura > larghezza:
        # Se la scritta è eccezionalmente lunga è preferibile scendere sotto
        # il minimo anziché sovrapporla alla scheda successiva.
        grandezza = max(.9, grandezza * larghezza / misura * .985)
    canvas.setFont(font, grandezza)
    canvas.drawString(x, y, stringa)


def _testo_macchine(evento: dict) -> str:
    nomi = evento.get("impastatrici") or []
    return ", ".join(str(nome) for nome in nomi) if nomi else "N/D"


def _riduci_etichetta_risorsa(testo: str, categoria: str) -> str:
    """Toglie soltanto prefissi ridondanti; non elimina il nome della risorsa."""
    testo = str(testo or "N/D").strip()
    if categoria == "impastatrice":
        testo = re.sub(r"(?i)^impastatric(?:e|i)\s+", "", testo)
    elif categoria == "forno":
        testo = re.sub(r"(?i)^forno\s+", "", testo)
    return re.sub(r"(?i)\(modello\s+", "(", testo)


def _a_capo(testo: str, font: str, dimensione: float, larghezza: float) -> list[str]:
    """A capo in base ai punti PDF, mai ridimensionamento orizzontale minuscolo.

    Spezza anche una parola particolarmente lunga quando necessario.
    """
    testo = re.sub(r"\s+", " ", str(testo or "")).strip()
    if not testo:
        return []
    parole = testo.split(" ")
    righe, riga = [], ""
    for parola in parole:
        candidato = f"{riga} {parola}" if riga else parola
        if pdfmetrics.stringWidth(candidato, font, dimensione) <= larghezza:
            riga = candidato
            continue
        if riga:
            righe.append(riga)
            riga = ""
        # La riga è vuota: suddividi solo la parola che non ci sta.
        while parola and pdfmetrics.stringWidth(parola, font, dimensione) > larghezza:
            lo, hi = 1, len(parola)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if pdfmetrics.stringWidth(parola[:mid], font, dimensione) <= larghezza:
                    lo = mid
                else:
                    hi = mid - 1
            n = max(1, lo)
            righe.append(parola[:n])
            parola = parola[n:]
        riga = parola
    if riga:
        righe.append(riga)
    return righe


def _dati_scheda(evento: dict) -> list[tuple[str, str]]:
    """Informazioni operative per fase, mantenendo il formato PDF esistente.

    Solo per le cotture miste usa i dati dei forni prenotati: le cotte sono
    dichiarate dall'operatore e NON derivano da stime di capienza o da una
    ripartizione inventata delle singole infornate.
    """
    descr = evento.get("descrizione") or "Articolo"
    ordinati = f'{evento.get("numero_ordini", 0)} ord.  ·  {evento.get("quantita", 0)} pz'
    imp = evento.get("impastatrici") or []
    imp_names = ", ".join(_riduci_etichetta_risorsa(n, "impastatrice") for n in imp) or "N/D"

    # Il calendario misto espone una cottura unica per piano e blocco,
    # con l'elenco strutturato dei forni effettivamente prenotati.
    dettagli = evento.get("forni_dettaglio") or []
    if dettagli and _fase_e_cottura(evento):
        # Un evento puo' riguardare piu' forni nello stesso blocco: non
        # ridistribuire mai i pezzi in singole cotte. Le quantita' qui
        # mostrate sono somme degli ordini associati a ciascun forno.
        dettagli_ordinati = sorted(
            dettagli,
            key=lambda d: (str(d.get("nome") or ""), int(d.get("id_forno") or 0)),
        )
        cotte_dichiarate = sum(int(d.get("numero_cotte") or 0) for d in dettagli_ordinati)
        dati = [
            (str(descr), "Helvetica-Bold"),
            (ordinati, "Helvetica-Bold"),
            (f"Cotte dichiarate: {cotte_dichiarate} · {len(dettagli_ordinati)} forni", "Helvetica-Bold"),
            (f"Imp.: {imp_names}", "Helvetica"),
        ]
        for forno in dettagli_ordinati:
            nome = _riduci_etichetta_risorsa(forno.get("nome") or f"#{forno.get('id_forno', '?')}", "forno")
            cotte = int(forno.get("numero_cotte") or 0)
            pezzi = int(forno.get("quantita") or 0)
            dati.append((f"Forno: {nome} · {cotte} cotte · {pezzi} pz", "Helvetica"))
        return dati

    forno = _riduci_etichetta_risorsa(evento.get("forno") or "N/D", "forno")
    return [
        (str(descr), "Helvetica-Bold"),
        (ordinati, "Helvetica-Bold"),
        (f"Imp.: {imp_names}", "Helvetica"),
        (f"Forno: {forno}", "Helvetica"),
    ]


def _layout_scheda(dati: list[tuple[str, str]], larghezza: float,
                   altezza: float) -> tuple[float, list[tuple[list[str], str]], float]:
    """Massimo carattere compatibile con la superficie: ricerca adattiva.

    Il testo non viene mai soppresso; se la settimana è sovraccarica può
    diventare piccolo, ma continua a stare nel foglio unico.
    """
    padding = 1.5 if larghezza < 68 else 2.0
    corpo_w = max(.5, larghezza - padding * 2)
    # Header compatto: occupa esattamente una riga.
    # Altri elementi vanno a capo, anziché ridursi di dimensione in orizzontale.
    overhead = 1.1  # spazio tra intestazione e dettagli
    massimo = min(10.5, max(5.0, altezza / 3.9))
    minimo = .9
    def calcola(dim):
        righe = [(_a_capo(t, f, dim, corpo_w), f) for t,f in dati]
        tot = sum(len(r) for r,_ in righe)
        separatori = max(0, len(righe)-1)
        altezza_testo = tot * dim * 1.035 + separatori * min(.75, dim*.10)
        return righe, altezza_testo
    lo, hi = minimo, massimo
    for _ in range(17):
        mid = (lo + hi) / 2
        _, bisogno = calcola(mid)
        if bisogno + overhead <= altezza:
            lo = mid
        else:
            hi = mid
    righe, bisogno = calcola(lo)
    return lo, righe, bisogno


def _scheda(canvas: Canvas, evento: dict, x: float, y: float,
            larghezza: float, altezza: float) -> None:
    bordo, sfondo = _piano_colori(evento["piano"])
    canvas.setFillColor(sfondo)
    canvas.setStrokeColor(bordo)
    canvas.setLineWidth(.9 if larghezza > 70 else .55)
    canvas.roundRect(x, y, larghezza, altezza,
                     min(2.0, larghezza / 4, altezza / 4), fill=1, stroke=1)
    if larghezza < 3 or altezza < 3:
        return

    padding_x = 1.5 if larghezza < 68 else 2.0
    testo_w = max(.5, larghezza - 2 * padding_x)
    label_color = _rgb(evento.get("colore_fase"), colors.HexColor("#FFEA36"))
    numero_fase = evento.get("fase") or "?"
    intestazione = f'{evento.get("etichetta_piano") or "#" + str(evento["piano"])} · F{numero_fase} · {evento["lavorazione"]}' 
    # Una sola riga e meno spazio di prima: altezza etichetta 9-12 pt.
    header_h = min(12.4, max(5.0, altezza * .20))
    canvas.setFillColor(label_color)
    canvas.roundRect(x + 0.7, y + altezza - header_h - .7,
                     larghezza - 1.4, header_h, 1.2, fill=1, stroke=0)
    canvas.setFillColor(colors.black)
    header_font = min(10.1, max(3.2, header_h * .79))
    # Solo l'intestazione sintetica viene compressa orizzontalmente.
    _fit(canvas, intestazione, x + padding_x,
         y + altezza - header_h + max(1.5, (header_h - header_font) / 2) - .5,
         testo_w, "Helvetica-Bold", header_font, 2.8)

    corpo_top = y + altezza - header_h - 1.7
    corpo_bottom = y + 1.1
    disponibile_h = corpo_top - corpo_bottom
    if disponibile_h < .7:
        return
    dati = _dati_scheda(evento)
    font_size, righe, uso_h = _layout_scheda(dati, testo_w, disponibile_h)
    passo = font_size * 1.035
    sep = min(.75, font_size * .10)
    # Allinea in alto: evita grandi vuoti fra descrizione, quantità e risorse.
    baseline = corpo_top - font_size * .91
    for i, (linee, font) in enumerate(righe):
        for linea in linee:
            canvas.setFont(font, font_size)
            canvas.setFillColor(colors.black)
            canvas.drawString(x + padding_x, baseline, linea)
            baseline -= passo
        if i != len(righe)-1:
            baseline -= sep


def _griglia_fasi(canvas: Canvas, eventi: list[dict], x: float, y: float,
                  larghezza: float, altezza: float) -> None:
    if not eventi:
        return
    elementi = sorted(eventi, key=lambda e: (e["piano"], e["fase"] or 0))
    # Riduzione sostanziale di margini e interspazi; spazio recuperato per
    # caratteri e testo a capo invece che per bordo bianco.
    pad, gap = 1.2, .9
    utile_l = max(1, larghezza - 2 * pad)
    utile_h = max(1, altezza - 2 * pad)
    massimo_colonne = max(1, min(len(elementi), 8, int(utile_l / 50)))
    colonne = min(massimo_colonne, 2 if len(elementi) > 1 else 1)
    while colonne < massimo_colonne and math.ceil(len(elementi) / colonne) * 34 > utile_h:
        colonne += 1
    righe = math.ceil(len(elementi) / colonne)
    gap = min(gap, utile_h / max(1, righe * 4))
    cw = max(.2, (utile_l - (colonne - 1) * gap) / colonne)
    ch = max(.001, (utile_h - (righe - 1) * gap) / righe)
    for idx, fase in enumerate(elementi):
        riga, colonna = divmod(idx, colonne)
        cx = x + pad + colonna * (cw + gap)
        cy = y + altezza - pad - (riga + 1) * ch - riga * gap
        _scheda(canvas, fase, cx, cy, cw, ch)

def crea_pdf_settimana(inizio: date, fasi: list[dict], scadenze: list[dict],
                        mostra_weekend: bool = False,
                        nascondi_cottura: bool = False) -> bytes:
    """Un solo foglio A3, con lo stesso filtro cottura della vista web."""
    if nascondi_cottura:
        fasi = [evento for evento in fasi if not _fase_e_cottura(evento)]
    date_visibili = giorni_da_esportare(inizio, fasi, mostra_weekend)
    w, h = landscape(A3)
    output = BytesIO()
    c = Canvas(output, pagesize=(w, h), pageCompression=1)
    c.setTitle(f"Calendario produzione {inizio.isoformat()}")
    c.setAuthor("Ordini Panettoni")

    c.setFillColor(colors.HexColor("#142b45"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGINE, h - 29, "Calendario produzione - settimana con ore")
    c.setFont("Helvetica", 8.5)
    c.setFillColor(colors.HexColor("#475569"))
    fine = inizio + timedelta(days=6)
    c.drawRightString(w - MARGINE, h - 28,
                      f"{inizio.strftime('%d/%m/%Y')} - {fine.strftime('%d/%m/%Y')}  |  A3 orizzontale")
    c.setFont("Helvetica", 7.4)
    intestazione_fasi = "Fasi 07:00-12:30 / 13:00-18:00  -  Colore scheda: pianificazione  -  Etichetta: fase"
    if nascondi_cottura:
        intestazione_fasi += "  -  Cottura nascosta"
    c.drawString(MARGINE, h - 44, intestazione_fasi)

    asse_x = MARGINE + ASSE_ORE
    griglia_w = w - asse_x - MARGINE
    conteggi = defaultdict(int)
    for ev in fasi:
        conteggi[ev["giorno"]] += 1
    pesi = [(.68 if conteggi[d] == 0 else min(1.25 + .35 * math.sqrt(conteggi[d]), 3.0))
            for d in date_visibili]
    utilizzabile = griglia_w - GAP_GIORNI * (len(date_visibili) - 1)
    giorni_l = [utilizzabile * p / sum(pesi) for p in pesi]
    giorni_x = []
    p = asse_x
    for lunghezza in giorni_l:
        giorni_x.append(p)
        p += lunghezza + GAP_GIORNI

    header_y = h - 72
    header_h = 19
    all_day_h = 33
    all_day_y = header_y - all_day_h - 2
    grid_top = all_day_y - 4
    grid_bottom = MARGINE + 6
    interruzione = 15
    grid_h = grid_top - grid_bottom - interruzione
    mattina_h = grid_h * (5.5 / 10.5)
    pomeriggio_h = grid_h - mattina_h
    mattina_y = grid_top - mattina_h
    pomeriggio_y = grid_bottom

    c.setFillColor(colors.HexColor("#f1f5fa"))
    c.setStrokeColor(colors.HexColor("#ced6df"))
    c.roundRect(MARGINE, all_day_y, ASSE_ORE - 5, all_day_h + header_h + 2, 2, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#475569"))
    c.setFont("Helvetica-Bold", 6.7)
    c.drawString(MARGINE + 3, all_day_y + 15, "Scadenze")

    scadenze_mappa = {s["giorno"]: s for s in scadenze}
    fasi_per_blocco = defaultdict(list)
    for evento in fasi:
        fasi_per_blocco[(evento["giorno"], evento["blocco"])].append(evento)
    for data, x, width in zip(date_visibili, giorni_x, giorni_l):
        c.setFillColor(colors.HexColor("#e7edf6"))
        c.setStrokeColor(colors.HexColor("#c7d3e2"))
        c.roundRect(x, header_y, width, header_h, 2, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#111827"))
        _fit(c, f"{GIORNI[data.weekday()]} {data.strftime('%d/%m')}", x + 5,
             header_y + 7, width - 10, "Helvetica-Bold", 9.3, 5)
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor("#d0dae5"))
        c.rect(x, all_day_y, width, all_day_h, fill=1, stroke=1)
        scad = scadenze_mappa.get(data)
        if scad:
            c.setFillColor(colors.HexColor("#c9e2fb"))
            c.setStrokeColor(colors.HexColor("#2776ba"))
            c.roundRect(x + 2, all_day_y + 3, width - 4, all_day_h - 6, 3, fill=1, stroke=1)
            c.setFillColor(colors.HexColor("#12365c"))
            _fit(c, f'{scad["numero_ordini"]} ordini in scadenza - {scad["quantita_ordinata"]} pz',
                 x + 6, all_day_y + 22, width - 12, "Helvetica-Bold", 8, 4.5)
            _fit(c, f'{scad["completati"]} eseguiti - {scad["da_eseguire"]} da eseguire',
                 x + 6, all_day_y + 9, width - 12, "Helvetica", 7.4, 4.5)
        for blocco, basso, alto in (("M", mattina_y, mattina_h), ("P", pomeriggio_y, pomeriggio_h)):
            c.setFillColor(colors.HexColor("#f8fafc"))
            c.setStrokeColor(colors.HexColor("#c5d1dd"))
            c.rect(x, basso, width, alto, stroke=1, fill=1)
            for ore in range(1, 11):
                fraction = ore / 11 if blocco == "M" else ore / 10
                if fraction >= 1: continue
                yy = basso + fraction * alto
                c.setStrokeColor(colors.HexColor("#e7edf3"))
                c.setLineWidth(.4)
                c.line(x, yy, x + width, yy)
            _griglia_fasi(c, fasi_per_blocco[(data, blocco)], x, basso, width, alto)

    c.setFillColor(colors.HexColor("#475569"))
    c.setFont("Helvetica-Bold", 7.0)
    # Ore allineate alle fasce; stampa ogni ora, con l'ultimo estremo a 12:30.
    for i in range(6):
        ora = 7 + i
        y_tick = grid_top - mattina_h * (i / 5.5)
        c.drawRightString(asse_x - 6, y_tick - 2.5, f"{ora:02d}:00")
    c.drawRightString(asse_x - 6, mattina_y - 2.5, "12:30")
    for i in range(6):
        ora = 13 + i
        y_tick = pomeriggio_y + pomeriggio_h * (1 - i / 5)
        c.drawRightString(asse_x - 6, y_tick - 2.5, f"{ora:02d}:00")
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 6.5)
    c.drawString(MARGINE, MARGINE - 2, f"{len(fasi)} fasi totali - {len(date_visibili)} giorni visibili - esportazione integrale su un solo foglio")

    c.showPage()  # UNA sola pagina, mai chiamato nella griglia.
    c.save()
    return output.getvalue()
