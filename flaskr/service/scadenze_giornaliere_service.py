"""Regole di aggregazione scadenze: nessuna modifica ai record ERP."""
from collections import defaultdict
from datetime import date, datetime


def articolo_cnf(cod_art: object) -> bool:
    """Sono riservati a vendite/admin i codici articolo che iniziano con CNF."""
    return str(cod_art or "").strip().upper().startswith("CNF")


def data_consegna_ordine(ordine) -> date:
    dato = ordine.DataConsegna
    if isinstance(dato, datetime):
        return dato.date()
    if isinstance(dato, date):
        return dato
    return date.fromisoformat(str(dato)[:10])


def eseguito(ordine, quantita_chiusa: int = 0) -> bool:
    """Eseguito solo se il saldo ERP e' zero o e' coperto da produzioni chiuse.

    Un piano in stato 'In produzione' NON rende l'ordine eseguito.
    Una chiusura parziale NON completa il residuo dell'ordine.
    """
    if ordine.QTA_SALDO_DOC is None:
        return False  # Saldo ignoto: non dichiarare l'ordine eseguito.
    saldo = max(0, int(ordine.QTA_SALDO_DOC))
    return saldo == 0 or int(quantita_chiusa or 0) >= saldo


def raggruppa_per_giorno(ordini, quantita_chiuse):
    gruppi = defaultdict(list)
    for ordine in ordini:
        gruppi[data_consegna_ordine(ordine)].append(ordine)

    risultato = []
    for giorno, gruppo in sorted(gruppi.items()):
        completati = sum(eseguito(o, quantita_chiuse.get((o.IdDocumento,o.IdRigaDoc), 0)) for o in gruppo)
        risultato.append({
            'giorno': giorno,
            'numero_ordini': len(gruppo),
            'completati': completati,
            'da_eseguire': len(gruppo) - completati,
            'quantita_ordinata': sum(int(o.QTA_ORD or 0) for o in gruppo),
            'quantita_saldo': sum(max(0, int(o.QTA_SALDO_DOC or 0)) for o in gruppo),
        })
    return risultato
