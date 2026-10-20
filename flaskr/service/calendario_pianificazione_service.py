from collections.abc import Sequence
from datetime import datetime, time
from decimal import Decimal

from flaskr.models.model_ordini import (
    PianificazioneFasi,
    PianificazioneOrdini,
    PianificazioneProduzione,
    TipologieLavorazione,
)

ORARI_BLOCCHI = {
    "M": {"start": time(6, 0), "end": time(12, 30)},
    "P": {"start": time(13, 0), "end": time(17, 0)},
}


def costruisci_evento_fase(
    fase: PianificazioneFasi,
    pianificazione: PianificazioneProduzione,
    lavorazione: TipologieLavorazione,
    associazioni: Sequence[PianificazioneOrdini],
    descrizione_articolo: str,
) -> dict:
    """Un evento per fase del programma; gli ordini del programma sono sommati.

    QtaPianificata e' la quantita' corretta. NON sommare QTA_SALDO_DOC
    dell'ordine ERP, che comprende parti non assegnate a questo programma.
    """
    if fase.Blocco not in ORARI_BLOCCHI:
        raise ValueError(f"Blocco fase non valido: {fase.Blocco!r}")

    valide = [a for a in associazioni if a.Stato in ("In produzione", "Chiuso")]
    incomplete = any(a.QtaPianificata is None for a in valide)
    quantita_assegnata = sum(int(a.QtaPianificata) for a in valide if a.QtaPianificata is not None)
    # I pezzi coperti da stock sono impegnati sull'ordine ma non prodotti nel piano.
    quantita_da_stock = sum(int(a.QtaDaStock or 0) for a in valide)
    quantita_da_produrre_ordine = quantita_assegnata - quantita_da_stock

    identificativi = [f"{a.IdDocumento}-{a.IdRiga}" for a in valide]
    orari = ORARI_BLOCCHI[fase.Blocco]
    start = datetime.combine(fase.DataFase, orari["start"])
    end = datetime.combine(fase.DataFase, orari["end"])

    panettoni_previsti = pianificazione.PanettoniPianificati
    peso_grammi = pianificazione.PesoTotale
    peso_kg = (
        float((Decimal(str(peso_grammi)) / Decimal(1000)).quantize(Decimal("0.01")))
        if peso_grammi is not None else None
    )
    surplus = (
        max(0, int(panettoni_previsti) - quantita_da_produrre_ordine)
        if panettoni_previsti is not None and not incomplete else None
    )

    return {
        "id": f"fase-{fase.IdPianificazioneFase}",
        "title": (
            f"Piano {pianificazione.IdPianificazione} · "
            f"{descrizione_articolo} · {lavorazione.DescrizioneLavorazione}"
        ),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "allDay": False,
        "extendedProps": {
            "id_pianificazione": pianificazione.IdPianificazione,
            "id_fase": fase.IdPianificazioneFase,
            "cod_art": pianificazione.CodArt,
            "descrizione_articolo": descrizione_articolo,
            "id_lavorazione": fase.IdLavorazione,
            "lavorazione": lavorazione.DescrizioneLavorazione,
            "tipologia_panettone": pianificazione.TipologiaPanettone,
            "numero_ordini": len(valide),
            "ordini": identificativi,
            "quantita_assegnata": quantita_assegnata if not incomplete else None,
            "quantita_da_stock": quantita_da_stock,
            "quantita_da_produrre_ordini": quantita_da_produrre_ordine,
            "quantita_parziale_nota": quantita_assegnata,
            "panettoni_previsti": panettoni_previsti,
            "numero_cotte": pianificazione.NumeroCotte,
            "peso_totale_kg": peso_kg,
            "surplus": surplus,
            "dati_incompleti": incomplete,
            "impastatrici": [],
        },
    }
