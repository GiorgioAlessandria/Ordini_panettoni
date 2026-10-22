"""Codici individuali per autenticazione senza inserire l'username.

Codici generati casualmente di 16 caratteri (~79 bit): nel DB rimane
soltanto SHA256. La password legacy non e' usata per il login.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from flaskr.extensions import db
from flaskr.models.model_login_code import CodiceLoginUtente, TentativoLoginCodice

_ALFABETO = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # esclusi 0/1/I/O (L resta ammessa per compatibilità)
_FORMATO = re.compile(r'^[A-HJ-NP-Z2-9]+$')
FINESTRA_MINUTI = 15
TENTATIVI_MAX = 5


def normalizza_codice(valore: str) -> str:
    return re.sub(r'[\s-]+', '', valore or '').upper()


def codice_valido(valore: str) -> bool:
    return len(valore) <= 64 and bool(_FORMATO.fullmatch(valore))


def impronta_codice(codice: str) -> str:
    return hashlib.sha256(codice.encode('ascii')).hexdigest()


def genera_codice(user_id: int) -> str:
    """Crea/ruota il codice senza commit: chi chiama completa la transazione.

    Restituisce il codice in chiaro UNA SOLA volta (da mostrare all'admin).
    """
    from sqlalchemy.exc import IntegrityError
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError('UserId non valido')
    codice = ''.join(secrets.choice(_ALFABETO) for _ in range(16))
    record = db.session.get(CodiceLoginUtente, user_id)
    if record is None:
        record = CodiceLoginUtente(UserId=user_id, Digest=impronta_codice(codice))
        db.session.add(record)
    else:
        record.Digest = impronta_codice(codice)
        record.GeneratoIl = datetime.now(timezone.utc)
    return '-'.join(codice[i:i + 4] for i in range(0, 16, 4))


def trova_utente(codice_digitato: str):
    """Restituisce l'ID o None senza indicare quali codici esistano."""
    from sqlalchemy import select
    testo = normalizza_codice(codice_digitato)
    if not codice_valido(testo):
        return None
    return db.session.scalar(
        select(CodiceLoginUtente.UserId).where(
            CodiceLoginUtente.Digest == impronta_codice(testo)
        )
    )


def _come_naive_utc(valore: datetime | None):
    """SQLite puo' restituire datetime privi di tzinfo."""
    if valore is None:
        return None
    if valore.tzinfo is None:
        return valore.replace(tzinfo=timezone.utc)
    return valore.astimezone(timezone.utc)


def digest_indirizzo(ip: str) -> str:
    return hashlib.sha256(ip.encode('utf-8')).hexdigest()


def blocco_attivo(ip: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    item = db.session.get(TentativoLoginCodice, digest_indirizzo(ip))
    if item is None:
        return False
    return bool(item.BloccatoFino and _come_naive_utc(item.BloccatoFino) > now)


def registra_errore(ip: str, now: datetime | None = None) -> None:
    """Limite persistente: cinque tentativi per IP in 15 minuti."""
    now = now or datetime.now(timezone.utc)
    key = digest_indirizzo(ip)
    item = db.session.get(TentativoLoginCodice, key)
    if item is None:
        item = TentativoLoginCodice(IpDigest=key, Tentativi=0, InizioFinestra=now)
        db.session.add(item)
    elif now - _come_naive_utc(item.InizioFinestra) >= timedelta(minutes=FINESTRA_MINUTI):
        item.InizioFinestra = now
        item.Tentativi = 0
        item.BloccatoFino = None
    item.Tentativi += 1
    if item.Tentativi >= TENTATIVI_MAX:
        item.BloccatoFino = now + timedelta(minutes=FINESTRA_MINUTI)
    db.session.commit()


def azzera_tentativi(ip: str) -> None:
    item = db.session.get(TentativoLoginCodice, digest_indirizzo(ip))
    if item:
        db.session.delete(item)
        db.session.commit()
