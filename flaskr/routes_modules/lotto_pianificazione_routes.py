"""Anteprima HTMX del lotto: sola lettura, nessuna prenotazione."""
from datetime import date
from flask import abort, render_template, request
from flaskr.accessi import solo_ruoli, ruolo_attuale
from flaskr.extensions import db
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.models.model_ordini import (
    BozzaPianificazioneMista, OrdiniCliente, PianificazioneProduzione,
    PianificazioneFasi, TipRicetteAnagArticoli,
)
from flaskr.models.model_lotto import LottoPianificazione
from flaskr.service.lotto_pianificazione_service import (
    ErroreLotto, lotto_corrente, prefisso_lotto, proponi_progressivo, prima_data_impasto_fasi,
)


def _prima_impasto_ricetta(cod_art: str, giorno: date, blocco: str) -> date:
    from flaskr.service.pianificazione_service import blocchi_fase, recupero_fasi_per_ricetta
    from flaskr.service.risorse_produzione_service import individua_fasi_risorse
    from types import SimpleNamespace
    righe = db.session.scalars(db.select(TipRicetteAnagArticoli).where(
        TipRicetteAnagArticoli.CodArt == cod_art
    )).all()
    if len(righe) != 1:
        raise ErroreLotto('L\'articolo non possiede una ricetta unica.')
    fasi = blocchi_fase(
        recupero_fasi_per_ricetta(SimpleNamespace(IdRicetta=righe[0].IdRicetta)),
        PianificazioneProduzione(), giorno.isoformat(), 0 if blocco == 'M' else 1,
    )
    fasi_impasto, _ = individua_fasi_risorse(fasi)
    return prima_data_impasto_fasi(fasi_impasto)


def _data_prima_fase() -> date:
    tipo = request.args.get('tipo', '').upper()
    raw = (request.args.get('data_inizio') or request.args.get('data_inizio_produzione')
           or request.args.get('nuova_data') or '')
    try:
        giorno = date.fromisoformat(raw)
    except ValueError as exc:
        raise ErroreLotto('Seleziona la data iniziale per calcolare il lotto.') from exc
    blocco = request.args.get('blocco', 'M') if tipo == 'MISTA' else (
        'M' if request.args.get('blocco_inizio_produzione', '0') == '0' else 'P'
    )
    if tipo == 'RIP':
        try:
            id_piano = int(request.args['id_piano'])
        except (ValueError, KeyError) as exc:
            raise ErroreLotto('Identificativo del piano da spostare non valido.') from exc
        piano = db.session.get(PianificazioneProduzione, id_piano)
        if piano is None or piano.StatoRigaOrdine != 'In produzione':
            raise ErroreLotto('Pianificazione inesistente o non ripianificabile.')
        from flaskr.service.risorse_produzione_service import individua_fasi_risorse
        from flaskr.service.pianificazione_service import calcola_blocchi_fase
        from types import SimpleNamespace
        righe = db.session.scalars(db.select(PianificazioneFasi).where(
            PianificazioneFasi.IdPianificazione == id_piano
        )).all()
        fasi = []
        for riga in righe:
            data_fase, indice = calcola_blocchi_fase(
                0 if piano.BloccoIniziale == 'M' else 1,
                int(riga.OffsetBlocchi), giorno,
            )
            fasi.append(SimpleNamespace(IdRicettaFase=riga.IdRicettaFase,
                IdLavorazione=riga.IdLavorazione, DataFase=data_fase,
                Blocco='M' if indice == 0 else 'P', OffsetBlocchi=riga.OffsetBlocchi))
        impasti, _ = individua_fasi_risorse(fasi)
        return prima_data_impasto_fasi(impasti)
    if tipo == 'TRAD':
        keys = request.args.getlist('ordini_selezionati')
        if not keys:
            raise ErroreLotto('Seleziona gli ordini prima di proporre un lotto.')
        try:
            documento, riga = map(int, keys[0].split('-'))
        except (TypeError, ValueError) as exc:
            raise ErroreLotto('Identificativo ordine non valido.') from exc
        ordine = db.session.get(OrdiniCliente, (documento, riga))
        if ordine is None:
            raise ErroreLotto('Ordine non trovato.')
        return _prima_impasto_ricetta(ordine.CodArt, giorno, blocco)
    if tipo == 'MISTA':
        keys = request.args.getlist('ordini')
        if keys:
            try:
                documento, riga = map(int, keys[0].split('-'))
            except ValueError as exc:
                raise ErroreLotto('Identificativo ordine non valido.') from exc
            ordine = db.session.get(OrdiniCliente, (documento, riga))
            if ordine is None:
                raise ErroreLotto('Ordine non trovato.')
            return _prima_impasto_ricetta(ordine.CodArt, giorno, blocco)
        gruppo = request.args.get('gruppo')
        if not gruppo or not gruppo.isdecimal():
            raise ErroreLotto('Seleziona almeno un ordine del gruppo.')
        from flaskr.models.model_ordini import GruppoProduzioneArticolo
        codice = db.session.scalar(db.select(GruppoProduzioneArticolo.CodArt).where(
            GruppoProduzioneArticolo.IdGruppo == int(gruppo)
        ).order_by(GruppoProduzioneArticolo.CodArt).limit(1))
        if not codice:
            raise ErroreLotto('Il gruppo non contiene articoli.')
        return _prima_impasto_ricetta(codice, giorno, blocco)
    raise ErroreLotto('Tipo di pianificazione non valido.')


@bp_ordini_cliente.get('/pianificazione/lotto/proposta', endpoint='proposta_lotto_pianificazione')
@login_required
@solo_ruoli('admin', 'utente_produzione')
def proposta_lotto_pianificazione():
    try:
        data_impasto = _data_prima_fase()
        prefisso = prefisso_lotto(data_impasto)
        numero = proponi_progressivo(data_impasto)
        errore = None
    except (ErroreLotto, ValueError) as exc:
        prefisso = None
        numero = None
        errore = str(exc)
    return render_template('ordini/_partials/_proposta_lotto_pianificazione.j2',
                           prefisso=prefisso, progressivo=numero,
                           errore=errore)


@bp_ordini_cliente.app_context_processor
def _lotto_pianificazione_contesto():
    # Le viste esistenti possono mostrare il lotto senza alterare le query ORM.
    def cerca(tipo, identificativo):
        if identificativo is None:
            return ''
        record = lotto_corrente(tipo, int(identificativo))
        return record.Lotto if record else ''
    def cerca_multipli(tipo, identificativi):
        # Una sola SELECT per tutte le righe di un elenco.
        ids = {int(i) for i in identificativi if i is not None}
        if not ids:
            return {}
        righe = db.session.scalars(
            db.select(LottoPianificazione).where(
                LottoPianificazione.TipoOwner == tipo,
                LottoPianificazione.Corrente.is_(True),
                LottoPianificazione.IdOwner.in_(ids),
            )
        ).all()
        return {r.IdOwner: r.Lotto for r in righe}

    return {
        'lotto_pianificazione': cerca,
        'lotti_pianificazioni': cerca_multipli,
    }
