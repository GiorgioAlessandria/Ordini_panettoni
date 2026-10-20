"""Pagina amministrativa delle anagrafiche e associazioni produttive."""
from __future__ import annotations

import secrets
from functools import wraps
from math import ceil

from flask import (
    abort, current_app, flash, g, redirect, render_template, request, session, url_for,
)
from sqlalchemy import String, cast, inspect, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flaskr.extensions import db
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.impostazioni_service import (
    GRUPPI, SEZIONI, ErroreImpostazioni, chiavi_pk, codifica_pk,
    conta, decodifica_pk, estrai_valori, opzioni_relazione, record_ha_dipendenze,
)


PAGE_SIZE = 12


def solo_configuratori(funzione):
    @wraps(funzione)
    def inner(*args, **kwargs):
        # Negazione predefinita. Non tutti gli utenti autenticati devono
        # poter modificare coefficienti, macchine e associazioni.
        autorizzati = current_app.config.get('IMPOSTAZIONI_ADMIN_USERS', ())
        if isinstance(autorizzati, str):
            autorizzati = [s.strip() for s in autorizzati.split(',') if s.strip()]
        if not autorizzati or g.user.username not in autorizzati:
            abort(403, description='Non sei autorizzato a modificare le impostazioni.')
        return funzione(*args, **kwargs)
    return inner


def _csrf_token():
    if 'impostazioni_csrf' not in session:
        session['impostazioni_csrf'] = secrets.token_urlsafe(32)
    return session['impostazioni_csrf']


def _verifica_csrf():
    token = request.form.get('_csrf_token', '')
    salvato = session.get('impostazioni_csrf', '')
    if not salvato or not secrets.compare_digest(token, salvato):
        abort(400, description='Sessione scaduta o token di sicurezza non valido.')


def _sezione_o_404(slug):
    spec = SEZIONI.get(slug)
    if spec is None:
        abort(404)
    return spec


def _link_di_ritorno(slug):
    return url_for('ordini_cliente.impostazioni_pagina', sezione=slug)


def _render_pagina(slug, *, dati_form=None, errore=None, record_token=None):
    spec = _sezione_o_404(slug)
    token_modifica = record_token if record_token is not None else request.args.get('modifica', '')
    record_modifica = None
    if token_modifica:
        try:
            key = decodifica_pk(spec, token_modifica)
        except ErroreImpostazioni:
            abort(400)
        record_modifica = db.session.get(spec.modello, key)
        if record_modifica is None:
            abort(404)

    q = request.args.get('q', '').strip()[:80]
    try:
        numero_pagina = max(1, int(request.args.get('pagina', '1')))
    except ValueError:
        numero_pagina = 1

    stmt = select(spec.modello)
    if q:
        colonne = [cast(getattr(spec.modello, c.nome), String).ilike(f'%{q}%') for c in spec.campi]
        if colonne:
            stmt = stmt.where(or_(*colonne))
    tot = db.session.scalar(select(db.func.count()).select_from(stmt.subquery())) or 0
    pagine = max(1, ceil(tot / PAGE_SIZE))
    numero_pagina = min(numero_pagina, pagine)
    ordina = [getattr(spec.modello, k).asc() for k in chiavi_pk(spec)]
    righe = db.session.scalars(stmt.order_by(*ordina).offset((numero_pagina-1)*PAGE_SIZE).limit(PAGE_SIZE)).all()

    opzioni = {}
    etichette_fk = {}
    for campo in spec.campi:
        if campo.tipo == 'fk':
            opzioni[campo.nome] = opzioni_relazione(campo)
            etichette_fk[campo.nome] = {str(k): v for k, v in opzioni[campo.nome]}

    # I campi da PK composte sono selezionati solo in creazione. Per cambiare
    # un'associazione si elimina la precedente e se ne aggiunge una nuova.
    valori_form = (
        dict(dati_form) if dati_form is not None else
        {c.nome: getattr(record_modifica, c.nome) for c in spec.campi} if record_modifica is not None else
        {c.nome: request.args.get(c.nome, '') for c in spec.campi}
    )
    return render_template(
        'ordini/impostazioni.j2',
        spec=spec,
        sezioni=SEZIONI,
        gruppi=GRUPPI,
        statistiche={s: conta(v) for s, v in SEZIONI.items()},
        righe=righe,
        q=q,
        pagina=numero_pagina,
        pagine=pagine,
        totale=tot,
        chiavi=chiavi_pk(spec),
        codifica_pk=codifica_pk,
        opzioni=opzioni,
        etichette_fk=etichette_fk,
        valori_form=valori_form,
        token_modifica=token_modifica,
        record_modifica=record_modifica,
        errore=errore,
        csrf_token=_csrf_token(),
    )


@bp_ordini_cliente.get('/impostazioni')
@login_required
@solo_configuratori
def impostazioni_index():
    return redirect(_link_di_ritorno('articoli-ricette'))


@bp_ordini_cliente.get('/impostazioni/<sezione>')
@login_required
@solo_configuratori
def impostazioni_pagina(sezione):
    return _render_pagina(sezione)


@bp_ordini_cliente.post('/impostazioni/<sezione>/salva')
@login_required
@solo_configuratori
def impostazioni_salva(sezione):
    _verifica_csrf()
    spec = _sezione_o_404(sezione)
    token = request.form.get('_record_token', '')
    record = None
    if token:
        try:
            key = decodifica_pk(spec, token)
        except ErroreImpostazioni:
            abort(400)
        record = db.session.get(spec.modello, key)
        if record is None:
            abort(404)

    try:
        dati = estrai_valori(spec, request.form, record)
        if record is None:
            record = spec.modello(**dati)
            db.session.add(record)
        else:
            for campo in spec.campi:
                if campo.nome not in chiavi_pk(spec):
                    setattr(record, campo.nome, dati[campo.nome])
        db.session.commit()
    except (ErroreImpostazioni, IntegrityError) as errore:
        db.session.rollback()
        if isinstance(errore, IntegrityError):
            messaggio = 'Impossibile salvare: relazione duplicata oppure vincolo del database non rispettato.'
        else:
            messaggio = str(errore)
        current_app.logger.info('Impostazioni non salvate: %s', messaggio)
        return _render_pagina(sezione, dati_form=request.form, errore=messaggio, record_token=token)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Errore database durante il salvataggio di %s', sezione)
        return _render_pagina(sezione, dati_form=request.form, errore='Errore durante il salvataggio. Nessuna modifica registrata.', record_token=token)

    flash('Configurazione salvata correttamente.', 'success')
    return redirect(_link_di_ritorno(sezione))


@bp_ordini_cliente.post('/impostazioni/<sezione>/elimina')
@login_required
@solo_configuratori
def impostazioni_elimina(sezione):
    _verifica_csrf()
    spec = _sezione_o_404(sezione)
    if not spec.consentire_eliminazione:
        abort(403)
    try:
        key = decodifica_pk(spec, request.form.get('_record_token', ''))
    except ErroreImpostazioni:
        abort(400)
    record = db.session.get(spec.modello, key)
    if record is None:
        abort(404)
    try:
        if sezione == 'coefficienti' and record.Coefficiente in (
            'PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA',
            'PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO',
        ):
            flash('Questo coefficiente è richiesto dalle formule di pianificazione e non può essere eliminato.', 'warning')
            return redirect(_link_di_ritorno(sezione))
        if record_ha_dipendenze(spec, record):
            flash('Impossibile eliminare: il dato è già utilizzato da altre tabelle o pianificazioni.', 'warning')
            return redirect(_link_di_ritorno(sezione))
        db.session.delete(record)
        db.session.commit()
        flash('Elemento rimosso.', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('Eliminazione impedita dai vincoli del database.', 'danger')
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Errore durante eliminazione impostazioni: %s', sezione)
        flash('Errore database. Nessuna modifica registrata.', 'danger')
    return redirect(_link_di_ritorno(sezione))
