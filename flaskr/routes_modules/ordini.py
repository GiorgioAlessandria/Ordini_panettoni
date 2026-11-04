from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from flask import abort, current_app, g, make_response, render_template, request, flash, redirect, url_for
from sqlalchemy import tuple_, update
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.service.consuntivi_service import leggi_form_consuntivo, ErroreConsuntivo
from flaskr.models.model_consuntivi import ConsuntivoProduzioneOrdine, ConsuntivoProduzioneArticolo, GiacenzaSecondaScelta
from flaskr.service.lotto_pianificazione_service import (
    ErroreLotto, assegna_lotto, prima_data_impasto_fasi,
)
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
from flaskr.accessi import ruolo_attuale
from flaskr.service.scadenze_giornaliere_service import articolo_cnf
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
from flaskr.service.agenda_pianificazione_service import carica_agenda_giorni_interessati

from flaskr.service.risorse_produzione_service import (
    capacita_impastatrici_pezzi,
    impastatrici_libere,
    disponibilita_forno,
    individua_fasi_risorse,
    verifica_capacita_forno,
    verifica_e_distribuisci_impasti,
)


def _proteggi_ordini_cnf(ordini):
    """Le richieste POST manuali non possono aggirare il filtro del ruolo produzione."""
    if ruolo_attuale() == "utente_produzione" and any(
        articolo_cnf(o.CodArt) for o in ordini
    ):
        abort(404, description="Ordine non disponibile per questo profilo")


def _proteggi_piano_cnf(id_pianificazione):
    if ruolo_attuale() == "utente_produzione":
        piano = db.session.get(PianificazioneProduzione, id_pianificazione)
        if piano is not None and articolo_cnf(piano.CodArt):
            abort(404, description="Pianificazione non disponibile per questo profilo")



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
    seconde = {
        cod: qta for cod, qta in db.session.execute(
            db.select(GiacenzaSecondaScelta.CodArt, GiacenzaSecondaScelta.Quantita)
        )
    }
    righe_stock = [dict(CodArt=r.CodArt, Quantita=int(r.Quantita or 0),
                        DesArt=r.DesArt) for r in righe]
    codici_prima = {r["CodArt"] for r in righe_stock}
    # Mostra anche articoli presenti SOLO nella seconda scelta.
    for cod in sorted(set(seconde) - codici_prima):
        articolo = db.session.get(AnagraficaArticoli, cod)
        righe_stock.append(dict(CodArt=cod, Quantita=0,
                                DesArt=articolo.DesArt if articolo else None))
    righe_stock.sort(key=lambda r: r["CodArt"])
    return render_template("ordini/_partials/_stock_tabella.j2",
                           righe=righe_stock, seconde=seconde)


@bp_ordini_cliente.route("/tabella_ordini_da_produrre")
@login_required
def tabella_ordini_cliente_da_produrre():
    # Vendite: un unico elenco di ordini, indipendente dallo stato della pianificazione.
    if ruolo_attuale() == "utente_vendite":
        from flaskr.service.ordini_vendite_service import righe_ordini_vendite

        return render_template(
            "ordini/_partials/_tabella_ordini_vendite.j2",
            ordini_cliente=righe_ordini_vendite(),
        )


    ordini_cliente_assegnati = dati_tabella_produzione(stato="Nuovo")
    if ruolo_attuale() == "utente_produzione":
        ordini_cliente_assegnati = [r for r in ordini_cliente_assegnati if not articolo_cnf(r.get("CodArt"))]

    # Colori/checkbox per SOTTOGRUPPO di impasto effettivamente compatibile.
    # Appartenenza commerciale allo stesso gruppo non è sufficiente: le firme
    # delle fasi di impasto devono coincidere. Il POST rivalida tutto.
    from flaskr.service.compatibilita_visuale_misti_service import gruppi_compatibili_visibili
    gruppi_per_articolo = gruppi_compatibili_visibili(
        {r["CodArt"] for r in ordini_cliente_assegnati}
    )
    for riga in ordini_cliente_assegnati:
        id_gruppo, nome_gruppo, firma = gruppi_per_articolo.get(
            riga["CodArt"], (None, "", "")
        )
        riga["IdGruppoImpasto"] = id_gruppo
        riga["NomeGruppoImpasto"] = nome_gruppo
        riga["CompatibilitaImpasto"] = firma

    return render_template(
        "ordini/_partials/_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
        stato_tabella="Nuovo",
    )


@bp_ordini_cliente.route("/tabella_ordini_in_produzione")
@login_required
def tabella_ordini_cliente_in_produzione():

    ordini_cliente_assegnati = dati_tabella_produzione(stato="In produzione")
    if ruolo_attuale() == "utente_produzione":
        ordini_cliente_assegnati = [r for r in ordini_cliente_assegnati if not articolo_cnf(r.get("CodArt"))]

    from flaskr.service.elenco_pianificazioni_unificato_service import righe_pianificazioni_unificate
    piani_unificati = righe_pianificazioni_unificate(
        ordini_cliente_assegnati,
        nascondi_cnf=(ruolo_attuale() == "utente_produzione"),
    )
    return render_template(
        "ordini/_partials/_pianificazioni_integrate.j2",
        ordini_cliente=ordini_cliente_assegnati,
        piani_unificati=piani_unificati,
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
    if ordine is None or (ruolo_attuale() == "utente_produzione" and articolo_cnf(ordine.CodArt)):
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
    if request.form.get("tipo_pianificazione") == "mista":
        from flaskr.routes_modules.pianificazione_mista_unificata import valida_selezione_mista
        try:
            risultato = valida_selezione_mista(ids_selezionati)
            riepilogo_misto = {
                "gruppo": risultato["gruppo"].Nome,
                "numero_ordini": len(risultato["ordini"]),
                "numero_articoli": len(risultato["codici"]),
                "quantita": sum(risultato["disponibili"].values()),
            }
            return render_template("ordini/_partials/_riepilogo_ordini.j2",
                                   riepilogo_misto=riepilogo_misto)
        except ValueError as errore:
            return render_template("ordini/_partials/_riepilogo_ordini.j2",
                                   errore=str(errore))
    try:
        ordini = recupera_ordini_selezionati(ids_selezionati)
        _proteggi_ordini_cnf(ordini)
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
    if ordine is None or (ruolo_attuale() == "utente_produzione" and articolo_cnf(ordine.CodArt)):
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


def _simula_pianificazione_su_blocchi(
    riepilogo,
    fasi_ricetta,
    data_produzione: date,
    blocco_iniziale: int,
    impastatrici,
    ottimizza: bool,
    stock_da_usare: int,
):
    """Simulazione senza INSERT, con le stesse occupazioni usate nella conferma."""
    piano_temporaneo = PianificazioneProduzione()
    fasi = blocchi_fase(
        fasi_ricetta, piano_temporaneo, data_produzione.isoformat(), blocco_iniziale
    )
    if not validazione_linearita_offset(fasi):
        raise ErroreCalcoloPianificazione("Offset delle fasi non validi")
    fasi_impasto, (fase_forno, lavoro_forno, ricetta_fase_forno) = (
        individua_fasi_risorse(fasi)
    )
    cotte_libere, _ = disponibilita_forno(
        fase_forno, lavoro_forno, ricetta_fase_forno, riepilogo["id_forno"]
    )
    qta_ordini = int(riepilogo["qta_panettoni"])
    if qta_ordini <= 0:
        raise ErroreCalcoloPianificazione("Nessun panettone residuo da pianificare")
    peso_unitario = Decimal(str(riepilogo["peso_totale_grammi"])) / Decimal(qta_ordini)
    pezzi_impastatrici = capacita_impastatrici_pezzi(
        impastatrici, fasi_impasto, peso_unitario
    )
    simulazione = simula_ottimizzazione(
        riepilogo,
        fasi_ricetta,
        ottimizza,
        stock_da_usare,
        cotte_libere=cotte_libere,
        capacita_impastatrici_pezzi=pezzi_impastatrici,
    )
    return fasi, simulazione


def _impastatrici_disponibili_su_blocchi(
    fasi_ricetta,
    data_produzione: date,
    blocco_iniziale: int,
    impastatrici,
) -> set[int]:
    """Macchine libere per OGNI fase di impasto e per tutti i blocchi occupati.

    Riutilizza il controllo della conferma (anche per durata > 1 e piani legacy).
    Nessun inserimento nel database.
    """
    piano_temporaneo = PianificazioneProduzione()
    fasi = blocchi_fase(
        fasi_ricetta, piano_temporaneo, data_produzione.isoformat(), blocco_iniziale
    )
    if not validazione_linearita_offset(fasi):
        raise ErroreCalcoloPianificazione("Offset delle fasi non validi")
    fasi_impasto, _ = individua_fasi_risorse(fasi)
    disponibili, _ = impastatrici_libere(impastatrici, fasi_impasto)
    return set(disponibili)


@bp_ordini_cliente.post("/pianificazione/anteprima")
@login_required
def anteprima_pianificazione():
    ids_selezionati = request.form.getlist("ordini_selezionati")
    if request.form.get("tipo_pianificazione") == "mista":
        from flaskr.routes_modules.pianificazione_mista_unificata import form_misto
        return form_misto(ids_selezionati)
    template = "ordini/_partials/_form_pianificazione.j2"
    if not ids_selezionati:
        return render_template(template, error="Nessun ordine selezionato")

    try:
        ordini = recupera_ordini_selezionati(ids_selezionati)
        _proteggi_ordini_cnf(ordini)
        riepilogo = calcolo_riepilogo_ordini(ordini)
    except ErroreCalcoloRiepilogo as errore:
        return render_template(template, error=str(errore))

    stmt_impastatrici = db.select(AnagraficaImpastatrici).order_by(
        AnagraficaImpastatrici.IdImpastatrice
    )
    impastatrici = db.session.scalars(stmt_impastatrici).all()
    return render_template(
        template,
        impastatrici=impastatrici,
        ids_impastatrici_disponibili=set(),
        ids_impastatrici_selezionate=set(),
        impastatrici_calcolate=False,
        agenda=None,
        ordini_selezionati=ids_selezionati,
        riepilogo=riepilogo,
        simulazione=None,
        messaggio="Seleziona data, blocco e impastatrici per calcolare la capacità disponibile.",
        stock_disponibile=_stock_disponibile(riepilogo["codart_ordine"]),
        error=None,
    )


@bp_ordini_cliente.post("/pianificazione/simulazione")
@login_required
def simulazione_pianificazione():
    """Aggiorna simulazione e check delle impastatrici con HTMX/OOB.

    Una macchina è selezionabile solo quando risulta libera in *tutte* le
    fasi di impasto della ricetta, comprese le fasi di durata > 1 blocco.
    """
    template = "ordini/_partials/_simulazione_e_impastatrici.j2"
    impastatrici = db.session.scalars(
        db.select(AnagraficaImpastatrici).order_by(
            AnagraficaImpastatrici.IdImpastatrice
        )
    ).all()
    context = dict(
        impastatrici=impastatrici,
        ids_impastatrici_disponibili=set(),
        ids_impastatrici_selezionate=set(),
        impastatrici_calcolate=False,
        agenda=None,
        simulazione=None,
        error=None,
    )
    try:
        ordini = recupera_ordini_selezionati(request.form.getlist("ordini_selezionati"))
        _proteggi_ordini_cnf(ordini)
        riepilogo = calcolo_riepilogo_ordini(ordini)
        stock_disponibile = _stock_disponibile(riepilogo["codart_ordine"])
        stock_da_usare = _quantita_stock_form()
        if stock_da_usare > stock_disponibile:
            raise ErroreCalcoloPianificazione(
                "Lo stock disponibile è diminuito: aggiornare la quantità"
            )
        context.update(riepilogo=riepilogo, stock_disponibile=stock_disponibile)

        data_input = request.form.get("data_inizio_produzione")
        blocco_input = request.form.get("blocco_inizio_produzione")
        if not data_input or blocco_input not in ("0", "1"):
            context["messaggio"] = (
                "Seleziona data e blocco per conoscere le impastatrici libere."
            )
            return render_template(template, **context)

        data_produzione = date.fromisoformat(data_input)
        _, ricetta = trova_capacita_forno(ordini[0], riepilogo["tipologia_panettone"])
        fasi = recupero_fasi_per_ricetta(ricetta)
        # La bozza viene disegnata anche senza impastatrici selezionate:
        # serve a scegliere data/blocco valutando gli eventi già presenti.
        fasi_proposte = blocchi_fase(
            fasi, PianificazioneProduzione(), data_produzione.isoformat(), int(blocco_input)
        )
        if not validazione_linearita_offset(fasi_proposte):
            raise ErroreCalcoloPianificazione("Offset delle fasi non validi")
        context["agenda"] = carica_agenda_giorni_interessati(
            fasi_proposte, fasi, riepilogo["codart_ordine"],
            mostra_articoli_cnf=(ruolo_attuale() != "utente_produzione"),
        )
        disponibili = _impastatrici_disponibili_su_blocchi(
            fasi, data_produzione, int(blocco_input), impastatrici
        )
        context.update(
            ids_impastatrici_disponibili=disponibili,
            impastatrici_calcolate=True,
        )

        ids_richiesti = {int(value) for value in request.form.getlist("impastatrici_selezionate")}
        ids_esistenti = {imp.IdImpastatrice for imp in impastatrici}
        if any(i <= 0 for i in ids_richiesti) or not ids_richiesti.issubset(ids_esistenti):
            raise ErroreCalcoloPianificazione("Una o più impastatrici non sono valide")
        # Quando la data o il blocco cambiano, deseleziona quelle ora occupate.
        ids_selezionati = ids_richiesti & disponibili
        context["ids_impastatrici_selezionate"] = ids_selezionati
        if not ids_selezionati:
            context["messaggio"] = (
                "Non ci sono impastatrici libere nei blocchi delle fasi di impasto."
                if not disponibili
                else "Seleziona almeno una delle impastatrici disponibili per la simulazione."
            )
            return render_template(template, **context)

        impastatrici_scelte = [
            imp for imp in impastatrici if imp.IdImpastatrice in ids_selezionati
        ]
        _, simulazione = _simula_pianificazione_su_blocchi(
            riepilogo, fasi, data_produzione, int(blocco_input),
            impastatrici_scelte,
            request.form.get("ottimizza_infornate") == "1", stock_da_usare,
        )
        context["simulazione"] = simulazione
        return render_template(template, **context)
    except (ErroreCalcoloPianificazione, ErroreCalcoloRiepilogo, ValueError) as errore:
        context["error"] = str(errore)
        return render_template(template, **context)


def _errore_conferma_pianificazione(messaggio: str):
    """Mantiene il form aperto: con HTMX aggiorna soltanto il messaggio nel footer."""
    if request.headers.get("HX-Request", "").lower() != "true":
        # Fallback per richieste non HTMX: mantiene il comportamento precedente.
        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2", error=messaggio
        )
    risposta = make_response(render_template(
        "ordini/_partials/_errore_conferma_pianificazione.j2", error=messaggio
    ))
    risposta.headers["HX-Retarget"] = "#errore-pianificazione"
    risposta.headers["HX-Reswap"] = "innerHTML"
    return risposta


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
        return _errore_conferma_pianificazione(str(errore))

    if blocco_input not in ("0", "1"):
        return _errore_conferma_pianificazione("Seleziona un blocco iniziale valido")
    blocco_iniziale = int(blocco_input)
    blocco_iniziale_db = "M" if blocco_iniziale == 0 else "P"

    if not data_input:
        return _errore_conferma_pianificazione("Seleziona una data di produzione")
    try:
        data_produzione = date.fromisoformat(data_input)
    except ValueError:
        return _errore_conferma_pianificazione("La data inserita non è valida")

    if not ids_ordini:
        return _errore_conferma_pianificazione("Nessun ordine selezionato")
    if not ids_impastatrici_form:
        return _errore_conferma_pianificazione("Nessuna impastatrice selezionata")

    try:
        ids_impastatrici = {int(valore) for valore in ids_impastatrici_form}
    except ValueError:
        return _errore_conferma_pianificazione("ID impastatrice non valido")
    if any(id_imp <= 0 for id_imp in ids_impastatrici):
        return _errore_conferma_pianificazione("ID impastatrice non valido")

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
        _proteggi_ordini_cnf(ordini_selezionati)
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
        # Capacità effettiva nel blocco: min(forno libero, impastatrici libere).
        # La simulazione tiene conto anche dei pezzi eventualmente prelevati da stock.
        fasi, simulazione = _simula_pianificazione_su_blocchi(
            risultato, fasi_ricetta, data_produzione, blocco_iniziale,
            list_impastatrici, ottimizza_infornate, stock_da_usare,
        )
        # Non accettare un ID inviato manualmente o diventato occupato dopo
        # l'anteprima: ogni macchina deve essere libera in TUTTE le fasi.
        fasi_impasto, _ = individua_fasi_risorse(fasi)
        libere, _ = impastatrici_libere(list_impastatrici, fasi_impasto)
        if set(libere) != ids_impastatrici:
            raise ErroreCalcoloPianificazione(
                "Una o più impastatrici selezionate sono occupate in almeno "
                "un blocco di impasto. Aggiorna data, blocco o selezione."
            )
        panettoni_infornata = simulazione.pezzi_produzione
        panettoni_residui = simulazione.pezzi_residui
        panettoni_surplus = simulazione.pezzi_stock
        numero_cotte = simulazione.numero_cotte
        peso_unitario = simulazione.peso_unitario_grammi
        peso_totale_pianificato = simulazione.peso_totale_grammi

        # FASE 5 - Distribuzione, prima di qualsiasi INSERT.
        capacita_assegnabile = simulazione.pezzi_assegnati
        assegnazioni = distribuisci_stock_ordini(
            ordini_disponibili, stock_da_usare, capacita_assegnabile
        )
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
            QtaStockGenerata=0,  # stock fisico solo alla chiusura
            PesoTotale=peso_totale_pianificato,
            NumeroCotte=numero_cotte,
            PanettoniPianificati=panettoni_infornata,
            CodArt=risultato["codart_ordine"],
            BloccoIniziale=blocco_iniziale_db,
            IdRicetta=ricetta.IdRicetta,
        )
        db.session.add(pianificazione)
        db.session.flush()  # Genera IdPianificazione senza fare commit.

        # Assegna il lotto nella medesima transazione della pianificazione.
        # La data viene dalle fasi effettive di impasto, non dalla data del form.
        fasi_impasto_per_lotto, _ = individua_fasi_risorse(fasi)
        try:
            assegna_lotto(
                tipo="TRAD",
                id_owner=pianificazione.IdPianificazione,
                data_impasto=prima_data_impasto_fasi(fasi_impasto_per_lotto),
                progressivo=request.form.get("lotto_progressivo") or None,
                operatore=g.user.username,
            )
        except IntegrityError as exc:
            # Collisione tra due prenotazioni: nessun salvataggio parziale.
            raise ErroreLotto(
                "Lotto già utilizzato da un altro operatore: aggiorna la proposta."
            ) from exc

        # Le fasi sono state gia' calcolate senza scritture nella simulazione.
        for fase in fasi:
            fase.IdPianificazione = pianificazione.IdPianificazione

        # La concorrenza NON è più vietata per IdLavorazione.
        # Verifica la disponibilità delle risorse sulle fasi effettive
        # (che possono avere offset rispetto all'inizio della pianificazione).
        fasi_impasto, (fase_forno, lavoro_forno, ricetta_fase_forno) = (
            individua_fasi_risorse(fasi)
        )
        assegnazioni_macchine, blocchi_per_fase_impasto = verifica_e_distribuisci_impasti(
            impastatrici=list_impastatrici,
            peso_totale_grammi=peso_totale_pianificato,
            fasi_impasto=fasi_impasto,
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
            # Una sola associazione legacy macchina-piano, anche se la
            # ricetta richiede due o piu' impasti sulla stessa impastatrice.
            db.session.add(PianificazioneOrdiniImpastatrici(
                IdPianificazione=pianificazione.IdPianificazione,
                IdImpastatrice=id_macchina,
            ))
            for fase_impasto, blocchi_impasto in blocchi_per_fase_impasto:
                for giorno, blocco in blocchi_impasto:
                    db.session.add(PianificazioneImpastatriceFase(
                        IdPianificazioneFase=fase_impasto.IdPianificazioneFase,
                        IdImpastatrice=id_macchina,
                        DataFase=giorno,
                        Blocco=blocco,
                        # Peso INFORMATIVO per passaggio: non sommarlo fra
                        # fasi ripetute o slot successivi della stessa fase.
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
        # Il surplus è una stima: nessun accredito prima della chiusura.

        id_pianificazione = pianificazione.IdPianificazione
        db.session.commit()

    except (ErroreCalcoloPianificazione, ErroreCalcoloRiepilogo, ValueError) as errore:
        db.session.rollback()
        return _errore_conferma_pianificazione(str(errore))

    except IntegrityError:
        db.session.rollback()
        return _errore_conferma_pianificazione(
            "Lotto o altra risorsa già occupati: aggiorna la proposta e riprova"
        )

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Errore nel salvataggio della pianificazione")
        return _errore_conferma_pianificazione("Errore durante il salvataggio della pianificazione")

    response = make_response(
        render_template(
            template, id_pianificazione=id_pianificazione,
            qta_stock_generata=0,
            qta_stock_utilizzata=stock_da_usare, error=None
        )
    )
    response.headers["HX-Trigger"] = "pianificazioneSalvata, stockAggiornato"
    return response


# --- Ciclo di vita della pianificazione ---
from flaskr.service.ciclo_vita_pianificazione_service import (
    ErroreCicloVita, chiudi_pianificazione, annulla_pianificazione,
    calcola_surplus_teorico_storico, riconcilia_stock_storico,
)


@bp_ordini_cliente.get("/pianificazioni/chiuse")
@login_required
def pianificazioni_chiuse():
    piani = db.session.scalars(
        db.select(PianificazioneProduzione)
        .where(PianificazioneProduzione.StatoRigaOrdine.in_(("Chiuso", "Annullato")))
        .order_by(PianificazioneProduzione.IdPianificazione.desc())
    ).all()
    if ruolo_attuale() == "utente_produzione":
        piani = [p for p in piani if not articolo_cnf(p.CodArt)]
    return render_template("ordini/pianificazioni_storico.j2", piani=piani)


@bp_ordini_cliente.get("/pianificazioni/<int:IdPianificazione>/gestione")
@login_required
def gestione_pianificazione(IdPianificazione: int):
    piano = db.session.get(PianificazioneProduzione, IdPianificazione)
    if piano is None or (ruolo_attuale() == "utente_produzione" and articolo_cnf(piano.CodArt)):
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
    storico_spostamenti = db.session.scalars(
        db.select(StoricoRipianificazioni)
        .where(StoricoRipianificazioni.IdPianificazione == IdPianificazione)
        .order_by(StoricoRipianificazioni.Id.desc())
    ).all()
    # QtaStockGenerata=NULL e' previsto per i piani creati prima della migrazione.
    surplus_teorico = calcola_surplus_teorico_storico(piano, [a for a, _ in assoc])
    consuntivo_ordini = {
        (c.IdDocumento, c.IdRigaDoc): c
        for c in db.session.scalars(db.select(ConsuntivoProduzioneOrdine).where(
            ConsuntivoProduzioneOrdine.TipoPiano == "TRAD",
            ConsuntivoProduzioneOrdine.IdPiano == IdPianificazione,
        )).all()
    }
    consuntivo_articoli = {
        c.CodArt: c for c in db.session.scalars(db.select(ConsuntivoProduzioneArticolo).where(
            ConsuntivoProduzioneArticolo.TipoPiano == "TRAD",
            ConsuntivoProduzioneArticolo.IdPiano == IdPianificazione,
        )).all()
    }
    return render_template("ordini/gestione_pianificazione.j2", piano=piano,
                           consuntivo_ordini=consuntivo_ordini, consuntivo_articoli=consuntivo_articoli,
                           associazioni=assoc, registro=log,
                           storico_spostamenti=storico_spostamenti,
                           puo_ripianificare=ruolo_attuale() in ("admin", "utente_produzione"),
                           surplus_teorico=surplus_teorico,
                           puo_riconciliare=g.user.username in current_app.config.get("STOCK_RECONCILIATION_USERS", ()),
                           totale_da_stock=sum(int(a.QtaDaStock or 0) for a, _ in assoc))


# --- Ripianificazione senza cambiare ordini, impastatrici, kg o stock ---
from flaskr.accessi import verifica_csrf
from flaskr.models.model_ordini import StoricoRipianificazioni
from flaskr.service.ripianificazione_service import (
    anteprima_visuale_ripianificazione, salva_ripianificazione,
)


def _proteggi_ripianificazione(id_piano: int):
    if ruolo_attuale() not in ("utente_produzione", "admin"):
        abort(403)
    _proteggi_piano_cnf(id_piano)


@bp_ordini_cliente.get("/pianificazioni/<int:IdPianificazione>/ripianifica/anteprima")
@login_required
def anteprima_ripianificazione(IdPianificazione: int):
    _proteggi_ripianificazione(IdPianificazione)
    data_input = (request.args.get("nuova_data") or "").strip()
    try:
        if not data_input:
            raise ErroreCalcoloPianificazione("Seleziona la nuova data")
        nuova_data = date.fromisoformat(data_input)
        anteprima = anteprima_visuale_ripianificazione(
            IdPianificazione, nuova_data,
            mostra_articoli_cnf=(ruolo_attuale() != "utente_produzione"),
        )
        return render_template(
            "ordini/_partials/_anteprima_ripianificazione.j2",
            anteprima=anteprima, errore=None,
        )
    except (ValueError, ErroreCalcoloPianificazione) as eccezione:
        return render_template(
            "ordini/_partials/_anteprima_ripianificazione.j2",
            anteprima=None, errore=str(eccezione),
        )


@bp_ordini_cliente.post("/pianificazioni/<int:IdPianificazione>/ripianifica")
@login_required
def conferma_ripianificazione(IdPianificazione: int):
    _proteggi_ripianificazione(IdPianificazione)
    verifica_csrf()
    motivo = (request.form.get("motivo_ripianificazione") or "").strip()
    try:
        nuova_data = date.fromisoformat(request.form.get("nuova_data") or "")
        salva_ripianificazione(
            IdPianificazione, nuova_data, g.user.username, motivo,
            progressivo_lotto=request.form.get('lotto_progressivo') or None,
        )
    except (ValueError, ErroreCalcoloPianificazione) as errore:
        db.session.rollback()
        flash(str(errore), "danger")
    except IntegrityError:
        db.session.rollback()
        flash("Il lotto o una risorsa è già occupata: scegli un nuovo progressivo oppure una nuova data", "danger")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Errore durante la ripianificazione #%s", IdPianificazione)
        flash("Ripianificazione non salvata: errore nel database", "danger")
    else:
        flash(f"Pianificazione #{IdPianificazione} spostata al {nuova_data:%d/%m/%Y}", "success")
    return redirect(
        url_for("ordini_cliente.gestione_pianificazione", IdPianificazione=IdPianificazione),
        code=303,
    )


@bp_ordini_cliente.post("/pianificazioni/<int:IdPianificazione>/riconcilia-stock")
@login_required
def conferma_riconciliazione_stock(IdPianificazione: int):
    """Operazione amministrativa: nessun movimento di magazzino."""
    _proteggi_piano_cnf(IdPianificazione)
    # Disabilitata per default. Autorizzare esplicitamente gli utenti verificatori
    # con app.config["STOCK_RECONCILIATION_USERS"] = ["username1", ...].
    autorizzati = current_app.config.get("STOCK_RECONCILIATION_USERS", ())
    if g.user.username not in autorizzati:
        abort(403)
    valore = (request.form.get("pezzi_stock_accreditati") or "").strip()
    motivo = (request.form.get("motivo_riconciliazione") or "").strip()
    conferma = request.form.get("conferma_verifica_stock") == "1"
    try:
        if not conferma:
            raise ErroreCicloVita("Confermare di avere verificato i movimenti di stock")
        if not valore.isdecimal():
            raise ErroreCicloVita("Indicare un numero intero non negativo di pezzi accreditati")
        riconcilia_stock_storico(IdPianificazione, g.user.username, int(valore), motivo)
    except ErroreCicloVita as errore:
        flash(str(errore), "danger")
        return redirect(url_for("ordini_cliente.gestione_pianificazione", IdPianificazione=IdPianificazione), code=303)
    flash(f"Stock del piano #{IdPianificazione} riconciliato. Puoi procedere all'annullamento.", "success")
    return redirect(url_for("ordini_cliente.gestione_pianificazione", IdPianificazione=IdPianificazione), code=303)


@bp_ordini_cliente.post("/pianificazioni/<int:IdPianificazione>/chiudi")
@login_required
def conferma_chiusura_pianificazione(IdPianificazione: int):
    _proteggi_piano_cnf(IdPianificazione)
    verifica_csrf()
    try:
        piano = db.session.get(PianificazioneProduzione, IdPianificazione)
        if piano is None:
            abort(404)
        righe = db.session.scalars(db.select(PianificazioneOrdini).where(
            PianificazioneOrdini.IdPianificazione == IdPianificazione
        )).all()
        prodotte, articoli = leggi_form_consuntivo(request.form, [
            (r.IdDocumento, r.IdRiga, piano.CodArt, int(r.QtaPianificata)) for r in righe
        ])
        chiudi_pianificazione(IdPianificazione, g.user.username, prodotte, articoli)
    except (ErroreCicloVita, ErroreConsuntivo) as errore:
        flash(str(errore), "danger")
        return redirect(url_for("ordini_cliente.gestione_pianificazione", IdPianificazione=IdPianificazione), code=303)
    flash(f"Pianificazione #{IdPianificazione} chiusa correttamente.", "success")
    return redirect(url_for("ordini_cliente.pianificazioni_chiuse"), code=303)


@bp_ordini_cliente.post("/pianificazioni/<int:IdPianificazione>/annulla")
@login_required
def conferma_annullamento_pianificazione(IdPianificazione: int):
    _proteggi_piano_cnf(IdPianificazione)
    motivo = (request.form.get("motivo_annullamento") or "").strip()
    try:
        annulla_pianificazione(IdPianificazione, g.user.username, motivo)
    except ErroreCicloVita as errore:
        flash(str(errore), "danger")
        return redirect(url_for("ordini_cliente.gestione_pianificazione", IdPianificazione=IdPianificazione), code=303)
    flash(f"Pianificazione #{IdPianificazione} annullata. Quantità e risorse ripristinate.", "success")
    return redirect(url_for("ordini_cliente.pianificazioni_chiuse"), code=303)

# AVVISO_CONSEGNA_20261103
from flaskr.routes_modules import avviso_consegna_routes as _avviso_consegna_routes  # noqa: F401

# Importato a caricamento del blueprint: registra gli endpoint misti nel
# medesimo modulo di pianificazione e garantisce l'helper Jinja del lotto.
from flaskr.routes_modules import lotto_pianificazione_routes as _lotto_routes  # noqa: E402,F401
from flaskr.routes_modules import pianificazione_mista_unificata as _misto_unificato  # noqa: E402,F401

# Le nuove route logistiche condividono il blueprint ordini_cliente.
from flaskr.routes_modules import spedizioni as _spedizioni_routes  # noqa: E402,F401
