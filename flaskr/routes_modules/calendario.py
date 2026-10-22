from collections import defaultdict
from datetime import date, datetime, time, timedelta

from flask import abort, jsonify, render_template, request, url_for
from sqlalchemy import tuple_
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
from flaskr.models.model_accessi import NotaVenditeOrdine
from flaskr.accessi import ruolo_attuale
from flaskr.service.scadenze_giornaliere_service import (
    articolo_cnf, eseguito, raggruppa_per_giorno,
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


def _ordini_scadenti(inizio: date, fine: date):
    """Intervallo [inizio, fine): CNF nascosti al solo ruolo produzione."""
    stmt = (
        db.select(OrdiniCliente)
        .options(joinedload(OrdiniCliente.Cliente), joinedload(OrdiniCliente.stato))
        .where(
            OrdiniCliente.DataConsegna >= datetime.combine(inizio, time.min),
            OrdiniCliente.DataConsegna < datetime.combine(fine, time.min),
        )
        .order_by(OrdiniCliente.DataConsegna, OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc)
    )
    if ruolo_attuale() == "utente_produzione":
        stmt = stmt.where(~db.func.upper(db.func.trim(OrdiniCliente.CodArt)).like("CNF%"))
    return db.session.scalars(stmt).all()


def _quantita_chiuse(ordini):
    """Aggrega solo le quantita' dei piani chiusi in batch (SQLite)."""
    chiavi = sorted({(o.IdDocumento, o.IdRigaDoc) for o in ordini})
    risultato = {}
    for start in range(0, len(chiavi), 250):
        blocco = chiavi[start:start + 250]
        stmt = (
            db.select(
                PianificazioneOrdini.IdDocumento,
                PianificazioneOrdini.IdRiga,
                db.func.sum(PianificazioneOrdini.QtaPianificata),
            )
            .where(
                tuple_(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga).in_(blocco),
                PianificazioneOrdini.Stato == "Chiuso",
            )
            .group_by(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga)
        )
        for documento, riga, quantita in db.session.execute(stmt):
            risultato[(documento, riga)] = int(quantita or 0)
    return risultato


def _note_del_giorno(ordini):
    """Commenti in una query per blocco di ordini, non una query per riga."""
    chiavi = sorted({(o.IdDocumento, o.IdRigaDoc) for o in ordini})
    note = defaultdict(list)
    for start in range(0, len(chiavi), 250):
        stmt = (
            db.select(NotaVenditeOrdine)
            .where(tuple_(NotaVenditeOrdine.IdDocumento, NotaVenditeOrdine.IdRigaDoc).in_(chiavi[start:start + 250]))
            .order_by(NotaVenditeOrdine.CreatoIl.desc(), NotaVenditeOrdine.Id.desc())
        )
        for nota in db.session.scalars(stmt):
            note[(nota.IdDocumento, nota.IdRigaDoc)].append(nota)
    return note


@bp_ordini_cliente.get("/calendario/dati_scadenza")
@login_required
def ordini_calendario_scadenze():
    """Un solo evento FullCalendar per giorno di consegna."""
    try:
        inizio = date.fromisoformat(request.args["start"][:10])
        fine = date.fromisoformat(request.args["end"][:10])
    except (KeyError, ValueError):
        abort(400, description="Intervallo date del calendario non valido.")
    if fine <= inizio:
        return jsonify([])
    ordini = _ordini_scadenti(inizio, fine)
    chiuse = _quantita_chiuse(ordini)
    eventi = []
    for gruppo in raggruppa_per_giorno(ordini, chiuse):
        giorno = gruppo["giorno"]
        in_attesa = gruppo["da_eseguire"] > 0
        eventi.append({
            "id": f"scadenze-{giorno.isoformat()}",
            "title": f"{gruppo['numero_ordini']} ordini · {gruppo['quantita_ordinata']} pz",
            "start": giorno.isoformat(),
            "allDay": True,
            "extendedProps": {
                "tipo_evento": "scadenze_giorno",
                "numero_ordini": gruppo["numero_ordini"],
                "numero_completati": gruppo["completati"],
                "numero_da_eseguire": gruppo["da_eseguire"],
                "quantita_ordinata": gruppo["quantita_ordinata"],
                "quantita_saldo": gruppo["quantita_saldo"],
                "stato_giorno": "da_eseguire" if in_attesa else "eseguito",
                "detailUrl": url_for("ordini_cliente.calendario_riepilogo_giorno", data=giorno.isoformat()),
            },
        })
    return jsonify(eventi)


@bp_ordini_cliente.get("/calendario/scadenze/<data>/riepilogo")
@login_required
def calendario_riepilogo_giorno(data: str):
    """Tabella unica dei documenti del giorno, con commenti nella stessa riga."""
    try:
        giorno = date.fromisoformat(data)
    except ValueError:
        abort(404)
    ordini = _ordini_scadenti(giorno, giorno + timedelta(days=1))
    if not ordini:
        abort(404)
    chiuse = _quantita_chiuse(ordini)
    righe = [
        {
            "ordine": o,
            "quantita_chiusa": chiuse.get((o.IdDocumento, o.IdRigaDoc), 0),
            "eseguito": eseguito(o, chiuse.get((o.IdDocumento, o.IdRigaDoc), 0)),
        }
        for o in ordini
    ]
    return render_template(
        "ordini/_partials/_riepilogo_calendario_giorno.j2",
        giorno=giorno,
        righe=righe,
        note_per_ordine=_note_del_giorno(ordini),
        eseguiti=sum(r["eseguito"] for r in righe),
    )


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
        if piano is not None and ruolo_attuale() == "utente_produzione" and articolo_cnf(piano.CodArt):
            continue
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
    if ordine is None or (ruolo_attuale() == "utente_produzione" and articolo_cnf(ordine.CodArt)):
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
    if piano is None or (ruolo_attuale() == "utente_produzione" and articolo_cnf(piano.CodArt)):
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
