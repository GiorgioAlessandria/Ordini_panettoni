"""Endpoint HTMX (sola lettura) per la verifica della scadenza di consegna.

Importato una sola volta da flaskr.routes_modules.ordini prima della registrazione
 del blueprint. Non interfere con simulazione o conferma delle pianificazioni.
"""
from __future__ import annotations

import re
from datetime import date

from flask import abort, render_template, request
from sqlalchemy import tuple_
from sqlalchemy.orm import selectinload

from flaskr.accessi import ruolo_attuale, solo_ruoli
from flaskr.extensions import db
from flaskr.models.model_ordini import (
    GruppoProduzione, OrdiniCliente, RicettaFasi, TipRicetteAnagArticoli,
)
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.avviso_consegna_pianificazione_service import (
    ErroreAvvisoConsegna, calcola_avviso_consegna,
)
from flaskr.service.scadenze_giornaliere_service import articolo_cnf

_CHIAVE = re.compile(r'^([1-9]\d*)-([1-9]\d*)$')
_TEMPLATE = 'ordini/_partials/_avviso_consegna_pianificazione.j2'


def _ordini_form(misto: bool) -> list[OrdiniCliente]:
    nomi = request.form.getlist('ordini' if misto else 'ordini_selezionati')
    if not nomi:
        raise ErroreAvvisoConsegna('Seleziona almeno un ordine per verificare la consegna.')
    if len(nomi) > 200:
        raise ErroreAvvisoConsegna('La verifica supporta al massimo 200 ordini.')
    chiavi = []
    for nome in nomi:
        match = _CHIAVE.fullmatch(nome)
        if match is None:
            raise ErroreAvvisoConsegna('Identificativo ordine non valido.')
        chiavi.append((int(match[1]), int(match[2])))
    if len(set(chiavi)) != len(chiavi):
        raise ErroreAvvisoConsegna('La selezione contiene ordini ripetuti.')
    trovati = db.session.scalars(db.select(OrdiniCliente).where(
        tuple_(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc).in_(chiavi)
    )).all()
    if len(frozenset((o.IdDocumento, o.IdRigaDoc) for o in trovati)) != len(chiavi):
        raise ErroreAvvisoConsegna('Uno o più ordini selezionati non esistono più.')
    if ruolo_attuale() == 'utente_produzione' and any(articolo_cnf(o.CodArt) for o in trovati):
        abort(404)
    return trovati


def _fasi_articoli(codici: set[str]) -> list[RicettaFasi]:
    abbinamenti = db.session.execute(
        db.select(TipRicetteAnagArticoli.CodArt, TipRicetteAnagArticoli.IdRicetta)
        .where(TipRicetteAnagArticoli.CodArt.in_(codici))
    ).all()
    per_codice: dict[str, set[int]] = {cod: set() for cod in codici}
    for codice, ricetta in abbinamenti:
        per_codice[codice].add(ricetta)
    if any(len(ricette) != 1 for ricette in per_codice.values()):
        raise ErroreAvvisoConsegna('Ogni articolo selezionato deve avere una sola ricetta associata.')
    ricette_uniche = set().union(*per_codice.values())
    fasi = db.session.scalars(
        db.select(RicettaFasi)
        .where(RicettaFasi.IdRicetta.in_(ricette_uniche))
        .options(selectinload(RicettaFasi.lavorazione))
    ).all()
    if {f.IdRicetta for f in fasi} != ricette_uniche:
        raise ErroreAvvisoConsegna('Una delle ricette selezionate non contiene fasi.')
    return fasi


@bp_ordini_cliente.post('/pianificazione/avviso-consegna', endpoint='avviso_consegna_pianificazione')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def avviso_consegna_pianificazione():
    misto = 'data_inizio' in request.form or 'gruppo' in request.form
    data_raw = request.form.get('data_inizio' if misto else 'data_inizio_produzione', '')
    blocco_raw = request.form.get('blocco' if misto else 'blocco_inizio_produzione', '')
    if not data_raw or not blocco_raw:
        return render_template(_TEMPLATE, esito=None, errore=None,
                               messaggio='Seleziona la data e il blocco iniziale per verificare la consegna.')
    try:
        data_inizio = date.fromisoformat(data_raw)
        if misto:
            blocco = blocco_raw
        else:
            blocco = {'0': 'M', '1': 'P'}.get(blocco_raw, '')
        ordini = _ordini_form(misto)
        codici = {o.CodArt for o in ordini}
        if misto:
            try:
                id_gruppo = int(request.form.get('gruppo', ''))
            except ValueError as errore:
                raise ErroreAvvisoConsegna('Gruppo di produzione non valido.') from errore
            gruppo = db.session.get(GruppoProduzione, id_gruppo)
            if gruppo is None or not gruppo.Attivo:
                raise ErroreAvvisoConsegna('Il gruppo selezionato non è attivo.')
            associati = {a.CodArt for a in gruppo.articoli}
            if not codici.issubset(associati):
                raise ErroreAvvisoConsegna('Un ordine non appartiene al gruppo selezionato.')
        elif len(codici) != 1:
            raise ErroreAvvisoConsegna('Per ordini di articoli differenti usa la pianificazione a impasto misto.')
        esito = calcola_avviso_consegna(data_inizio, blocco, _fasi_articoli(codici), ordini)
        return render_template(_TEMPLATE, esito=esito, errore=None, messaggio=None)
    except (ValueError, OverflowError) as errore:
        return render_template(_TEMPLATE, esito=None, errore=str(errore), messaggio=None)
