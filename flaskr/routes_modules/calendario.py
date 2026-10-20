from datetime import datetime

from flask import abort, jsonify, render_template, request, url_for
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models import OrdiniCliente
from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    AnagraficaImpastatrici,
    PianificazioneFasi,
    PianificazioneOrdini,
    PianificazioneOrdiniImpastatrici,
    PianificazioneProduzione,
    TipologieLavorazione,
)
from flaskr.routes_auth import login_required
from flaskr.routes_blueprint import bp_ordini_cliente
from flaskr.service.calendario_pianificazione_service import costruisci_evento_fase


class ErrorePianificazioneCalendario(Exception):
    pass


# eventi calendario
# {
# "id": "49794-1",
# "title": "Cliente ABC - PNT750G",
# "start": "2026-10-05",
# "end": "2026-10-06",
# "allDay": true
# }
@bp_ordini_cliente.route("/calendario")
@login_required
def ordini_calendario():
    return render_template("ordini/calendario.j2")


@bp_ordini_cliente.route("/calendario/dati_scadenza")
@login_required
def ordini_calendario_scadenze():
    data_inizio = datetime.fromisoformat(request.args["start"])
    data_fine = datetime.fromisoformat(request.args["end"])
    stmt = (
        db.select(OrdiniCliente)
        .options(joinedload(OrdiniCliente.Cliente))
        .where(
            OrdiniCliente.DataConsegna >= data_inizio,
            OrdiniCliente.DataConsegna < data_fine,
        )
    )
    ordini_data_scadenza = db.session.execute(stmt).scalars().all()
    json_ordini_data_scadenza = [
        {
            "id": f"{ordine.IdDocumento}-{ordine.IdRigaDoc}",
            "title": f"{ordine.Cliente.RagioneSociale} · {ordine.DesArt or 'Articolo senza descrizione'}",
            "start": ordine.DataConsegna.date().isoformat(),
            "allDay": True,
            "extendedProps": {
                "tipo_evento": "scadenza",
                "descrizione_articolo": ordine.DesArt or "Articolo senza descrizione",
                "cod_art": ordine.CodArt,
                "cliente": ordine.Cliente.RagioneSociale,
                "quantita_residua": ordine.QTA_SALDO_DOC,
                "detailUrl": url_for(
                    "ordini_cliente.calendario_riepilogo_ordine",
                    IdDocumento=ordine.IdDocumento,
                    IdRigaDoc=ordine.IdRigaDoc,
                )
            },
        }
        for ordine in ordini_data_scadenza
    ]

    return jsonify(json_ordini_data_scadenza)


@bp_ordini_cliente.get("/calendario/pianificazione/eventi")
@login_required
def eventi_fase_calendario():
    """Un evento per fase, aggregando una volta le quantita' degli ordini del piano."""
    from collections import defaultdict

    inizio = datetime.fromisoformat(request.args["start"]).date()
    fine = datetime.fromisoformat(request.args["end"]).date()

    fasi = db.session.scalars(
        db.select(PianificazioneFasi)
        .where(PianificazioneFasi.DataFase >= inizio, PianificazioneFasi.DataFase < fine)
        .order_by(PianificazioneFasi.DataFase, PianificazioneFasi.Blocco)
    ).all()
    if not fasi:
        return jsonify([])

    ids_piani = {fase.IdPianificazione for fase in fasi}
    ids_lavorazioni = {fase.IdLavorazione for fase in fasi}

    # Recuperi in blocco: nessuna query per ogni ordine o ogni fase.
    piani = {
        p.IdPianificazione: p for p in db.session.scalars(
            db.select(PianificazioneProduzione).where(
                PianificazioneProduzione.IdPianificazione.in_(ids_piani)
            )
        ).all()
    }
    # Descrizione articolo, recuperata una volta per tutti i piani visualizzati.
    codici_articolo = {p.CodArt for p in piani.values() if p.CodArt}
    descrizioni_articoli = {}
    if codici_articolo:
        descrizioni_articoli = {
            a.CodArt: a.DesArt
            for a in db.session.scalars(
                db.select(AnagraficaArticoli).where(AnagraficaArticoli.CodArt.in_(codici_articolo))
            ).all()
        }
    lavorazioni = {
        l.IdLavorazione: l for l in db.session.scalars(
            db.select(TipologieLavorazione).where(
                TipologieLavorazione.IdLavorazione.in_(ids_lavorazioni)
            )
        ).all()
    }
    associazioni = db.session.scalars(
        db.select(PianificazioneOrdini).where(
            PianificazioneOrdini.IdPianificazione.in_(ids_piani),
            PianificazioneOrdini.Stato.in_(("In produzione", "Chiuso")),
        )
    ).all()
    associazioni_per_piano = defaultdict(list)
    for a in associazioni:
        associazioni_per_piano[a.IdPianificazione].append(a)

    impastatrici_associate = db.session.scalars(
        db.select(PianificazioneOrdiniImpastatrici).where(
            PianificazioneOrdiniImpastatrici.IdPianificazione.in_(ids_piani)
        )
    ).all()
    ids_impastatrici = {a.IdImpastatrice for a in impastatrici_associate}
    nomi_impastatrici = {}
    if ids_impastatrici:
        nomi_impastatrici = {
            i.IdImpastatrice: i.DescrizioneImpastatrice
            for i in db.session.scalars(
                db.select(AnagraficaImpastatrici).where(
                    AnagraficaImpastatrici.IdImpastatrice.in_(ids_impastatrici)
                )
            ).all()
        }
    impastatrici_per_piano = defaultdict(list)
    for link in impastatrici_associate:
        nome = nomi_impastatrici.get(link.IdImpastatrice)
        if nome and nome not in impastatrici_per_piano[link.IdPianificazione]:
            impastatrici_per_piano[link.IdPianificazione].append(nome)

    eventi = []
    for fase in fasi:
        piano = piani.get(fase.IdPianificazione)
        lavorazione = lavorazioni.get(fase.IdLavorazione)
        if piano is not None and piano.StatoRigaOrdine == "Annullato":
            continue
        if piano is None or lavorazione is None:
            raise ErrorePianificazioneCalendario(
                f"Pianificazione o lavorazione inesistente per la fase {fase.IdPianificazioneFase}"
            )
        righe = associazioni_per_piano.get(fase.IdPianificazione, [])
        if not righe:
            continue  # Nessun ordine attivo/chiuso associato al programma.
        evento = costruisci_evento_fase(
            fase=fase,
            pianificazione=piano,
            lavorazione=lavorazione,
            associazioni=righe,
            descrizione_articolo=descrizioni_articoli.get(piano.CodArt) or "Articolo senza descrizione",
        )
        evento["extendedProps"]["impastatrici"] = impastatrici_per_piano[fase.IdPianificazione]
        evento["extendedProps"]["detailUrl"] = url_for(
            "ordini_cliente.calendario_riepilogo_piano", IdPianificazione=fase.IdPianificazione
        )
        eventi.append(evento)
    return jsonify(eventi)


# --- Calendario macchine (aggiungere una sola volta alla fine di calendario.py) ---
@bp_ordini_cliente.get('/calendario/macchine')
@login_required
def calendario_macchine():
    from datetime import date
    from flaskr.service.calendario_macchine_service import riepilogo_settimana_macchine

    valore = request.args.get('settimana', '')
    try:
        giorno = date.fromisoformat(valore) if valore else datetime.now().date()
    except ValueError:
        abort(400, description='La data indicata non è valida')
    riepilogo = riepilogo_settimana_macchine(giorno)
    return render_template('ordini/calendario_macchine.j2', riepilogo=riepilogo)


@bp_ordini_cliente.get('/calendario/macchine/griglia')
@login_required
def calendario_macchine_griglia():
    from datetime import date
    from flaskr.service.calendario_macchine_service import riepilogo_settimana_macchine

    valore = request.args.get('settimana', '')
    try:
        giorno = date.fromisoformat(valore) if valore else datetime.now().date()
    except ValueError:
        abort(400, description='La data indicata non è valida')
    riepilogo = riepilogo_settimana_macchine(giorno)
    return render_template('ordini/_partials/_griglia_macchine.j2', riepilogo=riepilogo)


@bp_ordini_cliente.get("/calendario/ordine/<int:IdDocumento>/<int:IdRigaDoc>/riepilogo")
@login_required
def calendario_riepilogo_ordine(IdDocumento: int, IdRigaDoc: int):
    """Riepilogo sintetico consultabile dagli eventi di scadenza."""
    ordine = db.session.scalar(
        db.select(OrdiniCliente)
        .options(joinedload(OrdiniCliente.Cliente))
        .where(OrdiniCliente.IdDocumento == IdDocumento, OrdiniCliente.IdRigaDoc == IdRigaDoc)
    )
    if ordine is None:
        abort(404)
    impegnata = db.session.scalar(
        db.select(db.func.coalesce(db.func.sum(PianificazioneOrdini.QtaPianificata), 0))
        .where(PianificazioneOrdini.IdDocumento == IdDocumento,
               PianificazioneOrdini.IdRiga == IdRigaDoc,
               PianificazioneOrdini.Stato.in_(("In produzione", "Chiuso")))
    )
    return render_template(
        "ordini/_partials/_riepilogo_calendario_ordine.j2", ordine=ordine,
        impegnata=int(impegnata or 0),
        residua=max(0, int(ordine.QTA_SALDO_DOC or 0) - int(impegnata or 0)),
    )


@bp_ordini_cliente.get("/calendario/pianificazione/<int:IdPianificazione>/riepilogo")
@login_required
def calendario_riepilogo_piano(IdPianificazione: int):
    """Elenco ordini, stock utilizzato, macchine e fasi per un piano."""
    piano = db.session.get(PianificazioneProduzione, IdPianificazione)
    if piano is None:
        abort(404)
    associazioni = db.session.execute(
        db.select(PianificazioneOrdini, OrdiniCliente)
        .join(OrdiniCliente,
              (PianificazioneOrdini.IdDocumento == OrdiniCliente.IdDocumento) &
              (PianificazioneOrdini.IdRiga == OrdiniCliente.IdRigaDoc))
        .options(joinedload(OrdiniCliente.Cliente))
        .where(PianificazioneOrdini.IdPianificazione == IdPianificazione)
        .order_by(OrdiniCliente.DataConsegna)
    ).all()
    fasi = db.session.execute(
        db.select(PianificazioneFasi, TipologieLavorazione)
        .join(TipologieLavorazione,
              TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione)
        .where(PianificazioneFasi.IdPianificazione == IdPianificazione)
        .order_by(PianificazioneFasi.OffsetBlocchi)
    ).all()
    macchine = db.session.execute(
        db.select(AnagraficaImpastatrici)
        .join(PianificazioneOrdiniImpastatrici,
              PianificazioneOrdiniImpastatrici.IdImpastatrice == AnagraficaImpastatrici.IdImpastatrice)
        .where(PianificazioneOrdiniImpastatrici.IdPianificazione == IdPianificazione)
    ).scalars().all()
    articolo = db.session.get(AnagraficaArticoli, piano.CodArt)
    return render_template(
        "ordini/_partials/_riepilogo_calendario_piano.j2",
        piano=piano, associazioni=associazioni, fasi=fasi,
        macchine=macchine, descrizione=articolo.DesArt if articolo else piano.CodArt,
        totale_assegnato=sum(int(a.QtaPianificata or 0) for a, _ in associazioni),
        totale_da_stock=sum(int(a.QtaDaStock or 0) for a, _ in associazioni),
    )
