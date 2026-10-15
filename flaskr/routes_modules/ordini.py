from datetime import date, datetime
from typing import Any

from flask import abort, g, make_response, render_template, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaImpastatrici,
    CapacitaForno,
    Costanti,
    LogOperazioni,
    OrdiniCliente,
    PianificazioneFasi,
    PianificazioneOrdini,
    PianificazioneOrdiniImpastatrici,
    PianificazioneProduzione,
)
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.ordini_service import (
    ErroreCalcoloRiepilogo,
    calcolo_infornate,
    calcolo_riepilogo_ordini,
    dati_tabella_produzione,
    determina_tipologia_panettone,
    recupera_ordini_selezionati,
    trova_capacita_forno,
)
from flaskr.service.pianificazione_service import (
    ErroreCalcoloPianificazione,
    blocchi_fase,
    recupero_fasi_per_ricetta,
    recupero_ricetta_articolo,
    validazione_capacita_infornate,
    validazione_conflitti_idlavorazione,
    validazione_linearita_offset,
)


@bp_ordini_cliente.route("/tabella_ordini_da_produrre")
@login_required
def tabella_ordini_cliente_da_produrre():
    ordini_cliente_assegnati = dati_tabella_produzione(stato="Nuovo")
    return render_template(
        "ordini/_partials/_partial_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
    )


@bp_ordini_cliente.route("/tabella_ordini_in_produzione")
@login_required
def tabella_ordini_cliente_in_produzione():
    ordini_cliente_assegnati = dati_tabella_produzione(stato="In produzione")
    return render_template(
        "ordini/_partials/_partial_tabella_ordini.j2",
        ordini_cliente=ordini_cliente_assegnati,
    )


@bp_ordini_cliente.route("/riepilogo_selezione", methods=["POST"])
@login_required
def riepilogo_selezione():
    list_ordini_selezionati: list[str] = request.form.getlist("ordini_selezionati")
    try:
        list_ordini_selezionati_obj = recupera_ordini_selezionati(list_ordini_selezionati)
        risultato: dict[str, str] = calcolo_riepilogo_ordini(list_ordini_selezionati_obj)
    except ErroreCalcoloRiepilogo as errore:
        return render_template("ordini/_partials/_partial_riepilogo_ordini.j2", errore=str(errore))
    return render_template(
        "ordini/_partials/_partial_riepilogo_ordini.j2",
        numero_ordini=risultato["numero_ordini"],
        qta_panettoni=risultato["qta_panettoni"],
        peso_impasto=risultato["peso_impasto"],
        numero_cotte=risultato["numero_cotte"],
        panettoni_avanzo=risultato["panettoni_avanzo"],
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
        render_template("ordini/_partials/_partial_dettaglio_ordine.j2", errore=errore)
    if ordine is None:
        abort(404)

    return render_template(
        "ordini/_partials/_partial_dettaglio_ordine.j2", ordine=ordine, errore=None
    )


@bp_ordini_cliente.post("/pianificazione/anteprima")
@login_required
def anteprima_pianificazione():
    ordini_selezionati = request.form.getlist("ordini_selezionati")
    if not ordini_selezionati:
        return render_template(
            "ordini/_partials/_form_pianificazione.j2", error="Nessun ordine selezionato"
        )
    stmt_anagrafica_impastatrici = db.select(AnagraficaImpastatrici).order_by(
        AnagraficaImpastatrici.IdImpastatrice
    )
    impastatrici = db.session.scalars(stmt_anagrafica_impastatrici).all()
    return render_template(
        "ordini/_partials/_form_pianificazione.j2",
        impastatrici=impastatrici,
        ordini_selezionati=ordini_selezionati,
        error="",
    )


@bp_ordini_cliente.post("/pianificazione/conferma")
@login_required
def conferma_pianificazione():
    data_inizio_produzione = request.form.get("data_inizio_produzione")
    blocco_iniziale = request.form.get("blocco_inizio_produzione")
    if not blocco_iniziale:
        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2", error="Seleziona blocco iniziale"
        ), 400
    try:
        blocco_iniziale = int(blocco_iniziale)
    except ValueError:
        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2", error="Non conformità output"
        ), 400
    if blocco_iniziale not in (0, 1):
        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2", error="Non conformità output"
        ), 400
    blocco_iniziale_db = "M" if blocco_iniziale == 0 else "P"
    if not data_inizio_produzione:
        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2", error="Seleziona una data di produzione"
        ), 400

    try:
        data_produzione = date.fromisoformat(data_inizio_produzione)
    except ValueError:
        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2", error="La data inserita non è valida"
        ), 400
    form_ordini_selezionati = request.form.getlist("ordini_selezionati")
    if not form_ordini_selezionati:
        error = "Nessun ordine selezionato"
        return render_template("ordini/_partials/_conferma_pianificazione.j2", error=error)

    impastatrici_selezionate = request.form.getlist("impastatrici_selezionate")
    if not impastatrici_selezionate:
        error = "Nessun impastatrice selezionata"
        return render_template("ordini/_partials/_conferma_pianificazione.j2", error=error)
    list_impastatrici = []
    for impastatrice in impastatrici_selezionate:
        stmt_impastatrice_selezionata = db.select(AnagraficaImpastatrici).where(
            AnagraficaImpastatrici.IdImpastatrice == impastatrice
        )
        impastatrice_selezionata = db.session.scalars(stmt_impastatrice_selezionata).one_or_none()
        if not impastatrice_selezionata:
            error = f"Non è stato possibile selezionare l'impastatrice corrente {impastatrice}"
            return render_template("ordini/_partials/_conferma_pianificazione.j2", error=error)
        list_impastatrici.append(impastatrice_selezionata)
    # FASE 1 - Recupero e validazione degli ordini
    try:
        list_ordini_selezionati = recupera_ordini_selezionati(form_ordini_selezionati)

        # Verifico lo stato di tutti gli ordini PRIMA degli INSERT
        for ordine in list_ordini_selezionati:
            if ordine.stato is None:
                raise ErroreCalcoloRiepilogo(
                    f"L'ordine {ordine.IdDocumento}-{ordine.IdRigaDoc} non ha uno stato associato"
                )

            if ordine.stato.Stato != "Nuovo":
                raise ErroreCalcoloRiepilogo(
                    f"L'ordine {ordine.IdDocumento}-{ordine.IdRigaDoc} "
                    "non è disponibile per la pianificazione"
                )

        risultato = calcolo_riepilogo_ordini(list_ordini_selezionati)

    except ErroreCalcoloRiepilogo as errore:
        return render_template("ordini/_partials/_conferma_pianificazione.j2", error=str(errore))

    # FASE 2 - Inserimento dati nel database
    try:
        # 2.1 Creazione pianificazione principale
        pianificazione = PianificazioneProduzione(
            DataProduzione=data_produzione,
            IdForno=risultato["id_forno"],
            TipologiaPanettone=risultato["tipologia_panettone"],
            StatoRigaOrdine="In produzione",
            PesoTotale=risultato["peso_totale_grammi"],
            NumeroCotte=risultato["numero_cotte"],
            CodArt=risultato["codart_ordine"],
            BloccoIniziale=blocco_iniziale_db,
            IdRicetta=risultato["id_ricetta"],
        )

        db.session.add(pianificazione)
        db.session.flush()

        # 2.2 Associazione ordini e aggiornamento stati
        list_forni: list[CapacitaForno] = []
        panettoni_previsti = 0
        for ordine in list_ordini_selezionati:
            associazione = PianificazioneOrdini(
                IdPianificazione=pianificazione.IdPianificazione,
                IdDocumento=ordine.IdDocumento,
                IdRiga=ordine.IdRigaDoc,
            )
            db.session.add(associazione)

            # Aggiorno lo stato dell'ordine
            ordine.stato.Stato = "In produzione"

            log = LogOperazioni(
                IdDocumento=ordine.IdDocumento,
                IdRigaDoc=ordine.IdRigaDoc,
                Stato="In produzione",
                Operatore=g.user.username,
                synced_at=datetime.now(),
            )
            db.session.add(log)
            forno, _ = trova_capacita_forno(ordine, risultato["tipologia_panettone"])
            list_forni.append(forno)
            panettoni_previsti += ordine.QTA_ORD

        set_capienza_infornata: set[int] = {forno.Capienza for forno in list_forni}
        if not set_capienza_infornata:
            raise ErroreCalcoloPianificazione("Non è stata trovata la capacità delle infornate")
        if len(set_capienza_infornata) > 1:
            raise ErroreCalcoloPianificazione(
                "Sono state selezionate delle ricette con capacità infornate differenti"
            )
        capienza_infornata = next(iter(set_capienza_infornata))

        # 2.3 Associazione impastatrici
        for impastatrice in list_impastatrici:
            associazione_imp = PianificazioneOrdiniImpastatrici(
                IdPianificazione=pianificazione.IdPianificazione,
                IdImpastatrice=impastatrice.IdImpastatrice,
            )

            db.session.add(associazione_imp)

        # 2.4 Confermo tutte le operazioni

        ricetta = recupero_ricetta_articolo(list_ordini_selezionati)
        fasi_ricetta = recupero_fasi_per_ricetta(ricetta)
        capacita_massima_infornate_blocco = next(
            (fase.CapacitaMax for fase in fasi_ricetta if fase.IdLavorazione == 2), None
        )
        if not capacita_massima_infornate_blocco:
            raise ErroreCalcoloPianificazione(
                "Non è stata impostata la capacità massima di infornate per questa ricetta"
            )
        lista_fasi_pianificate: list[PianificazioneFasi] = blocchi_fase(
            fasi_ricetta, pianificazione, data_inizio_produzione, blocco_iniziale
        )
        if not validazione_linearita_offset(lista_fasi_pianificate):
            raise ErroreCalcoloPianificazione("Errore nell'impostazione degli offset di produzione")

        if not validazione_capacita_infornate(
            capacita_massima_infornate_blocco, capienza_infornata, panettoni_previsti
        ):
            raise ErroreCalcoloPianificazione(
                "La capacità delle infornate è stata superata",
            )
        if not validazione_conflitti_idlavorazione(lista_fasi_pianificate):
            raise ErroreCalcoloPianificazione(
                "Uno dei blocchi di produzione scelto include già una lavorazione identica",
            )
        db.session.add_all(lista_fasi_pianificate)
        db.session.commit()
    except (
        ErroreCalcoloPianificazione,
        ErroreCalcoloRiepilogo,
    ) as errore:
        db.session.rollback()
        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2",
            error=str(errore),
        )
    except SQLAlchemyError:
        db.session.rollback()

        return render_template(
            "ordini/_partials/_conferma_pianificazione.j2",
            error="Errore durante il salvataggio della pianificazione",
        )

    # FASE 3 - Risposta HTMX dopo il salvataggio
    response = make_response(
        render_template(
            "ordini/_partials/_conferma_pianificazione.j2",
            id_pianificazione=pianificazione.IdPianificazione,
            error=None,
        )
    )

    response.headers["HX-Trigger"] = "pianificazioneSalvata"

    return response
