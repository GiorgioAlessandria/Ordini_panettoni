"""Solo indicazioni a video, senza generazione o stampa di etichette."""
from __future__ import annotations

import re
from datetime import date, datetime
from flaskr.extensions import db
from flaskr.models.model_ordini import (
    CommentoEtichettaConfigurazione, CommentoEtichettaClienteScelta,
    CommentoEtichettaOrdine, OrdiniCliente,
)

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
    return selezione, risolvi_testo(testi_globali()[selezione], ordine)
