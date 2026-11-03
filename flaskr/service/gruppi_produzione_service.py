"""Configurazione dei gruppi di articoli che condividono un impasto base.

Non gestisce quantità, cotte o conferma delle pianificazioni: quelle operazioni
richiederanno una successiva migrazione del modello produttivo.
"""
from __future__ import annotations

from sqlalchemy.orm import selectinload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaArticoli, GruppoProduzione, GruppoProduzioneArticolo,
    TipRicetteAnagArticoli,
)


class ErroreGruppoProduzione(ValueError):
    pass


def lista_configurazione():
    gruppi = db.session.scalars(
        db.select(GruppoProduzione)
        .options(selectinload(GruppoProduzione.articoli))
        .order_by(GruppoProduzione.Nome, GruppoProduzione.IdGruppo)
    ).all()
    articoli = db.session.scalars(
        db.select(AnagraficaArticoli).order_by(AnagraficaArticoli.CodArt)
    ).all()
    assegnazioni = {
        item.CodArt: gruppo.Nome
        for gruppo in gruppi for item in gruppo.articoli
    }
    return gruppi, articoli, assegnazioni


def salva_gruppo_produzione(*, id_gruppo: int | None, nome: str,
                            descrizione: str, attivo: str,
                            codici: list[str]) -> GruppoProduzione:
    nome = (nome or '').strip()
    descrizione = (descrizione or '').strip()
    if not (2 <= len(nome) <= 100):
        raise ErroreGruppoProduzione('Il nome del gruppo deve contenere da 2 a 100 caratteri.')
    if len(descrizione) > 400:
        raise ErroreGruppoProduzione('La descrizione non può superare 400 caratteri.')
    if attivo not in ('0', '1'):
        raise ErroreGruppoProduzione('Seleziona uno stato valido per il gruppo.')
    if len(codici) > 500:
        raise ErroreGruppoProduzione('Troppi codici articolo selezionati.')
    codici_set = {c.strip() for c in codici if c and c.strip()}
    if len(codici_set) < 2:
        raise ErroreGruppoProduzione('Seleziona almeno due codici articolo per l’impasto comune.')

    gruppo = db.session.get(GruppoProduzione, id_gruppo) if id_gruppo is not None else None
    if id_gruppo is not None and gruppo is None:
        raise ErroreGruppoProduzione('Il gruppo da modificare non esiste.')

    esistenti = set(db.session.scalars(
        db.select(AnagraficaArticoli.CodArt).where(AnagraficaArticoli.CodArt.in_(codici_set))
    ).all())
    assenti = sorted(codici_set - esistenti)
    if assenti:
        raise ErroreGruppoProduzione('Articoli non presenti nell’anagrafica ERP: ' + ', '.join(assenti[:8]))

    # Un gruppo produttivo è utilizzabile solo se ogni articolo dispone già di
    # una ricetta. Ricette diverse sono permesse: il loro impasto base comune
    # verrà validato nella successiva fase del progetto.
    associati = set(db.session.scalars(
        db.select(TipRicetteAnagArticoli.CodArt)
        .where(TipRicetteAnagArticoli.CodArt.in_(codici_set))
    ).all())
    senza_ricetta = sorted(codici_set - associati)
    if senza_ricetta:
        raise ErroreGruppoProduzione('Articoli senza una ricetta associata: ' + ', '.join(senza_ricetta[:8]))

    occupati = db.session.execute(
        db.select(GruppoProduzioneArticolo.CodArt, GruppoProduzione.Nome)
        .join(GruppoProduzione, GruppoProduzione.IdGruppo == GruppoProduzioneArticolo.IdGruppo)
        .where(GruppoProduzioneArticolo.CodArt.in_(codici_set))
        .where(GruppoProduzioneArticolo.IdGruppo != (id_gruppo if id_gruppo is not None else -1))
    ).all()
    if occupati:
        codice, altro = sorted(occupati)[0]
        raise ErroreGruppoProduzione(f'L’articolo {codice} appartiene già al gruppo «{altro}».')

    nome_occupato = db.session.scalar(
        db.select(GruppoProduzione.IdGruppo)
        .where(db.func.lower(GruppoProduzione.Nome) == nome.lower())
        .where(GruppoProduzione.IdGruppo != (id_gruppo if id_gruppo is not None else -1))
    )
    if nome_occupato is not None:
        raise ErroreGruppoProduzione('Esiste già un gruppo con questo nome.')

    if gruppo is None:
        gruppo = GruppoProduzione(Nome=nome, Descrizione=descrizione or None,
                                 Attivo=int(attivo))
        db.session.add(gruppo)
        db.session.flush()
    else:
        gruppo.Nome = nome
        gruppo.Descrizione = descrizione or None
        gruppo.Attivo = int(attivo)

    attuali = set(db.session.scalars(
        db.select(GruppoProduzioneArticolo.CodArt)
        .where(GruppoProduzioneArticolo.IdGruppo == gruppo.IdGruppo)
    ).all())
    for codice in sorted(attuali - codici_set):
        item = db.session.get(GruppoProduzioneArticolo, codice)
        if item is not None:
            db.session.delete(item)
    for codice in sorted(codici_set - attuali):
        db.session.add(GruppoProduzioneArticolo(CodArt=codice, IdGruppo=gruppo.IdGruppo))
    db.session.flush()
    return gruppo
