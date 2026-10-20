from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from flask import abort, current_app, g, make_response, render_template, request
from sqlalchemy import tuple_
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
    Stock,
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

from flaskr.service.risorse_produzione_service import (
    individua_fasi_risorse,
    verifica_capacita_forno,
    verifica_e_distribuisci_impasto,
)


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
    return render_template(
        template,
        impastatrici=impastatrici,
        ordini_selezionati=ids_selezionati,
        riepilogo=riepilogo,
        error=None,
    )


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

        # FASE 4 - Calcolo del forno, delle cotte e della produzione.
        forno, ricetta = trova_capacita_forno(
            ordini_disponibili[0][0], risultato["tipologia_panettone"]
        )
        fasi_ricetta = recupero_fasi_per_ricetta(ricetta)
        # Individua la capacità di cottura dalla fase della ricetta, senza
        # assumere che l'IdLavorazione sia sempre 2.
        fasi_cottura = [
            fase for fase in fasi_ricetta
            if "forno" in str(fase.lavorazione.TipologiaLavorazione).casefold()
            or "cott" in str(fase.lavorazione.DescrizioneLavorazione).casefold()
            or "inforn" in str(fase.lavorazione.DescrizioneLavorazione).casefold()
        ]
        if len(fasi_cottura) != 1:
            raise ErroreCalcoloPianificazione(
                f"La ricetta richiede una fase di cottura, trovate {len(fasi_cottura)}"
            )
        capacita_max_blocco = fasi_cottura[0].CapacitaMax
        if capacita_max_blocco is None or capacita_max_blocco <= 0:
            raise ErroreCalcoloPianificazione("Capacità massima delle infornate non valida")
        if forno.Capienza is None or forno.Capienza <= 0:
            raise ErroreCalcoloPianificazione("Capienza del forno non valida")
        massimo_panettoni_blocco = int(capacita_max_blocco) * int(forno.Capienza)
        if panettoni_previsti > massimo_panettoni_blocco:
            raise ErroreCalcoloPianificazione(
                f"Sono richiesti {panettoni_previsti} panettoni, ma il forno permette "
                f"al massimo {massimo_panettoni_blocco} pezzi in {capacita_max_blocco} cotte. "
                "Riduci la selezione degli ordini."
            )

        (
            panettoni_infornata,
            panettoni_residui,
            panettoni_surplus,
            _,  # Il numero di cotte restituito dal servizio attuale è da correggere.
        ) = calcolo_max_panettoni(
            capacita_max_blocco,
            forno.Capienza,
            panettoni_previsti,
            ottimizza_infornate,
        )
        if panettoni_infornata <= 0:
            raise ErroreCalcoloPianificazione("Quantità produttiva non valida")

        # Si calcola sulle quantità effettivamente previste, non su quelle richieste.
        numero_cotte, _ = calcolo_infornate(forno.Capienza, panettoni_infornata)
        if numero_cotte > capacita_max_blocco:
            raise ErroreCalcoloPianificazione("Il numero di cotte supera la capacità del blocco")

        # FASE 5 - Distribuzione, prima di qualsiasi INSERT.
        capacita_assegnabile = min(panettoni_previsti, panettoni_infornata)
        assegnazioni = distribuisci_quantita_ordini(ordini_disponibili, capacita_assegnabile)
        totale_assegnato = sum(qta for _, qta in assegnazioni)
        if not assegnazioni or (
            totale_assegnato != capacita_assegnabile
            or panettoni_residui != panettoni_previsti - totale_assegnato
            or panettoni_surplus != panettoni_infornata - totale_assegnato
        ):
            raise ErroreCalcoloPianificazione("Incoerenza nelle quantità della pianificazione")

        # Il peso comprende anche l'eventuale surplus di produzione.
        qta_riepilogo = risultato["qta_panettoni"]
        if not qta_riepilogo or qta_riepilogo <= 0:
            raise ErroreCalcoloPianificazione("Quantità riepilogo non valida")
        peso_unitario = Decimal(str(risultato["peso_totale_grammi"])) / Decimal(qta_riepilogo)
        peso_totale_pianificato = (peso_unitario * Decimal(panettoni_infornata)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # FASE 6 - Registrazione atomica della pianificazione.
        pianificazione = PianificazioneProduzione(
            DataProduzione=data_produzione,
            IdForno=forno.IdForno,
            TipologiaPanettone=risultato["tipologia_panettone"],
            StatoRigaOrdine="In produzione",  # Stato generale del programma.
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
        for ordine, quantita_assegnata in assegnazioni:
            db.session.add(
                PianificazioneOrdini(
                    IdPianificazione=pianificazione.IdPianificazione,
                    IdDocumento=ordine.IdDocumento,
                    IdRiga=ordine.IdRigaDoc,
                    QtaPianificata=quantita_assegnata,
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

        # Lo stock viene aggiornato soltanto alla chiusura della produzione.
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
        render_template(template, id_pianificazione=id_pianificazione, error=None)
    )
    response.headers["HX-Trigger"] = "pianificazioneSalvata"
    return response
