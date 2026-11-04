"""Vista completa delle pianificazioni attive, con tutte le relative righe ordine.

La fonte delle pianificazioni è il database delle pianificazioni, NON l'elenco
raggruppato 'ordini in produzione': in questo modo la pagina non perde righe
quando il feed ERP cambia o quando un ordine compare in più pianificazioni.
Le note sono recuperate in batch e mostrate in sola lettura.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import and_, tuple_
from sqlalchemy.orm import joinedload, selectinload

from flaskr.extensions import db
from flaskr.models.model_accessi import NotaVenditeOrdine
from flaskr.models.model_note_produzione import NotaProduzioneOrdine
from flaskr.models.model_ordini import (
    AnagraficaForni,
    BozzaPianificazioneMista,
    OrdiniCliente,
    PianificazioneMistaAttiva,
    PianificazioneOrdini,
    PianificazioneProduzione,
)
from flaskr.service.scadenze_giornaliere_service import articolo_cnf
from flaskr.service.commenti_etichetta_service import commenti_etichette_per_righe


def _informazioni_ordini(chiavi: set[tuple[int, int]]) -> dict[tuple[int, int], OrdiniCliente]:
    """Anagrafica dei singoli ordini attualmente presenti nel feed ERP."""
    risultato = {}
    elenco = sorted(chiavi)
    for inizio in range(0, len(elenco), 200):
        gruppo = elenco[inizio:inizio + 200]
        for ordine in db.session.scalars(
            db.select(OrdiniCliente)
            .options(joinedload(OrdiniCliente.Cliente))
            .where(tuple_(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc).in_(gruppo))
        ).all():
            risultato[(ordine.IdDocumento, ordine.IdRigaDoc)] = ordine
    return risultato


def _commenti(chiavi: set[tuple[int, int]], modello):
    """Un solo insieme di query, a blocchi di 200 coppie, senza N+1 per piano."""
    risultato = defaultdict(list)
    elenco = sorted(chiavi)
    for inizio in range(0, len(elenco), 200):
        gruppo = elenco[inizio:inizio + 200]
        for nota in db.session.scalars(
            db.select(modello)
            .where(tuple_(modello.IdDocumento, modello.IdRigaDoc).in_(gruppo))
            .order_by(modello.CreatoIl.desc(), modello.Id.desc())
        ).all():
            risultato[(nota.IdDocumento, nota.IdRigaDoc)].append(nota)
    return risultato


def righe_pianificazioni_unificate(tradizionali: list[dict] | None = None,
                                   *, nascondi_cnf: bool = False) -> list[dict]:
    """Restituisce OGNI pianificazione attiva e TUTTE le righe ad essa collegate.

    `tradizionali` è mantenuto nell'interfaccia per compatibilità con la route
    preesistente, ma non filtra più le pianificazioni: la fonte sono le testate
    PianificazioneProduzione/PianificazioneMistaAttiva.
    """
    piani_trad = db.session.scalars(
        db.select(PianificazioneProduzione)
        .where(PianificazioneProduzione.StatoRigaOrdine == 'In produzione')
        .order_by(PianificazioneProduzione.DataProduzione.desc(),
                  PianificazioneProduzione.IdPianificazione.desc())
    ).all()
    if nascondi_cnf:
        piani_trad = [p for p in piani_trad if not articolo_cnf(p.CodArt)]

    piani_misti = db.session.scalars(
        db.select(PianificazioneMistaAttiva)
        .where(PianificazioneMistaAttiva.Stato == 'In produzione')
        .options(
            selectinload(PianificazioneMistaAttiva.bozza)
            .selectinload(BozzaPianificazioneMista.ordini),
            selectinload(PianificazioneMistaAttiva.bozza)
            .selectinload(BozzaPianificazioneMista.forni),
        )
    ).all()
    if nascondi_cnf:
        piani_misti = [p for p in piani_misti
                       if not any(articolo_cnf(o.CodArt) for o in p.bozza.ordini)]

    ids_trad = [p.IdPianificazione for p in piani_trad]
    associazioni = defaultdict(list)
    # Nessuna paginazione o limit: si raccolgono tutte le associazioni.
    for inizio in range(0, len(ids_trad), 200):
        blocco = ids_trad[inizio:inizio + 200]
        for a in db.session.scalars(
            db.select(PianificazioneOrdini).where(
                PianificazioneOrdini.IdPianificazione.in_(blocco)
            )
            .order_by(PianificazioneOrdini.IdPianificazione,
                      PianificazioneOrdini.IdDocumento,
                      PianificazioneOrdini.IdRiga)
        ).all():
            associazioni[a.IdPianificazione].append(a)

    chiavi = {
        (a.IdDocumento, a.IdRiga)
        for lista in associazioni.values() for a in lista
    }
    chiavi.update(
        (o.IdDocumento, o.IdRigaDoc)
        for piano in piani_misti for o in piano.bozza.ordini
    )
    anagrafiche = _informazioni_ordini(chiavi)
    note_vendite = _commenti(chiavi, NotaVenditeOrdine)
    note_produzione = _commenti(chiavi, NotaProduzioneOrdine)
    note_etichette = commenti_etichette_per_righe(
        [{"IdDocumento": documento, "IdRigaDoc": riga} for documento, riga in chiavi],
        True,
    ) if chiavi else {}

    ids_forni = {p.IdForno for p in piani_trad}
    ids_forni.update(f.IdForno for p in piani_misti for f in p.bozza.forni)
    nomi_forni = {}
    if ids_forni:
        nomi_forni = {
            f.IdForno: (f.DescrizioneForno or f.ModelloForno or f'Forno #{f.IdForno}')
            for f in db.session.scalars(
                db.select(AnagraficaForni).where(AnagraficaForni.IdForno.in_(ids_forni))
            ).all()
        }

    def riga_ordine(documento: int, riga: int, codice: str, quantita: int,
                   da_stock: int = 0) -> dict:
        chiave = (documento, riga)
        anag = anagrafiche.get(chiave)
        return {
            'IdDocumento': documento,
            'IdRigaDoc': riga,
            'CodArt': codice,
            'DesArt': anag.DesArt if anag else '',
            'Cliente': (
                anag.Cliente.RagioneSociale if anag and anag.Cliente
                else 'Cliente non disponibile nel feed ERP'
            ),
            'DataConsegna': anag.DataConsegna if anag else None,
            'QtaPianificata': int(quantita or 0),
            'QtaDaStock': int(da_stock or 0),
            'NoteVendite': note_vendite.get(chiave, []),
            'NoteProduzione': note_produzione.get(chiave, []),
            'CommentoEtichetta': note_etichette.get(chiave),
        }

    righe = []
    for p in piani_trad:
        ordini = [
            riga_ordine(a.IdDocumento, a.IdRiga, p.CodArt,
                        a.QtaPianificata, a.QtaDaStock)
            for a in associazioni[p.IdPianificazione]
        ]
        righe.append({
            'tipo': 'TRAD', 'id': p.IdPianificazione,
            'data': p.DataProduzione, 'stato': p.StatoRigaOrdine,
            'articoli': p.CodArt, 'numero_ordini': len(ordini),
            'qta': int(p.PanettoniPianificati or 0),
            'qta_assegnata': sum(o['QtaPianificata'] for o in ordini),
            'cotte': int(p.NumeroCotte or 0),
            'peso_kg': float(p.PesoTotale or 0) / 1000,
            'forni': nomi_forni.get(p.IdForno, f'Forno #{p.IdForno}'),
            'ordini': ordini,
        })

    for p in piani_misti:
        b = p.bozza
        ordini = [
            riga_ordine(o.IdDocumento, o.IdRigaDoc, o.CodArt,
                        o.QtaPianificata)
            for o in sorted(b.ordini, key=lambda x: (x.CodArt, x.IdDocumento, x.IdRigaDoc))
        ]
        righe.append({
            'tipo': 'MISTA', 'id': b.IdBozza,
            'data': b.DataProduzione, 'stato': p.Stato,
            'articoli': ' · '.join(sorted({o.CodArt for o in b.ordini})),
            'numero_ordini': len(ordini),
            'qta': sum(o['QtaPianificata'] for o in ordini),
            'qta_assegnata': sum(o['QtaPianificata'] for o in ordini),
            'cotte': int(b.NumeroCotte or 0),
            'peso_kg': float(b.PesoTotaleGrammi or 0) / 1000,
            'forni': ' · '.join(nomi_forni.get(f.IdForno, f'Forno #{f.IdForno}')
                                 for f in sorted(b.forni, key=lambda x: x.IdForno)),
            'ordini': ordini,
        })

    return sorted(righe, key=lambda r: (r['data'], r['tipo'], r['id']), reverse=True)
