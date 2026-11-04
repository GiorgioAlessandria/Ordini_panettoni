"""Anteprima e conferma della ripianificazione mista, protette da permessi e CSRF."""
from __future__ import annotations

from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError

from flaskr.accessi import ruolo_attuale, solo_ruoli
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.routes_modules.impostazioni import _operatore_bozza_mista, _verifica_csrf
from flaskr.service.ripianificazione_mista_service import (
    ErroreRipianificazioneMista, prepara_ripianificazione_mista,
    salva_ripianificazione_mista, storico_piano_misto,
)


@bp_ordini_cliente.app_context_processor
def _storico_ripianificazione_mista_contesto():
    return {'storico_piano_misto': storico_piano_misto}


@bp_ordini_cliente.get('/pianificazione/impasto-misto/bozze/<int:id_bozza>/ripianifica/anteprima',
                       endpoint='anteprima_ripianificazione_mista')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def anteprima_ripianificazione_mista(id_bozza: int):
    try:
        giorno = date.fromisoformat(request.args.get('nuova_data') or '')
        dati = prepara_ripianificazione_mista(
            id_bozza, giorno, strict=False, include_cnf=(ruolo_attuale() == 'admin')
        )
        errore = None
    except (ValueError, ErroreRipianificazioneMista) as exc:
        dati, errore = None, str(exc)
    return render_template('ordini/_partials/_anteprima_ripianificazione_mista.j2',
                           dati=dati, errore=errore)


@bp_ordini_cliente.post('/pianificazione/impasto-misto/bozze/<int:id_bozza>/ripianifica',
                        endpoint='conferma_ripianificazione_mista')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def conferma_ripianificazione_mista(id_bozza: int):
    _verifica_csrf()
    if not current_app.config.get('ABILITA_CONFERMA_PIANI_MISTI', False):
        abort(403, description='Conferma dei piani misti non abilitata sul server.')
    if request.form.get('conferma_capienza_mista') != '1':
        flash('Conferma la verifica fisica del numero di cotte dichiarato per ogni forno.', 'danger')
        return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
    try:
        nuova_data = date.fromisoformat(request.form['nuova_data'])
        nuovo_id = salva_ripianificazione_mista(
            id_bozza, nuova_data, _operatore_bozza_mista(),
            request.form.get('motivo', ''), include_cnf=(ruolo_attuale() == 'admin'),
        )
    except (KeyError, ValueError, ErroreRipianificazioneMista) as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
    except SQLAlchemyError:
        current_app.logger.exception('Ripianificazione mista: errore SQL')
        flash('Errore database: nessuna ripianificazione salvata.', 'danger')
        return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
    flash(f'Ripianificazione M-{nuovo_id} creata. Il piano M-{id_bozza} rimane nello storico.', 'success')
    return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=nuovo_id))
