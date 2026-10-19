from collections.abc import Sequence
from datetime import datetime, time

from flaskr.models.model_ordini import (
    OrdiniCliente,
    PianificazioneFasi,
    PianificazioneProduzione,
    TipologieLavorazione,
)

ORARI_BLOCCHI = {
    "M": {
        "start": time(6, 0),
        "end": time(12, 30),
    },
    "P": {
        "start": time(13, 0),
        "end": time(17, 0),
    },
}


def costruisci_evento_fase(
    fase: PianificazioneFasi,
    pianificazione: PianificazioneProduzione,
    lavorazione: TipologieLavorazione,
    ordini: Sequence[OrdiniCliente],
):
    id_pianificazione_fase = fase.IdPianificazioneFase
    id_pianificazione = pianificazione.IdPianificazione
    codice_articolo = pianificazione.CodArt
    descrizione_lavorazione = lavorazione.DescrizioneLavorazione
    titolo = f"{codice_articolo} - {descrizione_lavorazione}"
    orari_blocco = ORARI_BLOCCHI[fase.Blocco]
    start = datetime.combine(fase.DataFase, orari_blocco["start"])
    end = datetime.combine(fase.DataFase, orari_blocco["end"])
    quantita_panettoni_rimanente = 0
    tipologia_panettone = pianificazione.TipologiaPanettone
    list_ordini = list()
    for ordine in ordini:
        quantita_panettoni_rimanente += ordine.QTA_SALDO_DOC
        id_ordine = f"{ordine.IdDocumento}-{ordine.IdRigaDoc}"
        list_ordini.append(id_ordine)

    return {
        "id": id_pianificazione_fase,
        "title": titolo,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "allDay": False,
        "backgroundColor": "#2563eb",
        "borderColor": "#2563eb",
        "extendedProps": {
            "id_pianificazione": id_pianificazione,
            "cod_art": codice_articolo,
            "quantita": quantita_panettoni_rimanente,
            "tipologia_panettone": tipologia_panettone,
            "lavorazione": descrizione_lavorazione,
            "ordini": list_ordini,
            "impastatrici": "",
        },
    }
