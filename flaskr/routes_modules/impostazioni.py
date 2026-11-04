"""Pagina amministrativa delle anagrafiche e associazioni produttive."""
from __future__ import annotations

import re
import secrets
import uuid
from functools import wraps
from math import ceil

from flask import (
    abort, current_app, flash, g, redirect, render_template, request, session, url_for,
)
from sqlalchemy import String, cast, inspect, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flaskr.accessi import ruolo_attuale
from flaskr.extensions import db
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.impostazioni_service import (
    GRUPPI, SEZIONI, ErroreImpostazioni, chiavi_pk, codifica_pk,
    conta, decodifica_pk, estrai_valori, opzioni_relazione, record_ha_dipendenze,
)


from datetime import datetime, timezone
from flaskr.models.model_ordini import (
    AnagraficaClienti, OrdiniCliente,
    CommentoEtichettaConfigurazione, CommentoEtichettaClienteScelta, CommentoEtichettaOrdine, CommentoEtichettaOrdineTesto,
)
from flaskr.service.commenti_etichetta_service import (
    ErroreCommentoEtichetta, SEGNAPOSTO, indicazione_ordine,
    testi_globali, valida_testo, tipo_per_ordine, tipo_predefinito_cliente,
    dati_cella_ordine, commenti_etichette_per_righe,
)
from flaskr.accessi import solo_ruoli, verifica_csrf

PAGE_SIZE = 12
COLORE_FASE_PREDEFINITO = '#64748b'


def _normalizza_colore_fase(valore: str | None) -> str:
    """Accetta solo HEX RGB; evita valori CSS arbitrari inseriti nel form."""
    colore = (valore or COLORE_FASE_PREDEFINITO).strip()
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', colore):
        raise ErroreImpostazioni('Seleziona un colore valido nel formato #RRGGBB.')
    return colore.lower()


def solo_configuratori(funzione):
    @wraps(funzione)
    def inner(*args, **kwargs):
        # Negazione predefinita. Non tutti gli utenti autenticati devono
        # poter modificare coefficienti, macchine e associazioni.
        autorizzati = current_app.config.get('IMPOSTAZIONI_ADMIN_USERS', ())
        if isinstance(autorizzati, str):
            autorizzati = [s.strip() for s in autorizzati.split(',') if s.strip()]
        if ruolo_attuale() != "admin":
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
            # Manteniamo le etichette complete per i collegamenti già mostrati
            # nella lista. Filtriamo soltanto il menu di una nuova associazione.
            opzioni_complete = opzioni_relazione(campo)
            etichette_fk[campo.nome] = {str(k): v for k, v in opzioni_complete}
            if (spec.slug == 'articoli-ricette'
                    and campo.nome == 'CodArt'
                    and record_modifica is None):
                opzioni[campo.nome] = opzioni_relazione(
                    campo, solo_articoli_senza_ricetta=True,
                )
            else:
                opzioni[campo.nome] = opzioni_complete

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
        colore_fase_form=(
            (dati_form.get('Colore') or COLORE_FASE_PREDEFINITO)
            if dati_form is not None else
            (getattr(record_modifica, 'Colore', None) or COLORE_FASE_PREDEFINITO)
        ),
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
        # Questo campo non è nella scheda generica impostazioni_service:
        # lo salviamo solo per la sezione fasi, mantenendo la validazione esistente.
        colore_fase = (
            _normalizza_colore_fase(request.form.get('Colore'))
            if sezione == 'fasi' else None
        )
        dati = estrai_valori(spec, request.form, record)
        if record is None:
            record = spec.modello(**dati)
            db.session.add(record)
        else:
            for campo in spec.campi:
                if campo.nome not in chiavi_pk(spec):
                    setattr(record, campo.nome, dati[campo.nome])
        if sezione == 'fasi':
            record.Colore = colore_fase
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


# --- Commenti etichette: modelli globali e scelta predefinita per cliente ---
@bp_ordini_cliente.get('/impostazioni/commenti-etichette', endpoint='impostazioni_commenti_etichette')
@login_required
@solo_configuratori
def impostazioni_commenti_etichette():
    q = request.args.get('q', '').strip()[:80]
    try:
        pagina = max(1, int(request.args.get('pagina', '1')))
    except ValueError:
        pagina = 1
    query = db.select(AnagraficaClienti).where(AnagraficaClienti.TipoAnagrafica == 1)
    if q:
        query = query.where(or_(
            cast(AnagraficaClienti.CodCliFor, String).ilike(f'%{q}%'),
            AnagraficaClienti.RagioneSociale.ilike(f'%{q}%'),
        ))
    totale = db.session.scalar(db.select(db.func.count()).select_from(query.subquery())) or 0
    pagine = max(1, ceil(totale / PAGE_SIZE))
    pagina = min(pagina, pagine)
    clienti = db.session.scalars(
        query.order_by(AnagraficaClienti.RagioneSociale, AnagraficaClienti.CodCliFor)
        .offset((pagina - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    codici = [c.CodCliFor for c in clienti]
    scelte = {r.CodCliFor: r.TipoCommento for r in db.session.scalars(
        db.select(CommentoEtichettaClienteScelta).where(
            CommentoEtichettaClienteScelta.CodCliFor.in_(codici))
    ).all()} if codici else {}
    return render_template('ordini/impostazioni_commenti_etichette.j2',
        testi=testi_globali(), clienti=clienti, scelte=scelte,
        segnaposto=sorted(SEGNAPOSTO), q=q, pagina=pagina,
        pagine=pagine, totale=totale, csrf_token=_csrf_token(),
    )


@bp_ordini_cliente.post('/impostazioni/commenti-etichette/salva', endpoint='salva_commenti_etichette')
@login_required
@solo_configuratori
def salva_commenti_etichette():
    _verifica_csrf()
    try:
        t1 = valida_testo(request.form.get('testo1', ''))
        t2 = valida_testo(request.form.get('testo2', ''))
    except ErroreCommentoEtichetta as errore:
        flash(str(errore), 'danger')
        return redirect(url_for('ordini_cliente.impostazioni_commenti_etichette'))
    try:
        for tipo, testo in ((1, t1), (2, t2)):
            record = db.session.get(CommentoEtichettaConfigurazione, tipo)
            if record is None:
                record = CommentoEtichettaConfigurazione(Id=tipo, Testo=testo)
                db.session.add(record)
            record.Testo = testo
            record.ModificatoIl = datetime.now(timezone.utc)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Salvataggio dei commenti etichette fallito')
        flash('Impossibile salvare i commenti.', 'danger')
    else:
        flash('I due commenti sono stati aggiornati.', 'success')
    return redirect(url_for('ordini_cliente.impostazioni_commenti_etichette'))


@bp_ordini_cliente.post('/impostazioni/commenti-etichette/cliente/salva', endpoint='salva_commento_etichetta_cliente')
@login_required
@solo_configuratori
def salva_commento_etichetta_cliente():
    _verifica_csrf()
    try:
        cod_cliente = int(request.form.get('cod_cliente', ''))
    except (TypeError, ValueError):
        abort(400, description='Codice cliente non valido.')
    cliente = db.session.get(AnagraficaClienti, cod_cliente)
    if cliente is None or cliente.TipoAnagrafica != 1:
        abort(404, description='Cliente non trovato.')
    if request.form.get('tipo_commento') not in ('1', '2'):
        abort(400, description='Selezione commento non valida.')
    try:
        record = db.session.get(CommentoEtichettaClienteScelta, cod_cliente)
        if record is None:
            record = CommentoEtichettaClienteScelta(CodCliFor=cod_cliente)
            db.session.add(record)
        record.TipoCommento = int(request.form['tipo_commento'])
        record.ModificatoIl = datetime.now(timezone.utc)
        record.Operatore = getattr(g.user, 'username', 'admin')
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Errore commento predefinito cliente %s', cod_cliente)
        flash('Impossibile salvare il commento predefinito.', 'danger')
    else:
        flash(f'Commento iniziale aggiornato per il cliente {cod_cliente}.', 'success')
    q = request.form.get('q', '')[:80]
    pagina = request.form.get('pagina', '1')
    return redirect(url_for('ordini_cliente.impostazioni_commenti_etichette', q=q, pagina=pagina))


def _modifica_commento_etichetta_ordine(ordine: OrdiniCliente) -> None:
    """Una sola procedura per pagina dettaglio e tabella HTMX.

    La personalizzazione del testo riguarda SOLO (IdDocumento, IdRigaDoc, TipoCommento).
    """
    id_documento, id_riga = ordine.IdDocumento, ordine.IdRigaDoc
    azione = request.form.get('azione')
    if not azione:
        azione = ('ripristina_scelta' if request.form.get('ripristina_predefinito') == '1'
                  else 'salva_scelta')
    if azione not in ('salva_scelta', 'ripristina_scelta', 'salva_testo', 'ripristina_testo'):
        raise ErroreCommentoEtichetta('Operazione non valida.')

    if azione in ('salva_scelta', 'ripristina_scelta'):
        record = db.session.get(CommentoEtichettaOrdine, (id_documento, id_riga))
        if azione == 'ripristina_scelta':
            if record is not None:
                db.session.delete(record)
        else:
            tipo = request.form.get('tipo_commento')
            if tipo not in ('1', '2'):
                raise ErroreCommentoEtichetta('Selezione del commento non valida.')
            if record is None:
                record = CommentoEtichettaOrdine(IdDocumento=id_documento, IdRigaDoc=id_riga)
                db.session.add(record)
            record.TipoCommento = int(tipo)
            record.ModificatoIl = datetime.now(timezone.utc)
            record.Operatore = getattr(g.user, 'username', 'operatore')
    else:
        tipo = request.form.get('tipo_testo')
        if tipo not in ('1', '2') or int(tipo) != tipo_per_ordine(ordine)[0]:
            raise ErroreCommentoEtichetta(
                'Il commento selezionato è cambiato: aggiorna la riga e riprova.'
            )
        key = (id_documento, id_riga, int(tipo))
        record = db.session.get(CommentoEtichettaOrdineTesto, key)
        if azione == 'ripristina_testo':
            if record is not None:
                db.session.delete(record)
        else:
            testo = valida_testo(request.form.get('testo_commento', ''))
            # Se coincide con il modello attuale, non creare inutilmente
            # una copia che impedirebbe di ereditare future modifiche globali.
            testo_originale = indicazione_testo_globale(ordine, int(tipo))
            if testo == testo_originale:
                if record is not None:
                    db.session.delete(record)
            else:
                if record is None:
                    record = CommentoEtichettaOrdineTesto(
                        IdDocumento=id_documento, IdRigaDoc=id_riga,
                        TipoCommento=int(tipo),
                    )
                    db.session.add(record)
                record.Testo = testo
                record.ModificatoIl = datetime.now(timezone.utc)
                record.Operatore = getattr(g.user, 'username', 'operatore')
    db.session.commit()


def indicazione_testo_globale(ordine: OrdiniCliente, tipo: int) -> str:
    """Versione del testo globale senza le eventuali personalizzazioni dell'ordine."""
    from flaskr.service.commenti_etichetta_service import risolvi_testo
    return risolvi_testo(testi_globali()[tipo], ordine)


@bp_ordini_cliente.route('/ordine/<int:id_documento>/<int:id_riga>/commento-etichetta',
    methods=['GET', 'POST'], endpoint='commento_etichetta_ordine')
@solo_ruoli('admin', 'utente_produzione', 'utente_vendite')
def commento_etichetta_ordine(id_documento: int, id_riga: int):
    ordine = db.session.get(OrdiniCliente, (id_documento, id_riga))
    if ordine is None:
        abort(404)
    from flaskr.service.scadenze_giornaliere_service import articolo_cnf
    if ruolo_attuale() == 'utente_produzione' and articolo_cnf(ordine.CodArt):
        abort(404)
    puo_modificare = ruolo_attuale() in ('admin', 'utente_produzione')
    if request.method == 'POST':
        if not puo_modificare:
            abort(403)
        verifica_csrf()
        try:
            _modifica_commento_etichetta_ordine(ordine)
        except ErroreCommentoEtichetta as errore:
            db.session.rollback()
            flash(str(errore), 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                'Errore indicazione ordine %s-%s', id_documento, id_riga
            )
            flash('Indicazione non salvata.', 'danger')
        else:
            flash('Indicazione aggiornata.', 'success')
        return redirect(url_for('ordini_cliente.commento_etichetta_ordine',
                                id_documento=id_documento, id_riga=id_riga))
    tipo, scelta_manuale = tipo_per_ordine(ordine)
    predefinito, configurato = tipo_predefinito_cliente(ordine.CodCliFor)
    item = dati_cella_ordine(ordine)
    return render_template('ordini/commento_etichetta_ordine.j2',
        ordine=ordine, tipo=tipo, scelta_manuale=scelta_manuale,
        predefinito=predefinito, configurato=configurato,
        testo=item['testo'], personalizzato=item['personalizzato'],
        alternativa1=indicazione_ordine(ordine, tipo=1)[1],
        alternativa2=indicazione_ordine(ordine, tipo=2)[1],
        puo_modificare=puo_modificare,
    )


# La stessa cella viene usata in "Da produrre" e nei gruppi "In produzione".
@bp_ordini_cliente.app_context_processor
def _context_celle_commenti_etichette():
    return {
        'commenti_etichette_per_righe': commenti_etichette_per_righe,
        'puo_modificare_commenti_etichette': ruolo_attuale() in ('admin', 'utente_produzione'),
    }


@bp_ordini_cliente.post(
    '/ordine/<int:id_documento>/<int:id_riga>/commento-etichetta/cella',
    endpoint='commento_etichetta_cella',
)
@solo_ruoli('admin', 'utente_produzione')
def commento_etichetta_cella(id_documento: int, id_riga: int):
    """Cambia scelta o testo del solo commento selezionato, in una riga HTMX."""
    verifica_csrf()
    ordine = db.session.get(OrdiniCliente, (id_documento, id_riga))
    if ordine is None:
        abort(404)
    from flaskr.service.scadenze_giornaliere_service import articolo_cnf
    if ruolo_attuale() == 'utente_produzione' and articolo_cnf(ordine.CodArt):
        abort(404)

    errore = None
    try:
        _modifica_commento_etichetta_ordine(ordine)
    except ErroreCommentoEtichetta as exc:
        db.session.rollback()
        errore = str(exc)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Errore commento etichetta %s-%s', id_documento, id_riga)
        errore = 'Impossibile salvare il commento. Nessuna modifica registrata.'

    return render_template(
        'ordini/_partials/_commento_etichetta_cella.j2',
        id_documento=id_documento,
        id_riga=id_riga,
        item=dati_cella_ordine(ordine),
        errore=errore,
        testo_form=(request.form.get('testo_commento', '')
                    if errore and request.form.get('azione') == 'salva_testo' else None),
        puo_modificare_commenti_etichette=True,
    )

# --- Gruppi di produzione: un impasto comune per codici articolo differenti. ---
from flaskr.models.model_ordini import AnagraficaArticoli, GruppoProduzione, GruppoProduzioneArticolo
from flaskr.service.gruppi_produzione_service import (
    ErroreGruppoProduzione, lista_configurazione, salva_gruppo_produzione,
)
from sqlalchemy.orm import selectinload


def _render_gruppi_produzione(*, errore=None, valori=None, id_modifica=None):
    gruppi, articoli, assegnazioni = lista_configurazione()
    gruppo_corrente = None
    if id_modifica is None:
        id_modifica = request.args.get('modifica', '').strip()
    if id_modifica:
        try:
            numero = int(id_modifica)
        except (TypeError, ValueError):
            abort(400)
        gruppo_corrente = db.session.get(GruppoProduzione, numero)
        if gruppo_corrente is None:
            abort(404)
    if valori is None:
        selezionati = (
            {item.CodArt for item in gruppo_corrente.articoli}
            if gruppo_corrente is not None else set()
        )
        valori = {
            'nome': gruppo_corrente.Nome if gruppo_corrente else '',
            'descrizione': gruppo_corrente.Descrizione or '' if gruppo_corrente else '',
            'attivo': str(gruppo_corrente.Attivo) if gruppo_corrente else '1',
            'codici': selezionati,
        }
    return render_template(
        'ordini/impostazioni_gruppi_produzione.j2',
        gruppi=gruppi, articoli=articoli, assegnazioni=assegnazioni,
        gruppo_corrente=gruppo_corrente, valori=valori,
        errore=errore, csrf_token=_csrf_token(),
    )


@bp_ordini_cliente.get('/impostazioni/gruppi-produzione', endpoint='impostazioni_gruppi_produzione')
@login_required
@solo_configuratori
def impostazioni_gruppi_produzione():
    return _render_gruppi_produzione()


@bp_ordini_cliente.post('/impostazioni/gruppi-produzione/salva', endpoint='salva_gruppo_produzione')
@login_required
@solo_configuratori
def salva_gruppo_produzione_route():
    _verifica_csrf()
    id_raw = request.form.get('id_gruppo', '').strip()
    if id_raw:
        try:
            id_gruppo = int(id_raw)
        except ValueError:
            abort(400)
        if id_gruppo <= 0:
            abort(400)
    else:
        id_gruppo = None
    dati = {
        'nome': request.form.get('nome', ''),
        'descrizione': request.form.get('descrizione', ''),
        'attivo': request.form.get('attivo', ''),
        'codici': set(request.form.getlist('codici')),
    }
    try:
        gruppo = salva_gruppo_produzione(
            id_gruppo=id_gruppo,
            nome=dati['nome'], descrizione=dati['descrizione'],
            attivo=dati['attivo'], codici=list(dati['codici']),
        )
        db.session.commit()
    except ErroreGruppoProduzione as exc:
        db.session.rollback()
        return _render_gruppi_produzione(errore=str(exc), valori=dati, id_modifica=id_gruppo), 400
    except IntegrityError:
        db.session.rollback()
        return _render_gruppi_produzione(
            errore='Impossibile salvare: nome o articolo già associato, oppure vincolo del database.',
            valori=dati, id_modifica=id_gruppo,
        ), 409
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Errore salvataggio gruppi di produzione')
        return _render_gruppi_produzione(
            errore='Errore database: nessuna modifica registrata.',
            valori=dati, id_modifica=id_gruppo,
        ), 500
    flash('Gruppo di produzione salvato.', 'success')
    return redirect(url_for('ordini_cliente.impostazioni_gruppi_produzione', modifica=gruppo.IdGruppo))


@bp_ordini_cliente.post('/impostazioni/gruppi-produzione/elimina', endpoint='elimina_gruppo_produzione')
@login_required
@solo_configuratori
def elimina_gruppo_produzione():
    _verifica_csrf()
    try:
        id_gruppo = int(request.form.get('id_gruppo', ''))
    except (TypeError, ValueError):
        abort(400)
    gruppo = db.session.get(GruppoProduzione, id_gruppo)
    if gruppo is None:
        abort(404)
    try:
        # Al momento i gruppi sono solo configurazione: nessuna pianificazione
        # li referenzia. Le associazioni articolo sono eliminate insieme al gruppo.
        db.session.delete(gruppo)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('Il gruppo è utilizzato da altri dati e non può essere eliminato.', 'warning')
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Errore eliminazione gruppo produzione %s', id_gruppo)
        flash('Impossibile eliminare il gruppo.', 'danger')
    else:
        flash('Gruppo eliminato. Gli articoli ERP rimangono invariati.', 'success')
    return redirect(url_for('ordini_cliente.impostazioni_gruppi_produzione'))

# --- Fasi 2 e 3A: simulazione e salvataggio BOZZE, mai pianificazioni attive. ---
from flaskr.models.model_ordini import (
    AnagraficaForni, AnagraficaImpastatrici, BozzaPianificazioneMista,
)
from flaskr.service.anteprima_impasto_misto_service import (
    ErroreAnteprimaMista, carica_gruppi, carica_scelte, forni_per_articolo, simula,
)
from flaskr.service.bozze_impasto_misto_service import (
    ErroreBozzaMista, carica_bozze, salva_bozza,
)
from flaskr.service.pianificazione_service import ErroreCalcoloPianificazione


@bp_ordini_cliente.route('/pianificazione/impasto-misto', methods=['GET', 'POST'],
                        endpoint='anteprima_impasto_misto')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def anteprima_impasto_misto():
    """Simula cotte condivise: non persiste piani e non prenota macchine."""
    if request.method == 'POST':
        _verifica_csrf()
    gruppi = carica_gruppi()
    error = None
    esito = None
    gruppo = None
    articoli = []
    ordini = []
    disponibili = {}
    forni_compatibili = {}
    bozza_ripresa = None
    if request.method == 'GET' and request.args.get('bozza'):
        try:
            id_bozza = int(request.args['bozza'])
        except ValueError:
            abort(400)
        bozza_ripresa = db.session.get(BozzaPianificazioneMista, id_bozza)
        if bozza_ripresa is None:
            abort(404)
    gruppo_raw = request.values.get('gruppo', '').strip()
    if bozza_ripresa is not None:
        if db.session.get(PianificazioneMistaAttiva, bozza_ripresa.IdBozza) is not None:
            flash('Una proposta già confermata non può essere riutilizzata. Crea una nuova bozza.', 'warning')
            return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=bozza_ripresa.IdBozza))
        gruppo_raw = str(bozza_ripresa.IdGruppo)
    try:
        id_gruppo = int(gruppo_raw) if gruppo_raw else (gruppi[0].IdGruppo if gruppi else None)
    except ValueError:
        abort(400)
    if id_gruppo is not None:
        try:
            gruppo, articoli, ordini, disponibili = carica_scelte(
                id_gruppo, include_cnf=(ruolo_attuale() == "admin")
            )
            forni_compatibili = forni_per_articolo(articoli)
        except (ErroreAnteprimaMista, ValueError) as exc:
            error = str(exc)

    forni_id = sorted({fid for opzioni in forni_compatibili.values() for fid in opzioni})
    forni = {
        f.IdForno: f for f in db.session.scalars(db.select(AnagraficaForni).where(
            AnagraficaForni.IdForno.in_(forni_id)
        )).all()
    }
    impastatrici = db.session.scalars(db.select(AnagraficaImpastatrici).order_by(
        AnagraficaImpastatrici.IdImpastatrice
    )).all()
    # Valori del modulo non cambiano finché non viene avviata un'altra anteprima.
    scelte = request.form.getlist('ordini') if request.method == 'POST' else []
    forni_form = {cod: request.form.get(f'forno_{cod}', '') for cod in forni_compatibili}
    # L'operatore deve indicare il numero di cotte: nessun valore precompilato.
    cotte_form = {fid: request.form.get(f'cotte_{fid}', '') for fid in forni_id}
    mixers_form = request.form.getlist('impastatrici')
    data_form = request.form.get('data_inizio', '')
    blocco_form = request.form.get('blocco', 'M')
    if bozza_ripresa is not None:
        ancora_presenti = {f'{o.IdDocumento}-{o.IdRigaDoc}' for o in ordini}
        scelte_storiche = {f'{o.IdDocumento}-{o.IdRigaDoc}' for o in bozza_ripresa.ordini}
        scelte = sorted(scelte_storiche & ancora_presenti)
        if scelte_storiche - ancora_presenti:
            error = ('Alcuni ordini della bozza non sono più disponibili. '
                     'Rivedi la selezione: la vecchia quantità non è riservata.')
        for o in bozza_ripresa.ordini:
            forni_form[o.CodArt] = str(o.IdForno)
        cotte_form = {fid: str(f.NumeroCotte) for f in bozza_ripresa.forni
                      for fid in [f.IdForno]}
        mixers_form = [str(m.IdImpastatrice) for m in bozza_ripresa.impastatrici]
        data_form = bozza_ripresa.DataProduzione.isoformat()
        blocco_form = bozza_ripresa.BloccoIniziale
    richiesta_id = request.form.get('richiesta_id', '') if request.method == 'POST' else uuid.uuid4().hex
    if request.method == 'POST' and not error and gruppo:
        try:
            data_inizio = datetime.strptime(data_form, '%Y-%m-%d').date()
            assegnazione = {}
            for cod in {o.CodArt for o in ordini if f'{o.IdDocumento}-{o.IdRigaDoc}' in scelte}:
                assegnazione[cod] = int(forni_form[cod])
            forni_usati = set(assegnazione.values())
            cotte = {fid: int(cotte_form[fid]) for fid in forni_usati}
            esito = simula(
                gruppo_id=gruppo.IdGruppo, chiavi=scelte,
                forno_articolo=assegnazione, cotte_per_forno=cotte,
                data_inizio=data_inizio, blocco=blocco_form,
                impastatrici_ids=[int(x) for x in mixers_form],
                include_cnf=(ruolo_attuale() == "admin"),
            )
            if request.form.get('azione') == 'salva_bozza':
                id_bozza = salva_bozza(
                    esito=esito, gruppo_id=gruppo.IdGruppo,
                    data_inizio=data_inizio, blocco=blocco_form,
                    forno_articolo=assegnazione,
                    impastatrici_ids=[int(x) for x in mixers_form],
                    richiesta_id=richiesta_id, operatore=_operatore_bozza_mista(),
                    progressivo_lotto=request.form.get('lotto_progressivo') or None,
                )
                flash(f'Bozza #{id_bozza} salvata. Non occupa risorse e non è una pianificazione attiva.', 'success')
                return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
        except (ErroreAnteprimaMista, ErroreBozzaMista, ErroreCalcoloPianificazione, ValueError, KeyError) as exc:
            db.session.rollback()
            error = str(exc) or 'Controlla i dati della simulazione.'
        except IntegrityError:
            db.session.rollback()
            error = 'Il lotto o un altro vincolo è già occupato. Aggiorna la proposta e riprova.'
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Errore simulazione / salvataggio bozza impasto misto')
            error = 'Errore di calcolo o salvataggio: nessuna bozza è stata registrata.'

    return render_template(
        'ordini/anteprima_impasto_misto.j2', gruppi=gruppi, gruppo=gruppo,
        articoli=articoli, ordini=ordini, disponibili=disponibili,
        forni=forni, forni_compatibili=forni_compatibili,
        forni_id=forni_id, impastatrici=impastatrici,
        selezionati=scelte, forni_form=forni_form, cotte_form=cotte_form,
        mixers_form=mixers_form, data_form=data_form, blocco_form=blocco_form,
        errore=error, esito=esito, csrf_token=_csrf_token(), richiesta_id=richiesta_id,
    )


# --- Fase 3A: bozze persistenti, NON prenotazioni produttive. ---


def _operatore_bozza_mista() -> str:
    utente = getattr(g, 'utente', None) or getattr(g, 'user', None)
    for attributo in ('Username', 'username', 'NomeUtente', 'IdUtente', 'id'):
        dato = getattr(utente, attributo, None)
        if dato is not None and str(dato).strip():
            return str(dato).strip()[:120]
    return str(session.get('username') or ruolo_attuale() or 'operatore')[:120]


@bp_ordini_cliente.get('/pianificazione/impasto-misto/bozze', endpoint='elenco_bozze_impasto_misto')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def elenco_bozze_impasto_misto():
    mostra_archiviate = request.args.get('archiviate') == '1'
    bozze = carica_bozze(includi_archiviate=mostra_archiviate)
    ids = [b.IdBozza for b in bozze]
    piani = {p.IdBozza: p for p in db.session.scalars(
        db.select(PianificazioneMistaAttiva).where(PianificazioneMistaAttiva.IdBozza.in_(ids))
    ).all()} if ids else {}
    return render_template(
        'ordini/bozze_impasto_misto.j2',
        bozze=bozze, piani=piani,
        mostra_archiviate=mostra_archiviate,
    )


@bp_ordini_cliente.get('/pianificazione/impasto-misto/bozze/<int:id_bozza>',
                       endpoint='dettaglio_bozza_impasto_misto')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def dettaglio_bozza_impasto_misto(id_bozza: int):
    bozza = db.session.get(BozzaPianificazioneMista, id_bozza)
    if bozza is None:
        abort(404)
    forni = sorted(bozza.forni, key=lambda f: f.IdForno)
    return render_template(
        'ordini/dettaglio_bozza_impasto_misto.j2',
        bozza=bozza, forni=forni,
        piano_misto=db.session.get(PianificazioneMistaAttiva, id_bozza),
        conferma_abilitata=bool(current_app.config.get('ABILITA_CONFERMA_PIANI_MISTI', False)),
        csrf_token=_csrf_token(),
    )


@bp_ordini_cliente.post('/pianificazione/impasto-misto/bozze/<int:id_bozza>/archivia',
                        endpoint='archivia_bozza_impasto_misto')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def archivia_bozza_impasto_misto(id_bozza: int):
    _verifica_csrf()
    bozza = db.session.get(BozzaPianificazioneMista, id_bozza)
    if bozza is None:
        abort(404)
    if db.session.get(PianificazioneMistaAttiva, id_bozza) is not None:
        flash('Una bozza già confermata non può essere archiviata: usare le operazioni del piano.', 'warning')
        return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
    if bozza.Stato == 'Bozza':
        bozza.Stato = 'Archiviata'
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Errore archiviazione bozza %s', id_bozza)
            flash('Impossibile archiviare la bozza.', 'danger')
        else:
            flash('Bozza archiviata. Nessuna risorsa produttiva è stata modificata.', 'success')
    return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))


# --- Fase 3B1: attivazione atomica dei piani misti e ciclo di vita autonomo. ---
from flaskr.models.model_ordini import PianificazioneMistaAttiva
from flaskr.service.attivazione_impasto_misto_service import (
    ErroreAttivazioneMista, conferma_bozza, chiudi_piano_misto, annulla_piano_misto,
)


@bp_ordini_cliente.post('/pianificazione/impasto-misto/bozze/<int:id_bozza>/conferma',
                        endpoint='conferma_piano_misto')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def conferma_piano_misto(id_bozza: int):
    _verifica_csrf()
    # Numero di cotte dichiarato dall'operatore: la conferma produttiva
    # resta subordinata all'autorizzazione server e alla verifica fisica.
    if not current_app.config.get('ABILITA_CONFERMA_PIANI_MISTI', False):
        flash('Attivazione disabilitata: validare prima la capienza fisica delle cotte miste.', 'warning')
        return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
    if request.form.get('conferma_capienza_mista') != '1':
        flash('Conferma la verifica della capienza fisica del carico misto.', 'warning')
        return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
    try:
        conferma_bozza(id_bozza, _operatore_bozza_mista(), include_cnf=(ruolo_attuale() == 'admin'))
    except ErroreAttivazioneMista as exc:
        flash(str(exc), 'danger')
    else:
        flash('Pianificazione mista confermata: ordini, forni e impastatrici prenotati.', 'success')
    return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))


@bp_ordini_cliente.post('/pianificazione/impasto-misto/bozze/<int:id_bozza>/chiudi',
                        endpoint='chiudi_piano_misto')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def chiudi_piano_misto_route(id_bozza: int):
    _verifica_csrf()
    try:
        chiudi_piano_misto(id_bozza, _operatore_bozza_mista())
    except ErroreAttivazioneMista as exc:
        flash(str(exc), 'danger')
    else:
        flash('Pianificazione mista chiusa. Non è stato movimentato stock.', 'success')
    return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))


@bp_ordini_cliente.post('/pianificazione/impasto-misto/bozze/<int:id_bozza>/annulla',
                        endpoint='annulla_piano_misto')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def annulla_piano_misto_route(id_bozza: int):
    _verifica_csrf()
    try:
        annulla_piano_misto(id_bozza, _operatore_bozza_mista(),
                           request.form.get('motivo', ''))
    except ErroreAttivazioneMista as exc:
        flash(str(exc), 'danger')
    else:
        flash('Piano annullato: ordini e risorse nuovamente disponibili.', 'success')
    return redirect(url_for('ordini_cliente.dettaglio_bozza_impasto_misto', id_bozza=id_bozza))
