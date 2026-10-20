from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from flask import abort, current_app, g, make_response, render_template, request, flash, redirect, url_for
from sqlalchemy import tuple_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaImpastatrici,
    CapacitaForno,
    Costanti,
    LogOperazioni,
    OrdiniCliente,
    OrdiniPriorita,
    PianificazioneFasi,
    PianificazioneImpastatriceFase,
    PianificazioneOrdini,
    PianificazioneOrdiniImpastatrici,
    PianificazioneProduzione,
    PianificazioneOperazioni,
    Stock,
    AnagraficaArticoli,
)
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.ordini_service import (
    ErroreCalcoloRiepilogo,
    calcola_quantita_residua_ordine,
    calcolo_infornate,
    calcolo_riepilogo_ordini,
    dati_tabella_produzione,
    determina_tipologia_panettone,
    recupera_ordini_selezionati,
    recupera_quantita_impegnate,
    trova_capacita_forno,
)
from flaskr.service.pianificazione_service import (
    ErroreCalcoloPianificazione,
    blocchi_fase,
    calcolo_max_panettoni,
    distribuisci_quantita_ordini,
    recupera_quantita_gia_pianificate,
    recupero_fasi_per_ricetta,
    recupero_ricetta_articolo,
    validazione_linearita_offset,
)

from flaskr.service.ottimizzazione_pianificazione_service import simula_ottimizzazione
from flaskr.service.consumo_stock_service import distribuisci_stock_ordini

from flaskr.service.risorse_produzione_service import (
    individua_fasi_risorse,
    verifica_capacita_forno,
    verifica_e_distribuisci_impasto,
)


@bp_ordini_cliente.get("/stock")
@login_required
def stock_articoli():
    return render_template("ordini/stock.j2")


@bp_ordini_cliente.get("/stock/tabella")
@login_required
def stock_articoli_tabella():
    righe = db.session.execute(
        db.select(Stock.CodArt, Stock.Quantita, AnagraficaArticoli.DesArt)
        .outerjoin(AnagraficaArticoli, Stock.CodArt == AnagraficaArticoli.CodArt)
        .order_by(Stock.CodArt)
    ).all()
    return render_template("ordini/_partials/_stock_tabella.j2", righe=righe)


@bp_ordini_cliente.route("/tabella_ordini_da_produrre")
@login_required
def tabella_ordini_cliente_da_produrre():

    ordini_cliente_assegnati = dati_tabella_produzione(stato="Nuovo")

    return render_template(
        "ordini/_partials/_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
        stato_tabella="Nuovo",
    )


@bp_ordini_cliente.route("/tabella_ordini_in_produzione")
@login_required
def tabella_ordini_cliente_in_produzione():

    ordini_cliente_assegnati = dati_tabella_produzione(stato="In produzione")

    return render_template(
        "ordini/_partials/_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
        stato_tabella="In produzione",
    )


@bp_ordini_cliente.post("/ordine/<int:IdDocumento>/<int:IdRigaDoc>/priorita")
@login_required
def aggiorna_priorita_ordine(IdDocumento: int, IdRigaDoc: int):
    """Aggiorna una priorità locale; non modifica mai la riga sincronizzata ERP."""
    valore = request.form.get(f"priorita_{IdDocumento}_{IdRigaDoc}", "")
    if valore not in ("", "1", "2", "3"):
        return "Priorità non valida", 400

    ordine = db.session.get(OrdiniCliente, (IdDocumento, IdRigaDoc))
    if ordine is None:
        abort(404)

    # Le priorità riguardano esclusivamente gli ordini con residuo pianificabile.
    impegnata = recupera_quantita_impegnate().get((IdDocumento, IdRigaDoc), 0)
    if ordine.QTA_SALDO_DOC is None or ordine.QTA_SALDO_DOC - impegnata <= 0:
        return "Ordine senza quantità disponibile", 409

    try:
        prioritario = db.session.get(OrdiniPriorita, (IdDocumento, IdRigaDoc))
        if not valore:
            if prioritario is not None:
                db.session.delete(prioritario)
        elif prioritario is None:
            db.session.add(OrdiniPriorita(
                IdDocumento=IdDocumento,
                IdRigaDoc=IdRigaDoc,
                Livello=int(valore),
            ))
        else:
            prioritario.Livello = int(valore)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Errore nel salvataggio della priorità")
        return "Impossibile salvare la priorità", 500

    # Innesca il refresh del solo pannello 'Da produrre' senza sostituire la select.
    return "", 204, {"HX-Trigger": "prioritaAggiornata"}


@bp_ordini_cliente.route("/riepilogo_selezione", methods=["POST"])
@login_required
def riepilogo_selezione():
    ids_selezionati = request.form.getlist("ordini_selezionati")
    try:
        ordini = recupera_ordini_selezionati(ids_selezionati)
        riepilogo = calcolo_riepilogo_ordini(ordini)
    except ErroreCalcoloRiepilogo as errore:
        return render_template(
            "ordini/_partials/_riepilogo_ordini.j2", errore=str(errore)
        )

    return render_template(
        "ordini/_partials/_riepilogo_ordini.j2", riepilogo=riepilogo
    )


@bp_ordini_cliente.route("/dettaglio/<int:IdDocumento>/<int:IdRigaDoc>")
@login_required
def dettaglio_ordine(IdDocumento: int, IdRigaDoc: int):
    stmt_ordine = (
        db.select(OrdiniCliente)
        .options(
            joinedload(OrdiniCliente.Cliente),
            joinedload(OrdiniCliente.articolo),
            joinedload(OrdiniCliente.stato),
        )
        .where(
            OrdiniCliente.IdDocumento == IdDocumento,
            OrdiniCliente.IdRigaDoc == IdRigaDoc,
        )
    )
    ordine = db.session.execute(stmt_ordine).scalar_one_or_none()
    if not ordine:
        errore = "Ordine selezionato non esistente a programma"
        render_template("ordini/_partials/_dettaglio_ordine.j2", errore=errore)
    if ordine is None:
        abort(404)

    return render_template("ordini/_partials/_dettaglio_ordine.j2", ordine=ordine, errore=None)


def _stock_disponibile(cod_art: str) -> int:
    riga = db.session.get(Stock, cod_art)
    return max(0, int(riga.Quantita or 0)) if riga else 0


def _quantita_stock_form() -> int:
    valore = request.form.get("quantita_da_stock", "0").strip()
    try:
        quantita = int(valore)
    except (ValueError, TypeError) as errore:
        raise ErroreCalcoloPianificazione("La quantità da stock deve essere un intero") from errore
    if quantita < 0:
        raise ErroreCalcoloPianificazione("La quantità da stock non può essere negativa")
    return quantita


@bp_ordini_cliente.post("/pianificazione/anteprima")
@login_required
def anteprima_pianificazione():
    ids_selezionati = request.form.getlist("ordini_selezionati")
    template = "ordini/_partials/_form_pianificazione.j2"
    if not ids_selezionati:
        return render_template(template, error="Nessun ordine selezionato")

    try:
        ordini = recupera_ordini_selezionati(ids_selezionati)
        riepilogo = calcolo_riepilogo_ordini(ordini)
    except ErroreCalcoloRiepilogo as errore:
        return render_template(template, error=str(errore))

    stmt_impastatrici = db.select(AnagraficaImpastatrici).order_by(
        AnagraficaImpastatrici.IdImpastatrice
    )
    impastatrici = db.session.scalars(stmt_impastatrici).all()
    try:
        _, ricetta = trova_capacita_forno(ordini[0], riepilogo["tipologia_panettone"])
        fasi = recupero_fasi_per_ricetta(ricetta)
        simulazione = simula_ottimizzazione(riepilogo, fasi, ottimizza=False, pezzi_stock_utilizzati=0)
    except (ErroreCalcoloPianificazione, ErroreCalcoloRiepilogo, ValueError) as errore:
        return render_template(template, error=str(errore))
    return render_template(
        template,
        impastatrici=impastatrici,
        ordini_selezionati=ids_selezionati,
        riepilogo=riepilogo,
        simulazione=simulazione,
        stock_disponibile=_stock_disponibile(riepilogo["codart_ordine"]),
        error=None,
    )


@bp_ordini_cliente.post("/pianificazione/simulazione")
@login_required
def simulazione_pianificazione():
    """Anteprima senza effetti collaterali: la checkbox non crea stock."""
    ids_ordini = request.form.getlist("ordini_selezionati")
    ottimizza = request.form.get("ottimizza_infornate") == "1"
    template = "ordini/_partials/_anteprima_ottimizzazione.j2"
    try:
        ordini = recupera_ordini_selezionati(ids_ordini)
        riepilogo = calcolo_riepilogo_ordini(ordini)
        _, ricetta = trova_capacita_forno(ordini[0], riepilogo["tipologia_panettone"])
        fasi = recupero_fasi_per_ricetta(ricetta)
        stock_disponibile = _stock_disponibile(riepilogo["codart_ordine"])
        stock_da_usare = _quantita_stock_form()
        if stock_da_usare > stock_disponibile:
            raise ErroreCalcoloPianificazione("Lo stock disponibile è diminuito: aggiornare la quantità")
        simulazione = simula_ottimizzazione(riepilogo, fasi, ottimizza, stock_da_usare)
        return render_template(template, riepilogo=riepilogo, simulazione=simulazione,
                               stock_disponibile=stock_disponibile, error=None)
    except (ErroreCalcoloPianificazione, ErroreCalcoloRiepilogo, ValueError) as errore:
        return render_template(template, error=str(errore))


@bp_ordini_cliente.post("/pianificazione/conferma")
@login_required
def conferma_pianificazione():
    template = "ordini/_partials/_conferma_pianificazione.j2"

    # FASE 1 - Validazione dei dati inviati dal form.
    data_input = request.form.get("data_inizio_produzione")
    blocco_input = request.form.get("blocco_inizio_produzione")
    ottimizza_infornate = request.form.get("ottimizza_infornate") == "1"
    ids_ordini = request.form.getlist("ordini_selezionati")
    ids_impastatrici_form = request.form.getlist("impastatrici_selezionate")
    try:
        stock_da_usare = _quantita_stock_form()
    except ErroreCalcoloPianificazione as errore:
        return render_template(template, error=str(errore))

    if blocco_input not in ("0", "1"):
        return render_template(template, error="Seleziona un blocco iniziale valido")
    blocco_iniziale = int(blocco_input)
    blocco_iniziale_db = "M" if blocco_iniziale == 0 else "P"

    if not data_input:
        return render_template(template, error="Seleziona una data di produzione")
    try:
        data_produzione = date.fromisoformat(data_input)
    except ValueError:
        return render_template(template, error="La data inserita non è valida")

    if not ids_ordini:
        return render_template(template, error="Nessun ordine selezionato")
    if not ids_impastatrici_form:
        return render_template(template, error="Nessuna impastatrice selezionata")

    try:
        ids_impastatrici = {int(valore) for valore in ids_impastatrici_form}
    except ValueError:
        return render_template(template, error="ID impastatrice non valido")
    if any(id_imp <= 0 for id_imp in ids_impastatrici):
        return render_template(template, error="ID impastatrice non valido")

    try:
        # Serializza lettura della disponibilità e scrittura su SQLite.
        # Il file SQLite risiede su un percorso di rete: verificare che il server
        # supporti realmente i lock SQLite attraverso SMB.
        if db.engine.dialect.name == "sqlite":
            db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        # FASE 2 - Recupero impastatrici e ordini selezionati.
        stmt_impastatrici = db.select(AnagraficaImpastatrici).where(
            AnagraficaImpastatrici.IdImpastatrice.in_(ids_impastatrici)
        )
        list_impastatrici = db.session.scalars(stmt_impastatrici).all()
        if {imp.IdImpastatrice for imp in list_impastatrici} != ids_impastatrici:
            raise ErroreCalcoloPianificazione("Una o più impastatrici non sono state trovate")

        ordini_selezionati = recupera_ordini_selezionati(ids_ordini)
        chiavi_ordini = [(ordine.IdDocumento, ordine.IdRigaDoc) for ordine in ordini_selezionati]
        if len(chiavi_ordini) != len(set(chiavi_ordini)):
            raise ErroreCalcoloRiepilogo("Sono presenti ordini selezionati più volte")

        # La scelta è manuale; tra gli ordini scelti prevale la consegna vicina.
        ordini_selezionati.sort(
            key=lambda ordine: (
                ordine.DataConsegna,
                ordine.IdDocumento,
                ordine.IdRigaDoc,
            )
        )

        # Verifica che tutti gli ordini siano dello stesso articolo/formato.
        risultato = calcolo_riepilogo_ordini(ordini_selezionati)

        # FASE 3 - Calcolo del residuo. Non toccare QTA_SALDO_DOC.
        # Questa funzione conta soltanto le righe "In produzione" e "Chiuso".
        quantita_impegnate = recupera_quantita_impegnate()
        ordini_disponibili = []
        for ordine in ordini_selezionati:
            chiave = (ordine.IdDocumento, ordine.IdRigaDoc)
            if ordine.QTA_SALDO_DOC is None:
                raise ErroreCalcoloRiepilogo(
                    f"Saldo mancante per l'ordine {ordine.IdDocumento}-{ordine.IdRigaDoc}"
                )

            gia_impegnata = quantita_impegnate.get(chiave, 0)
            disponibile = ordine.QTA_SALDO_DOC - gia_impegnata
            if disponibile > 0:
                ordini_disponibili.append((ordine, disponibile))

        panettoni_previsti = sum(qta for _, qta in ordini_disponibili)
        if panettoni_previsti <= 0:
            raise ErroreCalcoloPianificazione("Nessun panettone residuo da pianificare")

        # FASE 4 - Ottimizzazione e calcolo peso condivisi con l'anteprima HTMX.
        # Il riepilogo applica i coefficienti configurati; il surplus usa lo stesso
        # peso unitario, senza scorciatoie basate sulla sola prezzatura articolo.
        forno, ricetta = trova_capacita_forno(
            ordini_disponibili[0][0], risultato["tipologia_panettone"]
        )
        fasi_ricetta = recupero_fasi_per_ricetta(ricetta)
        disponibilita_stock = _stock_disponibile(risultato["codart_ordine"])
        if stock_da_usare > disponibilita_stock:
            raise ErroreCalcoloPianificazione(
                f"Stock insufficiente per {risultato['codart_ordine']}: "
                f"disponibili {disponibilita_stock}, richiesti {stock_da_usare}"
            )
        simulazione = simula_ottimizzazione(risultato, fasi_ricetta,
                                           ottimizza_infornate, stock_da_usare)
        panettoni_infornata = simulazione.pezzi_produzione
        panettoni_residui = 0
        panettoni_surplus = simulazione.pezzi_stock
        numero_cotte = simulazione.numero_cotte
        peso_unitario = simulazione.peso_unitario_grammi
        peso_totale_pianificato = simulazione.peso_totale_grammi

        # FASE 5 - Distribuzione, prima di qualsiasi INSERT.
        capacita_assegnabile = panettoni_previsti
        assegnazioni = distribuisci_stock_ordini(ordini_disponibili, stock_da_usare)
        totale_assegnato = sum(qta for _, qta, _ in assegnazioni)
        if not assegnazioni or (
            totale_assegnato != capacita_assegnabile
            or panettoni_residui != panettoni_previsti - totale_assegnato
            or panettoni_surplus != panettoni_infornata - (totale_assegnato - stock_da_usare)
        ):
            raise ErroreCalcoloPianificazione("Incoerenza nelle quantità della pianificazione")

        # FASE 6 - Registrazione atomica della pianificazione.
        pianificazione = PianificazioneProduzione(
            DataProduzione=data_produzione,
            IdForno=forno.IdForno,
            TipologiaPanettone=risultato["tipologia_panettone"],
            StatoRigaOrdine="In produzione",  # Stato generale del programma.
            QtaStockGenerata=panettoni_surplus if ottimizza_infornate else 0,
            PesoTotale=peso_totale_pianificato,
            NumeroCotte=numero_cotte,
            PanettoniPianificati=panettoni_infornata,
            CodArt=risultato["codart_ordine"],
            BloccoIniziale=blocco_iniziale_db,
            IdRicetta=ricetta.IdRicetta,
        )
        db.session.add(pianificazione)
        db.session.flush()  # Genera IdPianificazione senza fare commit.

        fasi = blocchi_fase(
            fasi_ricetta,
            pianificazione,
            data_produzione.isoformat(),
            blocco_iniziale,
        )
        if not validazione_linearita_offset(fasi):
            raise ErroreCalcoloPianificazione("Offset delle fasi non validi")

        # La concorrenza NON è più vietata per IdLavorazione.
        # Verifica la disponibilità delle risorse sulle fasi effettive
        # (che possono avere offset rispetto all'inizio della pianificazione).
        (fase_impasto, lavoro_impasto), (fase_forno, lavoro_forno, ricetta_fase_forno) = (
            individua_fasi_risorse(fasi)
        )
        assegnazioni_macchine, blocchi_impasto = verifica_e_distribuisci_impasto(
            impastatrici=list_impastatrici,
            peso_totale_grammi=peso_totale_pianificato,
            fase=fase_impasto,
            lavorazione=lavoro_impasto,
        )
        verifica_capacita_forno(
            fase_forno=fase_forno,
            lavorazione_forno=lavoro_forno,
            ricetta_fase_forno=ricetta_fase_forno,
            id_forno=forno.IdForno,
            capienza_cotta=int(forno.Capienza),
            numero_cotte=numero_cotte,
            peso_totale_grammi=peso_totale_pianificato,
            peso_unitario_grammi=peso_unitario,
        )

        db.session.add_all(fasi)
        db.session.flush()  # Servono gli ID delle fasi per le occupazioni.
        for id_macchina, kg in assegnazioni_macchine.items():
            # Mantiene l'associazione legacy utilizzata dal calendario.
            db.session.add(PianificazioneOrdiniImpastatrici(
                IdPianificazione=pianificazione.IdPianificazione,
                IdImpastatrice=id_macchina,
            ))
            for giorno, blocco in blocchi_impasto:
                db.session.add(PianificazioneImpastatriceFase(
                    IdPianificazioneFase=fase_impasto.IdPianificazioneFase,
                    IdImpastatrice=id_macchina,
                    DataFase=giorno,
                    Blocco=blocco,
                    # Peso dell'intero impasto assegnato alla macchina;
                    # se la fase dura più blocchi, ripetuto solo ai fini
                    # informativi (NON sommare sulle righe temporali).
                    KgAssegnati=kg,
                ))

        ora_log = datetime.now()
        for ordine, quantita_assegnata, quantita_da_stock in assegnazioni:
            db.session.add(
                PianificazioneOrdini(
                    IdPianificazione=pianificazione.IdPianificazione,
                    IdDocumento=ordine.IdDocumento,
                    IdRiga=ordine.IdRigaDoc,
                    QtaPianificata=quantita_assegnata,
                    QtaDaStock=quantita_da_stock,
                    Stato="In produzione",
                )
            )
            # Non modificare ordine.stato.Stato: l'eventuale residuo resta Nuovo.
            db.session.add(
                LogOperazioni(
                    IdDocumento=ordine.IdDocumento,
                    IdRigaDoc=ordine.IdRigaDoc,
                    Stato="In produzione",
                    Operatore=g.user.username,
                    synced_at=ora_log,
                )
            )

        # Consumo stock realmente disponibile. Il controllo nel WHERE protegge
        # anche da modifiche intervenute durante la conferma.
        if stock_da_usare:
            sottrazione = db.session.execute(
                update(Stock)
                .where(Stock.CodArt == risultato["codart_ordine"],
                       Stock.Quantita >= stock_da_usare)
                .values(Quantita=Stock.Quantita - stock_da_usare)
                .execution_options(synchronize_session=False)
            )
            if sottrazione.rowcount != 1:
                raise ErroreCalcoloPianificazione("Stock insufficiente al momento del salvataggio")
            # Evita valori ORM vecchi in caso di accredito dello stesso codice.
            db.session.expire_all()

        # Registra esclusivamente il surplus generato dalla scelta di ottimizzare.
        # Non accreditare qui i pezzi assegnati agli ordini, già tracciati sopra.
        # Eseguito prima del commit: eventuali errori annullano stock + piano.
        if ottimizza_infornate and panettoni_surplus > 0:
            articolo_stock = risultato["codart_ordine"]
            riga_stock = db.session.get(Stock, articolo_stock)
            if riga_stock is None:
                db.session.add(Stock(CodArt=articolo_stock, Quantita=panettoni_surplus))
            else:
                riga_stock.Quantita = int(riga_stock.Quantita or 0) + panettoni_surplus

        id_pianificazione = pianificazione.IdPianificazione
        db.session.commit()

    except (ErroreCalcoloPianificazione, ErroreCalcoloRiepilogo, ValueError) as errore:
        db.session.rollback()
        return render_template(template, error=str(errore))

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Errore nel salvataggio della pianificazione")
        return render_template(template, error="Errore durante il salvataggio della pianificazione")

    response = make_response(
        render_template(
            template, id_pianificazione=id_pianificazione,
            qta_stock_generata=panettoni_surplus if ottimizza_infornate else 0,
            qta_stock_utilizzata=stock_da_usare, error=None
        )
    )
    response.headers["HX-Trigger"] = "pianificazioneSalvata, stockAggiornato"
    return response


# --- Ciclo di vita della pianificazione ---
from flaskr.service.ciclo_vita_pianificazione_service import (
    ErroreCicloVita, chiudi_pianificazione, annulla_pianificazione
)


@bp_ordini_cliente.get("/pianificazioni/chiuse")
@login_required
def pianificazioni_chiuse():
    piani = db.session.scalars(
        db.select(PianificazioneProduzione)
        .where(PianificazioneProduzione.StatoRigaOrdine.in_(("Chiuso", "Annullato")))
        .order_by(PianificazioneProduzione.IdPianificazione.desc())
    ).all()
    return render_template("ordini/pianificazioni_storico.j2", piani=piani)


@bp_ordini_cliente.get("/pianificazioni/<int:IdPianificazione>/gestione")
@login_required
def gestione_pianificazione(IdPianificazione: int):
    piano = db.session.get(PianificazioneProduzione, IdPianificazione)
    if piano is None:
        abort(404)
    assoc = db.session.execute(
        db.select(PianificazioneOrdini, OrdiniCliente)
        .join(OrdiniCliente,
              (PianificazioneOrdini.IdDocumento == OrdiniCliente.IdDocumento) &
              (PianificazioneOrdini.IdRiga == OrdiniCliente.IdRigaDoc))
        .options(joinedload(OrdiniCliente.Cliente))
        .where(PianificazioneOrdini.IdPianificazione == IdPianificazione)
        .order_by(OrdiniCliente.DataConsegna)
    ).all()
    log = db.session.scalars(
        db.select(PianificazioneOperazioni)
        .where(PianificazioneOperazioni.IdPianificazione == IdPianificazione)
        .order_by(PianificazioneOperazioni.Id.desc())
    ).all()
    return render_template("ordini/gestione_pianificazione.j2", piano=piano,
                           associazioni=assoc, registro=log,
                           totale_da_stock=sum(int(a.QtaDaStock or 0) for a, _ in assoc))


@bp_ordini_cliente.post("/pianificazioni/<int:IdPianificazione>/chiudi")
@login_required
def conferma_chiusura_pianificazione(IdPianificazione: int):
    try:
        chiudi_pianificazione(IdPianificazione, g.user.username)
    except ErroreCicloVita as errore:
        flash(str(errore), "danger")
        return redirect(url_for("ordini_cliente.gestione_pianificazione", IdPianificazione=IdPianificazione), code=303)
    flash(f"Pianificazione #{IdPianificazione} chiusa correttamente.", "success")
    return redirect(url_for("ordini_cliente.pianificazioni_chiuse"), code=303)


@bp_ordini_cliente.post("/pianificazioni/<int:IdPianificazione>/annulla")
@login_required
def conferma_annullamento_pianificazione(IdPianificazione: int):
    motivo = (request.form.get("motivo_annullamento") or "").strip()
    try:
        annulla_pianificazione(IdPianificazione, g.user.username, motivo)
    except ErroreCicloVita as errore:
        flash(str(errore), "danger")
        return redirect(url_for("ordini_cliente.gestione_pianificazione", IdPianificazione=IdPianificazione), code=303)
    flash(f"Pianificazione #{IdPianificazione} annullata. Quantità e risorse ripristinate.", "success")
    return redirect(url_for("ordini_cliente.pianificazioni_chiuse"), code=303)
