"""Fase 3B2: lettura unica delle pianificazioni miste per calendario e PDF.

Nessuna scrittura: i dati di produzione sono snapshot dalle bozze 3A, lo stato
viene letto dalla testata 3B1. Il calendario legacy non deve trattare tali
produzioni come pianificazioni_produzione con un solo forno.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
import re

from sqlalchemy import or_, tuple_
from sqlalchemy.orm import selectinload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaArticoli, AnagraficaForni, AnagraficaImpastatrici, BozzaPianificazioneMista,
    BozzaPianificazioneMistaForno, BozzaPianificazioneMistaOrdine,
    PianificazioneMistaAttiva, PrenotazioneMistaForno,
    PrenotazioneMistaImpastatrice, RicettaFasi, TipologieLavorazione,
    TipRicetteAnagArticoli,
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



def _gruppi_cottura_per_slot(bozza, slot_forni: dict, nomi_forni: dict[int, str]) -> list[dict]:
    """Un evento di cottura per piano e slot, aggregando i forni effettivamente prenotati.

    I dati fisici delle singole cotte NON sono calcolati: NumeroCotte deriva dallo
    snapshot manuale del forno. La distribuzione ordini per forno deriva dalla
    bozza salvata, non dalla vecchia tabella facoltativa di composizioni.
    """
    gruppi: dict[tuple[date, str], dict[int, dict]] = defaultdict(dict)
    for forno in bozza.forni:
        ordini = [o for o in bozza.ordini if o.IdForno == forno.IdForno]
        quantita_articoli: dict[str, int] = defaultdict(int)
        for ordine in ordini:
            quantita_articoli[ordine.CodArt] += int(ordine.QtaPianificata)
        descrizione = nomi_forni.get(forno.IdForno, f'Forno #{forno.IdForno}')
        dettaglio = {
            'id_forno': int(forno.IdForno), 'nome': descrizione,
            'numero_cotte': int(forno.NumeroCotte),
            'quantita': sum(quantita_articoli.values()),
            'articoli': dict(quantita_articoli),
            'ordini': sorted(f'{o.IdDocumento}-{o.IdRigaDoc}' for o in ordini),
        }
        # La prenotazione reale comprende tutte le occupazioni multi-blocco.
        for giorno, blocco in set(slot_forni.get((bozza.IdBozza, forno.IdForno), ())):
            gruppi[(giorno, blocco)][forno.IdForno] = dettaglio
    risultato = []
    for (giorno, blocco), forni in sorted(gruppi.items()):
        risultato.append({
            'giorno': giorno, 'blocco': blocco,
            'forni': [forni[id_forno] for id_forno in sorted(forni)],
        })
    return risultato


def _colore_medio(colori: list[str | None]) -> str | None:
    """Media aritmetica RGB dei colori validi; un colore solo resta identico.

    I colori non configurati non contribuiscono alla media. Quando nessuna
    ricetta ha un colore valido, restituisce None (fallback grafico esistente).
    """
    validi = [c.upper() for c in colori
              if isinstance(c, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", c)]
    if not validi:
        return None
    if len(set(validi)) == 1:
        return validi[0]
    canali = [[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in validi]
    n = len(canali)
    medi = [(sum(rgb[i] for rgb in canali) * 2 + n) // (2 * n)
            for i in range(3)]
    return "#" + "".join(f"{valore:02X}" for valore in medi)


def _colori_cottura_per_articolo(codici: set[str]) -> dict[str, str | None]:
    """Recupera in una query il colore forno di ogni ricetta associata all'articolo."""
    if not codici:
        return {}
    righe = db.session.execute(
        db.select(
            TipRicetteAnagArticoli.CodArt,
            RicettaFasi.Colore,
            RicettaFasi.IdLavorazione,
            TipologieLavorazione.TipologiaLavorazione,
            TipologieLavorazione.CodiceLavorazione,
            TipologieLavorazione.DescrizioneLavorazione,
        )
        .join(RicettaFasi, RicettaFasi.IdRicetta == TipRicetteAnagArticoli.IdRicetta)
        .join(TipologieLavorazione, TipologieLavorazione.IdLavorazione == RicettaFasi.IdLavorazione)
        .where(TipRicetteAnagArticoli.CodArt.in_(codici))
    ).all()
    colori = defaultdict(list)
    for codice, colore, id_lavorazione, categoria, codice_lavorazione, descrizione in righe:
        nome = " ".join(str(x or "") for x in (
            categoria, codice_lavorazione, descrizione
        )).casefold()
        if int(id_lavorazione) == 2 or any(t in nome for t in ("cott", "forno", "inforn")):
            colori[codice].append(colore)
    return {codice: _colore_medio(valori) for codice, valori in colori.items()}


def righe_fasi_miste(inizio: date, fine: date, ruolo: str) -> list[dict]:
    """Ritorna righe normalizzate per UI e PDF, una per slot effettivo.

    La cottura e' visualizzata una volta per piano e blocco, con TUTTI i
    forni prenotati in quello slot e le cotte dichiarate manualmente.
    Le prenotazioni multi-blocco rimangono visibili per ogni slot effettivo.
    """
    if fine <= inizio:
        return []
    piani = _modelli_misti(inizio, fine)
    if not piani:
        return []
    codici_articoli = {o.CodArt for p in piani for o in p.bozza.ordini}
    descrizioni_articoli = {
        articolo.CodArt: (articolo.DesArt or articolo.CodArt)
        for articolo in db.session.scalars(db.select(AnagraficaArticoli).where(
            AnagraficaArticoli.CodArt.in_(codici_articoli)
        )).all()
    } if codici_articoli else {}
    colori_cottura_articolo = _colori_cottura_per_articolo(codici_articoli)
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
        # Ordinamento stabile delle righe ordine per tutte le fasi della stessa bozza.
        righe_ordine = sorted(bozza.ordini, key=lambda r: (r.IdDocumento, r.IdRigaDoc))
        quantita_ordini = [int(r.QtaPianificata or 0) for r in righe_ordine]
        descrizioni_piano = sorted({
            (descrizioni_articoli.get(codice) or 'Articolo senza descrizione').strip()
            for codice in codici
        })
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
            'quantita_per_ordine': quantita_ordini,
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
                        'descrizione': 'Impasto comune: ' + ', '.join(descrizioni_piano),
                        'colore_fase': rf.Colore if rf else None,
                        'forno': ', '.join(sorted({forni.get(f.IdForno, f'Forno #{f.IdForno}') for f in bozza.forni})),
                        'numero_cotte': sum(int(f.NumeroCotte) for f in bozza.forni),
                    })
        for gruppo in _gruppi_cottura_per_slot(bozza, slot_forni, forni):
            # Se piu' forni lavorano nello stesso blocco, produciamo UN evento.
            # Se occupano blocchi diversi, ognuno resta visibile nel proprio slot.
            dettagli = gruppo['forni']
            quantita_articoli = defaultdict(int)
            ordini_cottura = set()
            for dettaglio in dettagli:
                ordini_cottura.update(dettaglio['ordini'])
                for codart, qta in dettaglio['articoli'].items():
                    quantita_articoli[codart] += qta
            giorno, blocco = gruppo['giorno'], gruppo['blocco']
            # Le cotte sono mostrate con le descrizioni, senza codici articolo.
            descrizione_articoli = ', '.join(sorted({
                (descrizioni_articoli.get(codice) or 'Articolo senza descrizione').strip()
                for codice in quantita_articoli
            }))
            # La cotta puo' utilizzare solo un sottoinsieme degli ordini della bozza.
            righe_cottura = [r for r in righe_ordine
                             if f'{r.IdDocumento}-{r.IdRigaDoc}' in ordini_cottura]
            quantita_cottura = [int(r.QtaPianificata or 0) for r in righe_cottura]
            output.append({**base,
                'quantita': sum(quantita_articoli.values()),
                'numero_ordini': len(ordini_cottura),
                'ordini': sorted(ordini_cottura),
                'quantita_per_ordine': quantita_cottura,
                'id_evento': f'misto-{bozza.IdBozza}-cottura-{giorno:%Y%m%d}-{blocco}',
                'giorno': giorno, 'blocco': blocco,
                'numero_fase': 2, 'id_lavorazione': 2, 'lavorazione': 'Cottura',
                'descrizione': descrizione_articoli,
                # Cottura mista: se i colori ricetta coincidono rimangono
                # invariati; altrimenti si usa la media RGB dei colori
                # dei soli articoli effettivamente cotti in questo blocco.
                'colore_fase': _colore_medio([
                    colori_cottura_articolo.get(codice)
                    for codice in sorted(quantita_articoli)
                ]),
                'forno': ' · '.join(f"{d['nome']} ({d['numero_cotte']} cotte)" for d in dettagli),
                'forni_dettaglio': [
                    {'id_forno': d['id_forno'], 'nome': d['nome'],
                     'numero_cotte': d['numero_cotte'], 'quantita': d['quantita']}
                    for d in dettagli
                ],
                'numero_cotte': sum(d['numero_cotte'] for d in dettagli),
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
                'quantita_per_ordine': r['quantita_per_ordine'],
                'panettoni_previsti': r['quantita'],
                'peso_totale_kg': r['peso_kg'],
                'impastatrici': r['impastatrici'],
                'numero_cotte': r['numero_cotte'],
                'forni_dettaglio': r.get('forni_dettaglio', []),
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
        'forni_dettaglio': r.get('forni_dettaglio', []),
    } for r in righe_fasi_miste(inizio, fine, ruolo)]
