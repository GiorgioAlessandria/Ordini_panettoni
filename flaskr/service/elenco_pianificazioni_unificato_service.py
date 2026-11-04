"""Riepilogo sola lettura dei piani tradizionali e misti attivi in una vista unica."""
from __future__ import annotations
from sqlalchemy.orm import selectinload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaForni, BozzaPianificazioneMista, PianificazioneMistaAttiva,
    PianificazioneProduzione,
)
from flaskr.service.scadenze_giornaliere_service import articolo_cnf


def righe_pianificazioni_unificate(tradizionali: list[dict], *, nascondi_cnf: bool) -> list[dict]:
    ids = {r['IdPianificazione'] for r in tradizionali if r.get('IdPianificazione') is not None}
    piani = {p.IdPianificazione: p for p in db.session.scalars(
        db.select(PianificazioneProduzione).where(PianificazioneProduzione.IdPianificazione.in_(ids))
    ).all()} if ids else {}
    miste = db.session.scalars(
        db.select(PianificazioneMistaAttiva)
        .where(PianificazioneMistaAttiva.Stato == 'In produzione')
        .options(selectinload(PianificazioneMistaAttiva.bozza).selectinload(BozzaPianificazioneMista.ordini),
                 selectinload(PianificazioneMistaAttiva.bozza).selectinload(BozzaPianificazioneMista.forni))
    ).all()
    ids_forni = {p.IdForno for p in piani.values()}
    ids_forni |= {f.IdForno for p in miste for f in p.bozza.forni}
    nomi = {f.IdForno: (f.DescrizioneForno or f.ModelloForno or f'Forno #{f.IdForno}')
            for f in db.session.scalars(
                db.select(AnagraficaForni).where(AnagraficaForni.IdForno.in_(ids_forni))
            ).all()} if ids_forni else {}
    righe = []
    for r in tradizionali:
        p = piani.get(r.get('IdPianificazione'))
        if p is None:
            continue
        righe.append(dict(tipo='TRAD', id=p.IdPianificazione,
                          data=p.DataProduzione, stato=p.StatoRigaOrdine,
                          articoli=r.get('CodArt') or p.CodArt,
                          numero_ordini=r.get('NumeroOrdini', 1),
                          qta=int(p.PanettoniPianificati), cotte=int(p.NumeroCotte),
                          peso_kg=float(p.PesoTotale or 0) / 1000,
                          forni=nomi.get(p.IdForno, f'Forno #{p.IdForno}')))
    for p in miste:
        b=p.bozza
        if nascondi_cnf and any(articolo_cnf(o.CodArt) for o in b.ordini):
            continue
        righe.append(dict(tipo='MISTA', id=b.IdBozza, data=b.DataProduzione,
                          stato=p.Stato,
                          articoli=' · '.join(sorted({o.CodArt for o in b.ordini})),
                          numero_ordini=len(b.ordini),
                          qta=sum(int(o.QtaPianificata) for o in b.ordini),
                          cotte=int(b.NumeroCotte),
                          peso_kg=float(b.PesoTotaleGrammi or 0)/1000,
                          forni=' · '.join(nomi.get(f.IdForno, f'Forno #{f.IdForno}')
                                          for f in sorted(b.forni, key=lambda x: x.IdForno))))
    return sorted(righe, key=lambda r: (r['data'], r['tipo'], r['id']), reverse=True)
