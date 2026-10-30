"""Solo indicazioni a video, senza generazione o stampa di etichette."""
from __future__ import annotations

import re
from datetime import date, datetime
from collections.abc import Mapping, Sequence
from sqlalchemy import tuple_
from sqlalchemy.orm import selectinload
from flaskr.extensions import db
from flaskr.models.model_ordini import (
    CommentoEtichettaConfigurazione, CommentoEtichettaClienteScelta,
    CommentoEtichettaOrdine, CommentoEtichettaOrdineTesto, OrdiniCliente,
)

_NON_CARICATO = object()

TESTO_1_DEFAULT = "Prodotto per: {{ragione_sociale}}"
TESTO_2_DEFAULT = "Ordine {{numero_ordine}} - {{descrizione_articolo}}"
TIPO_CLIENTE_NON_CONFIGURATO = 1
MAX_TESTO = 1500
SEGNAPOSTO = frozenset({
    "ragione_sociale", "numero_ordine", "codice_articolo", "descrizione_articolo",
    "data_consegna", "id_documento", "id_riga", "codice_cliente", "quantita_ordinata",
})
_PATTERN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class ErroreCommentoEtichetta(ValueError):
    pass


def valida_testo(testo: str) -> str:
    testo = (testo or "").strip()
    if not 1 <= len(testo) <= MAX_TESTO:
        raise ErroreCommentoEtichetta(f"Il commento deve avere da 1 a {MAX_TESTO} caratteri.")
    nomi = _PATTERN.findall(testo)
    non_ammessi = sorted(set(nomi) - SEGNAPOSTO)
    if non_ammessi:
        raise ErroreCommentoEtichetta("Segnaposto non ammessi: " + ", ".join(non_ammessi))
    residuo = _PATTERN.sub("", testo)
    if "{{" in residuo or "}}" in residuo:
        raise ErroreCommentoEtichetta("Segnaposto incompleto o non valido.")
    return testo


def testi_globali() -> dict[int, str]:
    modelli = {1: TESTO_1_DEFAULT, 2: TESTO_2_DEFAULT}
    for riga in db.session.scalars(db.select(CommentoEtichettaConfigurazione)):
        if riga.Id in modelli:
            modelli[riga.Id] = riga.Testo
    return modelli


def tipo_predefinito_cliente(cod_cliente: int | None) -> tuple[int, bool]:
    """Fallback 1 se il cliente non ha ancora scelta locale; il trigger la inizializza."""
    record = db.session.get(CommentoEtichettaClienteScelta, cod_cliente) if cod_cliente is not None else None
    return (int(record.TipoCommento), True) if record else (TIPO_CLIENTE_NON_CONFIGURATO, False)


def tipo_per_ordine(ordine: OrdiniCliente) -> tuple[int, bool]:
    """Precedenza: override riga ordine > scelta predefinita cliente > fallback 1."""
    scelta = db.session.get(CommentoEtichettaOrdine, (ordine.IdDocumento, ordine.IdRigaDoc))
    if scelta:
        return int(scelta.TipoCommento), True
    return tipo_predefinito_cliente(ordine.CodCliFor)[0], False


def _data_it(d: date | datetime | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def risolvi_testo(testo: str, ordine: OrdiniCliente) -> str:
    """Non valuta Jinja o espressioni: sostituisce soltanto i segnaposto permessi."""
    cliente = getattr(ordine, "Cliente", None)
    valori = {
        "ragione_sociale": str(getattr(cliente, "RagioneSociale", "") or ""),
        "numero_ordine": str(getattr(ordine, "NumRegistraz", "") or ""),
        "codice_articolo": str(getattr(ordine, "CodArt", "") or ""),
        "descrizione_articolo": str(getattr(ordine, "DesArt", "") or ""),
        "data_consegna": _data_it(getattr(ordine, "DataConsegna", None)),
        "id_documento": str(getattr(ordine, "IdDocumento", "") or ""),
        "id_riga": str(getattr(ordine, "IdRigaDoc", "") or ""),
        "codice_cliente": str(getattr(ordine, "CodCliFor", "") or ""),
        "quantita_ordinata": str(getattr(ordine, "QTA_ORD", "") or ""),
    }
    return _PATTERN.sub(lambda match: valori[match.group(1)], testo)


def indicazione_ordine(ordine: OrdiniCliente, *, tipo: int | None = None) -> tuple[int, str]:
    selezione = tipo if tipo is not None else tipo_per_ordine(ordine)[0]
    if selezione not in (1, 2):
        raise ErroreCommentoEtichetta("Commento non valido")
    override = db.session.get(
        CommentoEtichettaOrdineTesto,
        (ordine.IdDocumento, ordine.IdRigaDoc, selezione),
    )
    originale = override.Testo if override is not None else testi_globali()[selezione]
    return selezione, risolvi_testo(originale, ordine)


# Dati per la cella della tabella, senza modificare le selezioni presenti.
def dati_cella_ordine(
    ordine: OrdiniCliente,
    *,
    testi: dict[int, str] | None = None,
    predefinito: int | None = None,
    scelta_manuale: int | None = None,
    testo_personalizzato: str | None | object = _NON_CARICATO,
) -> dict:
    if testi is None:
        testi = testi_globali()
    if predefinito is None:
        predefinito = tipo_predefinito_cliente(ordine.CodCliFor)[0]
    if scelta_manuale is None:
        # Solo per il rendering di una cella singola (non per le tabelle).
        scelta = db.session.get(CommentoEtichettaOrdine, (ordine.IdDocumento, ordine.IdRigaDoc))
        scelta_manuale = int(scelta.TipoCommento) if scelta else 0
    tipo = scelta_manuale or predefinito
    if testo_personalizzato is _NON_CARICATO:
        personalizzazione = db.session.get(
            CommentoEtichettaOrdineTesto,
            (ordine.IdDocumento, ordine.IdRigaDoc, tipo),
        )
        testo_personalizzato = personalizzazione.Testo if personalizzazione else None
    personalizzato = testo_personalizzato is not None
    contenuto = testo_personalizzato if personalizzato else testi[tipo]
    return {
        "tipo": tipo,
        "predefinito": predefinito,
        "manuale": bool(scelta_manuale),
        "personalizzato": personalizzato,
        "testo": risolvi_testo(contenuto, ordine),
    }


def _chiave_riga(ordine) -> tuple[int, int] | None:
    def campo(nome):
        return ordine.get(nome) if isinstance(ordine, Mapping) else getattr(ordine, nome, None)
    try:
        return int(campo("IdDocumento")), int(campo("IdRigaDoc"))
    except (ValueError, TypeError):
        return None


def commenti_etichette_per_righe(ordini_cliente: Sequence, selezione_attiva: bool) -> dict[tuple[int,int], dict]:
    """Recupero batch per le due tabelle ordini (righe singole o piani raggruppati)."""
    chiavi: set[tuple[int, int]] = set()
    for riga in ordini_cliente:
        ordini = (riga,) if selezione_attiva else (
            riga.get("OrdiniDettaglio", ()) if isinstance(riga, Mapping)
            else getattr(riga, "OrdiniDettaglio", ())
        )
        for ordine in ordini or ():
            chiave = _chiave_riga(ordine)
            if chiave is not None:
                chiavi.add(chiave)
    if not chiavi:
        return {}

    # Le impostazioni sono globali; tutte le scelte sono lette in batch.
    testi = testi_globali()
    risultato = {}
    elenco = sorted(chiavi)
    for inizio in range(0, len(elenco), 200):
        gruppo = elenco[inizio:inizio + 200]
        ordini = db.session.scalars(
            db.select(OrdiniCliente)
            .options(selectinload(OrdiniCliente.Cliente))
            .where(tuple_(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc).in_(gruppo))
        ).all()
        if not ordini:
            continue
        clienti = {o.CodCliFor for o in ordini}
        scelte_clienti = {
            x.CodCliFor: int(x.TipoCommento)
            for x in db.session.scalars(
                db.select(CommentoEtichettaClienteScelta)
                .where(CommentoEtichettaClienteScelta.CodCliFor.in_(clienti))
            ).all()
        }
        scelte_ordini = {
            (x.IdDocumento, x.IdRigaDoc): int(x.TipoCommento)
            for x in db.session.scalars(
                db.select(CommentoEtichettaOrdine)
                .where(tuple_(CommentoEtichettaOrdine.IdDocumento, CommentoEtichettaOrdine.IdRigaDoc).in_(gruppo))
            ).all()
        }
        testi_personalizzati = {
            (r.IdDocumento, r.IdRigaDoc, int(r.TipoCommento)): r.Testo
            for r in db.session.scalars(
                db.select(CommentoEtichettaOrdineTesto)
                .where(tuple_(CommentoEtichettaOrdineTesto.IdDocumento,
                              CommentoEtichettaOrdineTesto.IdRigaDoc).in_(gruppo))
            ).all()
        }
        for ordine in ordini:
            chiave = ordine.IdDocumento, ordine.IdRigaDoc
            tipo_effettivo = scelte_ordini.get(
                chiave, scelte_clienti.get(ordine.CodCliFor, TIPO_CLIENTE_NON_CONFIGURATO)
            )
            risultato[chiave] = dati_cella_ordine(
                ordine,
                testi=testi,
                predefinito=scelte_clienti.get(ordine.CodCliFor, TIPO_CLIENTE_NON_CONFIGURATO),
                scelta_manuale=scelte_ordini.get(chiave, 0),
                testo_personalizzato=testi_personalizzati.get(
                    (chiave[0], chiave[1], tipo_effettivo)
                ),
            )
    return risultato
