"""Gestione operativa dei casi ERP non piu' presenti (ID piano e lotto immutati)."""
from flask import abort, flash, g, redirect, render_template, request, url_for
from flaskr.accessi import solo_ruoli, ruolo_attuale, verifica_csrf
from flaskr.extensions import db
from flaskr.models.model_anomalie_erp import AnomaliaOrdineERP
from flaskr.models.model_ordini import PianificazioneMistaAttiva
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.anomalie_erp_service import ErroreAnomalia, elenco_anomalie, decidi_anomalia
from flaskr.service.scadenze_giornaliere_service import articolo_cnf


def _forni_per_piani(anomalie):
    mappa={}
    for a in anomalie:
        if a.TipoPiano=='MISTA' and ('MISTA',a.IdPiano) not in mappa:
            p=db.session.get(PianificazioneMistaAttiva,a.IdPiano)
            mappa[('MISTA',a.IdPiano)]=sorted(p.bozza.forni,key=lambda f:f.IdForno) if p else []
    return mappa


def _controllo_cnf(anomalie):
    if ruolo_attuale() == 'utente_produzione' and any(articolo_cnf(a.CodArt) for a in anomalie):
        abort(404)


@bp_ordini_cliente.get('/anomalie-erp/banner', endpoint='anomalie_erp_banner')
@solo_ruoli('admin','utente_produzione')
def anomalie_erp_banner():
    righe=elenco_anomalie(pendenti_soltanto=True)
    if ruolo_attuale() == 'utente_produzione':
        righe=[a for a in righe if not articolo_cnf(a.CodArt)]
    return render_template('ordini/_partials/_anomalie_erp_banner.j2', anomalie=righe)


@bp_ordini_cliente.get('/anomalie-erp/', endpoint='anomalie_erp_elenco')
@solo_ruoli('admin','utente_produzione')
def anomalie_erp_elenco():
    righe=elenco_anomalie()
    if ruolo_attuale() == 'utente_produzione':
        righe=[a for a in righe if not articolo_cnf(a.CodArt)]
    return render_template('ordini/anomalie_erp.j2', anomalie=righe, tipo=None, id_piano=None,
                           forni_misti=_forni_per_piani(righe))


@bp_ordini_cliente.get('/anomalie-erp/<tipo>/<int:id_piano>', endpoint='anomalie_erp_piano')
@solo_ruoli('admin','utente_produzione')
def anomalie_erp_piano(tipo,id_piano):
    if tipo not in ('TRAD','MISTA'): abort(404)
    righe=elenco_anomalie(tipo=tipo,id_piano=id_piano)
    if not righe: abort(404)
    _controllo_cnf(righe)
    return render_template('ordini/anomalie_erp.j2', anomalie=righe,tipo=tipo,id_piano=id_piano,
                           forni_misti=_forni_per_piani(righe))


@bp_ordini_cliente.post('/anomalie-erp/<tipo>/<int:id_piano>/<int:doc>/<int:riga>/decisione', endpoint='anomalie_erp_decidi')
@solo_ruoli('admin','utente_produzione')
def anomalie_erp_decidi(tipo,id_piano,doc,riga):
    verifica_csrf()
    a=db.session.get(AnomaliaOrdineERP,(tipo,id_piano,doc,riga))
    if a is None: abort(404)
    _controllo_cnf([a])
    scelta=request.form.get('decisione')
    try:
        forni={}
        for nome,valore in request.form.items():
            if nome.startswith('cotte_forno_'):
                suffisso=nome[len('cotte_forno_'):]
                if not suffisso.isdecimal() or not valore.isdecimal():
                    raise ErroreAnomalia('Numero di cotte non valido: usare interi non negativi')
                forni[int(suffisso)]=int(valore)
        decidi_anomalia(tipo=tipo,id_piano=id_piano,doc=doc,riga=riga,
            decisione=scelta,motivazione=request.form.get('motivazione'),
            operatore=g.user.username,conferma=request.form.get('conferma_ricalcolo')=='1',
            cotte_per_forno=forni if tipo=='MISTA' else None)
    except ErroreAnomalia as exc:
        flash(str(exc),'danger')
    else:
        flash('Decisione registrata; controlla quantità, cotte e risorse del piano. ID e lotto invariati.','success')
    return redirect(url_for('ordini_cliente.anomalie_erp_piano',tipo=tipo,id_piano=id_piano),code=303)
