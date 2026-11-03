"""Fase 3B2: lettura unica delle pianificazioni miste per calendario e PDF.

Nessuna scrittura: i dati di produzione sono snapshot dalle bozze 3A, lo stato
viene letto dalla testata 3B1. Il calendario legacy non deve trattare tali
produzioni come pianificazioni_produzione con un solo forno.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import or_, tuple_
from sqlalchemy.orm import selectinload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaForni, AnagraficaImpastatrici, BozzaPianificazioneMista,
    BozzaPianificazioneMistaForno, BozzaPianificazioneMistaOrdine,
    PianificazioneMistaAttiva, PrenotazioneMistaForno,
    PrenotazioneMistaImpastatrice, RicettaFasi, TipologieLavorazione,
)
from flaskr.service.risorse_produzione_service import _blocchi_occupati
from flaskr.service.scadenze_giornaliere_service import articolo_cnf

# Usa un identificativo grafico numerico in un intervallo distinto da quello
# dei piani legacy. Il testo mostrato all'operatore e' sempre "M-<IdBozza>".
ID_GRAFICO_MISTO_BASE = 1_000_000_000
ORARI_BLOCCHI = {
    'M': (time(7, 0), time(12, 30)),
    'P': (time(13, 0), time(18, 0)),
}


def quantita_miste_per_ordini(chiavi: list[tuple[int, int]], stati=('In produzione', 'Chiuso')) -> dict[tuple[int, int], int]:
    """Quantita' impegnate per ordine, ignorando bozze e piani annullati."""
    if not chiavi:
        return {}
    risultato: dict[tuple[int, int], int] = defaultdict(int)
    for start in range(0, len(chiavi), 200):
        blocco = chiavi[start:start + 200]
        stmt = (
            db.select(
                BozzaPianificazioneMistaOrdine.IdDocumento,
                BozzaPianificazioneMistaOrdine.IdRigaDoc,
                db.func.sum(BozzaPianificazioneMistaOrdine.QtaPianificata),
            )
            .join(PianificazioneMistaAttiva, PianificazioneMistaAttiva.IdBozza == BozzaPianificazioneMistaOrdine.IdBozza)
            .where(
                tuple_(BozzaPianificazioneMistaOrdine.IdDocumento, BozzaPianificazioneMistaOrdine.IdRigaDoc).in_(blocco),
                PianificazioneMistaAttiva.Stato.in_(stati),
            )
            .group_by(BozzaPianificazioneMistaOrdine.IdDocumento, BozzaPianificazioneMistaOrdine.IdRigaDoc)
        )
        for doc, riga, quantita in db.session.execute(stmt):
            risultato[(doc, riga)] += int(quantita or 0)
    return dict(risultato)


def _modelli_misti(inizio: date, fine: date):
    """Carica in batch SOLO i piani con prenotazioni nel periodo richiesto."""
    forni_slot = db.select(PrenotazioneMistaForno.IdBozza).where(
        PrenotazioneMistaForno.DataFase >= inizio,
        PrenotazioneMistaForno.DataFase < fine,
    )
    impasti_slot = db.select(PrenotazioneMistaImpastatrice.IdBozza).where(
        PrenotazioneMistaImpastatrice.DataFase >= inizio,
        PrenotazioneMistaImpastatrice.DataFase < fine,
    )
    return db.session.scalars(
        db.select(PianificazioneMistaAttiva)
        .where(
            PianificazioneMistaAttiva.Stato.in_(('In produzione', 'Chiuso')),
            or_(PianificazioneMistaAttiva.IdBozza.in_(forni_slot),
                   PianificazioneMistaAttiva.IdBozza.in_(impasti_slot)),
        )
        .options(
            selectinload(PianificazioneMistaAttiva.bozza).selectinload(BozzaPianificazioneMista.ordini),
            selectinload(PianificazioneMistaAttiva.bozza)
                .selectinload(BozzaPianificazioneMista.forni).selectinload(BozzaPianificazioneMistaForno.cotte),
            selectinload(PianificazioneMistaAttiva.bozza).selectinload(BozzaPianificazioneMista.fasi_impasto),
            selectinload(PianificazioneMistaAttiva.bozza).selectinload(BozzaPianificazioneMista.impastatrici),
        )
    ).all()


def righe_fasi_miste(inizio: date, fine: date, ruolo: str) -> list[dict]:
    """Ritorna righe normalizzate per UI e PDF, una per slot effettivo.

    La fase forno viene visualizzata per OGNI forno, senza sommare due volte le
    cotte di articoli differenti nella stessa infornata. Una fase che dura piu'
    blocchi occupa anche gli slot successivi.
    """
    if fine <= inizio:
        return []
    piani = _modelli_misti(inizio, fine)
    if not piani:
        return []
    ids_forni = {f.IdForno for p in piani for f in p.bozza.forni}
    ids_macchine = {m.IdImpastatrice for p in piani for m in p.bozza.impastatrici if m.KgAssegnati > 0}
    ids_lavorazioni = {f.IdLavorazione for p in piani for f in p.bozza.fasi_impasto}
    ids_ricetta_fase = {f.IdRicettaFase for p in piani for f in p.bozza.fasi_impasto}
    forni = {f.IdForno: (f.DescrizioneForno or f.ModelloForno or f'Forno #{f.IdForno}')
             for f in db.session.scalars(db.select(AnagraficaForni).where(AnagraficaForni.IdForno.in_(ids_forni)))}
    macchine = {m.IdImpastatrice: (m.DescrizioneImpastatrice or m.ModelloImpastatrice or f'Impastatrice #{m.IdImpastatrice}')
                for m in db.session.scalars(db.select(AnagraficaImpastatrici).where(AnagraficaImpastatrici.IdImpastatrice.in_(ids_macchine)))}
    lavorazioni = {l.IdLavorazione: l for l in db.session.scalars(
        db.select(TipologieLavorazione).where(TipologieLavorazione.IdLavorazione.in_(ids_lavorazioni)))}
    fasi_ricetta = {f.id: f for f in db.session.scalars(
        db.select(RicettaFasi).where(RicettaFasi.id.in_(ids_ricetta_fase)))}
    # Le prenotazioni 3B1 sono gia' espanse per giorno e blocco.
    slot_forni = defaultdict(list)
    for prenotazione in db.session.scalars(db.select(PrenotazioneMistaForno).where(
        PrenotazioneMistaForno.IdBozza.in_([p.IdBozza for p in piani]),
        PrenotazioneMistaForno.DataFase >= inizio,
        PrenotazioneMistaForno.DataFase < fine,
    )):
        slot_forni[(prenotazione.IdBozza, prenotazione.IdForno)].append(
            (prenotazione.DataFase, prenotazione.Blocco)
        )
    output = []
    for piano in piani:
        bozza = piano.bozza
        if ruolo == 'utente_produzione' and any(articolo_cnf(r.CodArt) for r in bozza.ordini):
            continue  # Non divulgare una pianificazione che contiene codici riservati.
        codici = sorted({r.CodArt for r in bozza.ordini})
        totale = sum(int(r.QtaPianificata) for r in bozza.ordini)
        identificativi = sorted(f'{r.IdDocumento}-{r.IdRigaDoc}' for r in bozza.ordini)
        mixer_nomi = [macchine.get(m.IdImpastatrice, f'Impastatrice #{m.IdImpastatrice}')
                      for m in bozza.impastatrici if m.KgAssegnati > 0]
        base = {
            'id_bozza': bozza.IdBozza,
            'id_grafico': ID_GRAFICO_MISTO_BASE + bozza.IdBozza,
            'etichetta_piano': f'M-{bozza.IdBozza}',
            'stato': piano.Stato,
            'ordini': identificativi,
            'numero_ordini': len(bozza.ordini),
            'quantita': totale,
            'peso_kg': float((Decimal(str(bozza.PesoTotaleGrammi)) / 1000).quantize(Decimal('0.01'))),
            'impastatrici': mixer_nomi,
        }
        for fase in bozza.fasi_impasto:
            lavorazione = lavorazioni.get(fase.IdLavorazione)
            rf = fasi_ricetta.get(fase.IdRicettaFase)
            nome = lavorazione.DescrizioneLavorazione if lavorazione else f'Lavorazione #{fase.IdLavorazione}'
            for indice, (data, blocco) in enumerate(_blocchi_occupati(fase.DataFase, fase.Blocco, int(fase.DurataBlocchi)), start=1):
                if inizio <= data < fine:
                    output.append({**base,
                        'id_evento': f'misto-{bozza.IdBozza}-fase-{fase.IdRicettaFase}-{indice}',
                        'giorno': data, 'blocco': blocco,
                        'numero_fase': int(rf.IdFase) if rf else int(fase.OffsetBlocchi) + 1,
                        'id_lavorazione': fase.IdLavorazione, 'lavorazione': nome,
                        'descrizione': 'Impasto comune: ' + ', '.join(codici),
                        'colore_fase': rf.Colore if rf else None,
                        'forno': ', '.join(sorted({forni.get(f.IdForno, f'Forno #{f.IdForno}') for f in bozza.forni})),
                        'numero_cotte': sum(int(f.NumeroCotte) for f in bozza.forni),
                    })
        for forno in bozza.forni:
            nome_forno = forni.get(forno.IdForno, f'Forno #{forno.IdForno}')
            composizione = defaultdict(int)
            for cotta in forno.cotte:
                composizione[cotta.CodArt] += int(cotta.QtaPezzi)
            if not composizione:
                continue
            articoli = ', '.join(f'{codice}: {pezzi} pz' for codice, pezzi in sorted(composizione.items()))
            ordini_forno = [o for o in bozza.ordini if o.IdForno == forno.IdForno]
            # La prenotazione registra gli slot realmente occupati, anche
            # quando la durata della lavorazione e' superiore a un blocco.
            occupazioni = sorted(slot_forni[(bozza.IdBozza, forno.IdForno)])
            for indice, (data, blocco) in enumerate(occupazioni, start=1):
                if inizio <= data < fine:
                    output.append({**base,
                        'quantita': sum(composizione.values()),
                        'numero_ordini': len(ordini_forno),
                        'ordini': sorted(f'{o.IdDocumento}-{o.IdRigaDoc}' for o in ordini_forno),
                        'id_evento': f'misto-{bozza.IdBozza}-forno-{forno.IdForno}-{indice}',
                        'giorno': data, 'blocco': blocco,
                        'numero_fase': 2, 'id_lavorazione': 2, 'lavorazione': 'Cottura · ' + nome_forno,
                        'descrizione': articoli,
                        'colore_fase': None,
                        'forno': nome_forno,
                        'numero_cotte': int(forno.NumeroCotte),
                    })
    return sorted(output, key=lambda r: (r['giorno'], r['blocco'], r['id_grafico'], r['id_evento']))


def eventi_fasi_miste(inizio: date, fine: date, ruolo: str, url_dettaglio) -> list[dict]:
    """Eventi compatibili con la sorgente FullCalendar gia' in produzione."""
    eventi = []
    for r in righe_fasi_miste(inizio, fine, ruolo):
        inizio_ora, fine_ora = ORARI_BLOCCHI[r['blocco']]
        eventi.append({
            'id': r['id_evento'],
            'title': f"Piano {r['etichetta_piano']} · {r['lavorazione']}",
            'start': datetime.combine(r['giorno'], inizio_ora).isoformat(),
            'end': datetime.combine(r['giorno'], fine_ora).isoformat(),
            'allDay': False,
            'extendedProps': {
                'tipo_evento': 'pianificazione_mista',
                'id_pianificazione': r['id_grafico'],
                'id_bozza_mista': r['id_bozza'],
                'etichetta_piano': r['etichetta_piano'],
                'numero_fase': r['numero_fase'],
                'id_lavorazione': r['id_lavorazione'],
                'lavorazione': r['lavorazione'],
                'descrizione_articolo': r['descrizione'],
                'ordini': r['ordini'],
                'numero_ordini': r['numero_ordini'],
                'quantita_assegnata': r['quantita'],
                'panettoni_previsti': r['quantita'],
                'peso_totale_kg': r['peso_kg'],
                'impastatrici': r['impastatrici'],
                'numero_cotte': r['numero_cotte'],
                'quantita_da_stock': 0,
                'colore_fase': r['colore_fase'],
                'detailUrl': url_dettaglio(r['id_bozza']),
            },
        })
    return eventi


def righe_pdf_miste(inizio: date, fine: date, ruolo: str) -> list[dict]:
    """Adatta le stesse fasi alla griglia settimanale del PDF esistente."""
    return [{
        'giorno': r['giorno'], 'blocco': r['blocco'],
        'piano': r['id_grafico'], 'etichetta_piano': r['etichetta_piano'],
        'fase': r['numero_fase'], 'id_lavorazione': r['id_lavorazione'],
        'lavorazione': r['lavorazione'], 'descrizione': r['descrizione'],
        'quantita': r['quantita'], 'numero_ordini': r['numero_ordini'],
        'colore_fase': r['colore_fase'],
        'impastatrici': r['impastatrici'], 'forno': r['forno'],
        'numero_cotte': r['numero_cotte'],
    } for r in righe_fasi_miste(inizio, fine, ruolo)]
