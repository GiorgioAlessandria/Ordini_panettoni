"""Impasto misto nello stesso popup della pianificazione tradizionale.

Il flusso tradizionale resta invariato. Nessuna route crea testate monoforno
per un impasto multi-articolo: usa sempre le tabelle miste già esistenti.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from flask import abort, current_app, g, make_response, render_template, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flaskr.accessi import ruolo_attuale, solo_ruoli
from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaForni, AnagraficaImpastatrici, GruppoProduzione,
    GruppoProduzioneArticolo,
)
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.routes_modules.impostazioni import _csrf_token, _operatore_bozza_mista, _verifica_csrf
from flaskr.service.anteprima_impasto_misto_service import (
    ErroreAnteprimaMista, _fasi_e_ricetta, forni_per_articolo, simula,
)
from flaskr.service.attivazione_impasto_misto_service import (
    ErroreAttivazioneMista, _transazione_sqlite_immediata, conferma_bozza,
)
from flaskr.service.bozze_impasto_misto_service import ErroreBozzaMista, salva_bozza
from flaskr.service.lotto_pianificazione_service import ErroreLotto
from flaskr.service.ordini_service import (
    ErroreCalcoloRiepilogo, recupera_ordini_selezionati, recupera_quantita_impegnate,
)
from flaskr.service.pianificazione_service import ErroreCalcoloPianificazione


_FORM = 'ordini/_partials/_form_pianificazione.j2'
_ESITO = 'ordini/_partials/_simulazione_pianificazione_mista.j2'


def _errore_conferma_mista(messaggio: str):
    risposta = make_response(render_template(_ESITO, esito=None, errore=messaggio))
    risposta.headers['HX-Retarget'] = '#misto-simulazione'
    risposta.headers['HX-Reswap'] = 'innerHTML'
    return risposta


def valida_selezione_mista(chiavi: list[str]) -> dict:
    """Verifica la selezione sul server; i gruppi JS sono un aiuto visivo.

    L'appartenenza al gruppo non basta: le firme delle fasi d'impasto devono
    coincidere. Non modifica DB e non prenota progressivi.
    """
    if not 2 <= len(chiavi) <= 200 or len(set(chiavi)) != len(chiavi):
        raise ValueError('Seleziona almeno due ordini distinti (massimo 200).')
    if any(not c or '-' not in c for c in chiavi):
        raise ValueError('Identificativo ordine non valido.')
    try:
        ordini = recupera_ordini_selezionati(chiavi)
    except (ErroreCalcoloRiepilogo, ValueError, TypeError) as exc:
        raise ValueError(f'Ordini non disponibili: {exc}') from exc
    if ruolo_attuale() == 'utente_produzione':
        from flaskr.service.scadenze_giornaliere_service import articolo_cnf
        if any(articolo_cnf(o.CodArt) for o in ordini):
            abort(404)

    codici = {o.CodArt for o in ordini}
    if len(codici) < 2:
        raise ValueError('Per l’impasto misto seleziona almeno due articoli differenti.')
    assoc = db.session.execute(
        db.select(GruppoProduzioneArticolo.CodArt, GruppoProduzioneArticolo.IdGruppo)
        .where(GruppoProduzioneArticolo.CodArt.in_(codici))
    ).all()
    associazioni = {cod: gruppo for cod, gruppo in assoc}
    if len(associazioni) != len(codici) or len(set(associazioni.values())) != 1:
        raise ValueError('Gli articoli selezionati non appartengono allo stesso gruppo di produzione.')
    gruppo_id = next(iter(associazioni.values()))
    gruppo = db.session.get(GruppoProduzione, gruppo_id)
    if gruppo is None or gruppo_inattivo(gruppo):
        raise ValueError('Il gruppo di produzione non è attivo.')

    try:
        firme = {_fasi_e_ricetta(codice)[1] for codice in codici}
    except (ErroreAnteprimaMista, ErroreCalcoloPianificazione) as exc:
        raise ValueError(str(exc)) from exc
    if len(firme) != 1:
        raise ValueError('Le fasi di impasto non sono compatibili tra gli articoli selezionati.')
    impegnate = recupera_quantita_impegnate()
    disponibili = {}
    for o in ordini:
        if o.QTA_SALDO_DOC is None:
            raise ValueError(f'Quantità non disponibile per {o.IdDocumento}-{o.IdRigaDoc}.')
        residuo = int(o.QTA_SALDO_DOC) - int(impegnate.get((o.IdDocumento, o.IdRigaDoc), 0))
        if residuo <= 0:
            raise ValueError(f'L’ordine {o.IdDocumento}-{o.IdRigaDoc} non ha residuo pianificabile.')
        disponibili[(o.IdDocumento, o.IdRigaDoc)] = residuo
    return {'gruppo': gruppo, 'ordini': ordini, 'codici': codici, 'disponibili': disponibili}


def gruppo_inattivo(gruppo):
    return int(gruppo.Attivo or 0) != 1


def form_misto(chiavi: list[str]):
    """Invocato dalla route anteprima standard, nello stesso modal Bootstrap."""
    if ruolo_attuale() not in {'admin', 'utente_produzione'}:
        abort(403)
    try:
        selezione = valida_selezione_mista(chiavi)
        codici = sorted(selezione['codici'])
        articoli = {o.CodArt: o.articolo for o in selezione['ordini']}
        forni_compatibili = forni_per_articolo([articoli[c] for c in codici])
        if any(not forni_compatibili.get(c) for c in codici):
            raise ValueError('Almeno un articolo non dispone di forni compatibili.')
        forni_id = sorted({fid for v in forni_compatibili.values() for fid in v})
        forni = {f.IdForno: f for f in db.session.scalars(
            db.select(AnagraficaForni).where(AnagraficaForni.IdForno.in_(forni_id))
        ).all()}
        impastatrici = db.session.scalars(
            db.select(AnagraficaImpastatrici).order_by(AnagraficaImpastatrici.IdImpastatrice)
        ).all()
        return render_template(
            _FORM, tipo_pianificazione='mista', error=None,
            gruppo=selezione['gruppo'], chiavi=chiavi,
            ordini=selezione['ordini'], disponibili=selezione['disponibili'],
            articoli=articoli, codici=codici, forni=forni,
            forni_compatibili=forni_compatibili, forni_id=forni_id,
            impastatrici=impastatrici,
            autorizzato=bool(current_app.config.get('ABILITA_CONFERMA_PIANI_MISTI', False)),
            csrf_token=_csrf_token(), richiesta_id=uuid4().hex,
        )
    except (ValueError, ErroreAnteprimaMista, ErroreCalcoloPianificazione) as exc:
        return render_template(_FORM, tipo_pianificazione='mista', error=str(exc))


def _dati_post() -> dict:
    chiavi = request.form.getlist('ordini')
    scelta = valida_selezione_mista(chiavi)
    if request.form.get('gruppo', '') != str(scelta['gruppo'].IdGruppo):
        raise ValueError('Il gruppo produttivo indicato non corrisponde agli ordini selezionati.')
    data_inizio = date.fromisoformat(request.form['data_inizio'])
    blocco = request.form['blocco']
    if blocco not in ('M', 'P'):
        raise ValueError('Blocco iniziale non valido.')
    macchine = request.form.getlist('impastatrici')
    ids_macchine = [int(x) for x in macchine]
    if len(set(ids_macchine)) != len(ids_macchine) or any(i <= 0 for i in ids_macchine):
        raise ValueError('Impastatrici duplicate o non valide.')
    forni_articolo = {
        codice: int(request.form[f'forno_{codice}']) for codice in scelta['codici']
    }
    cotte_per_forno = {
        forno: int(request.form[f'cotte_{forno}']) for forno in set(forni_articolo.values())
    }
    # Le quantità proposte sono i residui disponibili, modificabili dall'operatore.
    # Lato server validiamo l'intero e il limite per ordine.
    quantita_per_ordine = {}
    for ordine in scelta['ordini']:
        chiave = f'{ordine.IdDocumento}-{ordine.IdRigaDoc}'
        residuo = scelta['disponibili'][(ordine.IdDocumento, ordine.IdRigaDoc)]
        raw = request.form.get(f'quantita_{ordine.IdDocumento}_{ordine.IdRigaDoc}', '')
        quantita = int(raw)
        if not (1 <= quantita <= residuo):
            raise ValueError(f'Ordine {chiave}: quantità da 1 a {residuo}.')
        quantita_per_ordine[chiave] = quantita
    return dict(
        scelta=scelta, data_inizio=data_inizio, blocco=blocco,
        ids_macchine=ids_macchine, forni_articolo=forni_articolo,
        cotte_per_forno=cotte_per_forno, quantita_per_ordine=quantita_per_ordine,
    )


def _simula(dati):
    return simula(
        gruppo_id=dati['scelta']['gruppo'].IdGruppo,
        chiavi=request.form.getlist('ordini'),
        forno_articolo=dati['forni_articolo'],
        cotte_per_forno=dati['cotte_per_forno'],
        data_inizio=dati['data_inizio'], blocco=dati['blocco'],
        impastatrici_ids=dati['ids_macchine'],
        include_cnf=(ruolo_attuale() == 'admin'),
        quantita_per_ordine=dati['quantita_per_ordine'],
    )


@bp_ordini_cliente.post('/pianificazione/mista/unificata/simulazione',
                        endpoint='simulazione_mista_unificata')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def simulazione_mista_unificata():
    _verifica_csrf()
    try:
        dati = _dati_post()
        esito = _simula(dati)
        from flaskr.service.agenda_anteprima_mista_service import eventi_proposta_mista
        nomi_forni = {f.IdForno: (f.DescrizioneForno or f.ModelloForno)
                      for f in db.session.scalars(db.select(AnagraficaForni).where(
                          AnagraficaForni.IdForno.in_(esito['risultati'])
                      )).all()}
        return render_template(_ESITO, esito=esito, errore=None,
                               agenda_mista=eventi_proposta_mista(esito), nomi_forni=nomi_forni)
    except (ValueError, KeyError, TypeError, ErroreAnteprimaMista,
            ErroreCalcoloPianificazione) as exc:
        return render_template(_ESITO, esito=None, errore=str(exc),
                               agenda_mista=[], nomi_forni={})


@bp_ordini_cliente.post('/pianificazione/mista/unificata/conferma',
                        endpoint='conferma_mista_unificata')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def conferma_mista_unificata():
    _verifica_csrf()
    # Non aggirare la protezione della conferma mista già prevista nell'app.
    if not current_app.config.get('ABILITA_CONFERMA_PIANI_MISTI', False):
        return _errore_conferma_mista(
            'Conferma mista non abilitata nella configurazione server.')
    if request.form.get('conferma_capienza_mista') != '1':
        return _errore_conferma_mista('Conferma di aver calcolato e verificato il numero di cotte per ogni forno.')
    try:
        # Blocca gli altri writer prima della rilettura di quantità e risorse.
        _transazione_sqlite_immediata()
        dati = _dati_post()
        esito = _simula(dati)
        id_bozza = salva_bozza(
            esito=esito, gruppo_id=dati['scelta']['gruppo'].IdGruppo,
            data_inizio=dati['data_inizio'], blocco=dati['blocco'],
            forno_articolo=dati['forni_articolo'],
            impastatrici_ids=dati['ids_macchine'],
            richiesta_id=request.form['richiesta_id'],
            operatore=_operatore_bozza_mista(),
            progressivo_lotto=request.form.get('lotto_progressivo') or None,
            commit=False,
        )
        conferma_bozza(
            id_bozza, _operatore_bozza_mista(),
            include_cnf=(ruolo_attuale() == 'admin'), transazione_gestita=True,
        )
        db.session.commit()
    except (ValueError, TypeError, KeyError, ErroreBozzaMista, ErroreLotto,
            ErroreAnteprimaMista, ErroreAttivazioneMista,
            ErroreCalcoloPianificazione) as exc:
        db.session.rollback()
        return _errore_conferma_mista(str(exc))
    except IntegrityError:
        db.session.rollback()
        return _errore_conferma_mista('Lotto, impastatrice o forno già occupati: aggiorna la proposta.')
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Errore database in conferma mista unificata')
        return _errore_conferma_mista('Salvataggio non riuscito: nessuna pianificazione attivata.')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Errore inatteso in conferma mista unificata')
        return _errore_conferma_mista('Errore interno: nessuna pianificazione attivata.')

    response = make_response(render_template(
        'ordini/_partials/_conferma_mista_unificata.j2', id_bozza=id_bozza
    ))
    response.headers['HX-Trigger'] = 'pianificazioneSalvata, stockAggiornato'
    return response
