"""Accesso tramite password personale SENZA username nel form.

Usa gli hash gia' presenti in User.password (Werkzeug). Una stessa
password non puo' identificare due account: il servizio rifiuta i
risultati ambigui e impedisce di assegnare password gia' utilizzate.

Per installazioni con molti utenti e' preferibile un indice HMAC
server-side: questa versione privilegia la compatibilita' col DB esistente.
"""
from __future__ import annotations

from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from flaskr.extensions import db
from flaskr.models.model_auth import User


def _verifica_hash(hash_salvato: str | None, password: str) -> bool:
    """Gestisce anche hash legacy non riconosciuti senza esporre errori."""
    if not hash_salvato:
        return False
    try:
        return check_password_hash(hash_salvato, password)
    except (ValueError, TypeError):
        return False


def cerca_per_password(password: str):
    """Restituisce User solo se esiste un unico account corrispondente.

    Non interrompe il ciclo al primo risultato per individuare eventuali
    credenziali duplicate gia' presenti negli account storici.
    """
    if not password:
        return None
    trovato = None
    for utente in db.session.scalars(select(User)).all():
        if _verifica_hash(utente.password, password):
            if trovato is not None:
                return None  # Credenziale ambigua: non scegliere un account a caso.
            trovato = utente
    return trovato


def password_gia_usata(password: str, escludi_user_id: int | None = None) -> bool:
    """Blocca la condivisione di una password tra account differenti."""
    if not password:
        return False
    for utente in db.session.scalars(select(User)).all():
        if utente.id != escludi_user_id and _verifica_hash(utente.password, password):
            return True
    return False


def imposta_password(utente: User, password: str) -> None:
    """Cambia l'hash senza commit (gestito dal chiamante). Nessun minimo."""
    if not password:
        raise ValueError("La password non puo' essere vuota.")
    if password_gia_usata(password, escludi_user_id=utente.id):
        raise ValueError("La password e' gia' utilizzata da un altro account.")
    utente.password = generate_password_hash(password)
