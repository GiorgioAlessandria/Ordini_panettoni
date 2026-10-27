"""Diagnostica non distruttiva per la pianificazione con impasti ripetuti.

Eseguire dalla radice del progetto: python diagnostica_fasi_impasto.py
Non accede ai dati degli ordini e non modifica il database.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import os
from pathlib import Path
import sys

ERRORE = "La ricetta deve avere esattamente una fase di impasto"
EXPECTED_SHA256 = {
    "flaskr/routes_modules/ordini.py": "321bae27ef5d",
    "flaskr/service/risorse_produzione_service.py": "e1d0de5c976e",
}


def _fun_has_message(func: object, phrase: str) -> bool:
    code = getattr(func, "__code__", None)
    if code is None:
        return False
    def _search(code):
        for constant in code.co_consts:
            if isinstance(constant, str) and phrase in constant:
                return True
            if hasattr(constant, "co_consts") and _search(constant):
                return True
        return False
    return _search(code)


def _info_funzione(title: str, func: object) -> None:
    if func is None:
        print(f"{title}: funzione non trovata")
        return
    try:
        originale = inspect.unwrap(func)
        print(f"{title}: {originale.__module__}.{originale.__qualname__}")
        print(f"  file: {inspect.getfile(originale)}")
        print(f"  contiene vecchio errore nel codice caricato: {_fun_has_message(originale, ERRORE)}")
    except (OSError, TypeError, ValueError) as exc:
        print(f"{title}: non ispezionabile ({exc})")


def _riepilogo_file(radice: Path) -> None:
    print("\n--- Versione dei file sul disco ---")
    for relative, expected in EXPECTED_SHA256.items():
        percorso = radice / relative
        if not percorso.is_file():
            print(f"{percorso}: NON TROVATO")
            continue
        raw = percorso.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()[:12]
        presente = ERRORE.encode("utf-8") in raw
        print(f"{percorso}")
        print(f"  sha256 (inizio): {checksum} ; file caricato dall'utente: {expected}")
        print(f"  contiene vecchio messaggio nel testo: {presente}")
        if checksum != expected:
            print("  ATTENZIONE: contenuto o terminatori di riga diversi rispetto al file fornito")


def _scan(radice: Path) -> None:
    print(f"\n--- Ricerca del messaggio in {radice} ---")
    esclusi = {".git", ".venv", "venv", "__pycache__", "node_modules"}
    n = 0
    for base, dirs, files in os.walk(radice):
        dirs[:] = [d for d in dirs if d not in esclusi]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = Path(base) / filename
            try:
                contenuto = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for lineno, riga in enumerate(contenuto.splitlines(), start=1):
                if ERRORE in riga:
                    print(f"TROVATO: {path}:{lineno}")
                    n += 1
    if n == 0:
        print("Nessuna occorrenza nei sorgenti Python di questa cartella")


def main() -> None:
    print("=== DIAGNOSTICA FASI DI IMPASTO ===")
    radice = Path(__file__).resolve().parent
    print("Directory attuale:", Path.cwd())
    print("Directory script:", radice)
    print("Python:", sys.executable)
    try:
        app_mod = importlib.import_module("flaskr")
        mod_service = importlib.import_module("flaskr.service.risorse_produzione_service")
        mod_route = importlib.import_module("flaskr.routes_modules.ordini")
        print("Pacchetto flaskr:", getattr(app_mod, "__file__", "?"))
        print("Service importato:", getattr(mod_service, "__file__", "?"))
        print("Route importata:", getattr(mod_route, "__file__", "?"))
        _info_funzione("Service", getattr(mod_service, "individua_fasi_risorse", None))
        _info_funzione("Funzione importata dalla route", getattr(mod_route, "individua_fasi_risorse", None))
        _info_funzione("Conferma pianificazione", getattr(mod_route, "conferma_pianificazione", None))
        print("Stessa funzione chiamata dalla route:",
              getattr(mod_service, "individua_fasi_risorse", None) is getattr(mod_route, "individua_fasi_risorse", None))
        app_dir = Path(app_mod.__file__).resolve().parent.parent
        _riepilogo_file(app_dir)
        _scan(app_dir)
    except Exception as exc:  # diagnostica: identificare precisamente il guasto di startup
        print("Impossibile importare l'applicazione:", type(exc).__name__, str(exc))
        _riepilogo_file(Path.cwd())
        _scan(Path.cwd())
        raise
    print("\nFINE. Se l'errore compare ancora nel browser, arrestare TUTTI i processi Flask/WSGI")
    print("e riavviare quello che serve davvero l'app, anche l'Application Pool IIS se usato.")


if __name__ == "__main__":
    main()
