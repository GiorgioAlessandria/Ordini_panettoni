from datetime import datetime
from tempfile import template
from typing import Sequence

from flask import abort, jsonify, render_template, request, url_for
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models import OrdiniCliente
from flaskr.models.model_ordini import (
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
            "title": ordine.Cliente.RagioneSociale,
            "start": ordine.DataConsegna.date().isoformat(),
            "allDay": True,
            "extendedProps": {
                "detailUrl": url_for(
                    "ordini_cliente.dettaglio_ordine",
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
    data_inizio_visualizzata = datetime.fromisoformat(request.args["start"]).date()
    data_fine_visualizzata = datetime.fromisoformat(request.args["end"]).date()

    stmt_fasi_pianificate = db.select(PianificazioneFasi).where(
        PianificazioneFasi.DataFase >= data_inizio_visualizzata,
        PianificazioneFasi.DataFase < data_fine_visualizzata,
    )
    fasi_pianificate: Sequence[PianificazioneFasi] = db.session.scalars(stmt_fasi_pianificate).all()
    list_eventi_calendario = []
    for fase in fasi_pianificate:
        stmt_pianificazione_produzione = db.select(PianificazioneProduzione).where(
            PianificazioneProduzione.IdPianificazione == fase.IdPianificazione
        )
        pianificazione_produzione = db.session.execute(
            stmt_pianificazione_produzione
        ).scalar_one_or_none()
        if not pianificazione_produzione:
            raise ErrorePianificazioneCalendario(
                "Non sono presenti pianificazioni riferite alla fase presente"
            )

        stmt_tipologia_lavorazione = db.select(TipologieLavorazione).where(
            TipologieLavorazione.IdLavorazione == fase.IdLavorazione
        )
        tipologia_lavorazione = db.session.scalars(stmt_tipologia_lavorazione).one_or_none()
        if not tipologia_lavorazione:
            raise ErrorePianificazioneCalendario(
                "Non sono presenti tipologie lavorazioni riferite alla fase presente"
            )

        stmt_pianificazione_ordini = db.select(PianificazioneOrdini).where(
            PianificazioneOrdini.IdPianificazione == pianificazione_produzione.IdPianificazione
        )
        pianificazione_ordini = db.session.scalars(stmt_pianificazione_ordini).all()
        if not pianificazione_ordini:
            raise ErrorePianificazioneCalendario(
                "Non sono presenti pianificazioni ordini legati alla pianificazione produzione"
            )
        list_ordini = []
        for pianificazione_ordine in pianificazione_ordini:
            stmt_ordini = db.select(OrdiniCliente).where(
                OrdiniCliente.IdDocumento == pianificazione_ordine.IdDocumento,
                OrdiniCliente.IdRigaDoc == pianificazione_ordine.IdRiga,
            )
            ordine = db.session.scalars(stmt_ordini).one_or_none()
            if not ordine:
                raise ErrorePianificazioneCalendario(
                    "Non sono presenti ordini legati alla pianificazione produzione"
                )
            list_ordini.append(ordine)

        stmt_pianificazione_impastatrice = db.select(PianificazioneOrdiniImpastatrici).where(
            PianificazioneOrdiniImpastatrici.IdPianificazione
            == pianificazione_produzione.IdPianificazione
        )
        pianificazioni_impastatrici = db.session.scalars(stmt_pianificazione_impastatrice).all()
        if not pianificazioni_impastatrici:
            raise ErrorePianificazioneCalendario(
                "Non sono presenti pianificazione_impastatrici pianificate per la produzione"
            )
        list_impastatrici = []
        for pianificazione_impastatrice in pianificazioni_impastatrici:
            stmt_impastatrici = db.select(AnagraficaImpastatrici).where(
                AnagraficaImpastatrici.IdImpastatrice == pianificazione_impastatrice.IdImpastatrice
            )
            impastatrici = db.session.scalars(stmt_impastatrici).all()
            if not impastatrici:
                raise ErrorePianificazioneCalendario(
                    "Non sono presenti impastatrici pianificate per la produzione"
                )
            for impastatrice in impastatrici:
                list_impastatrici.append(impastatrice.DescrizioneImpastatrice)

        evento_fase = costruisci_evento_fase(
            fase=fase,
            pianificazione=pianificazione_produzione,
            lavorazione=tipologia_lavorazione,
            ordini=list_ordini,
        )
        evento_fase["extendedProps"]["impastatrici"] = list_impastatrici
        list_eventi_calendario.append(evento_fase)
    return jsonify(list_eventi_calendario)
