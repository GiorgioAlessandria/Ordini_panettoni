"""Controlli SQLite indipendenti da Flask; non sostituiscono staging IIS."""
from pathlib import Path
import sqlite3
import pytest

MIGRAZIONE = Path(__file__).resolve().parents[1] / "migrazioni" / "003_consuntivi_produzione.sql"

def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(MIGRAZIONE.read_text(encoding="utf-8"))
    return conn

def test_tabelle_create():
    db = _db()
    t = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"consuntivi_produzione_articoli", "consuntivi_produzione_ordini",
            "giacenze_seconda_scelta", "movimenti_stock_consuntivi"} <= t

def test_quantita_non_negative():
    db = _db()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO giacenze_seconda_scelta VALUES ('A', -1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO consuntivi_produzione_ordini VALUES ('TRAD',1,2,3,'A',500,-1,NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO consuntivi_produzione_articoli VALUES ('MISTA',1,'A',0,-1,0,NULL,'test','2026-11-04')")

def test_prodotti_possono_superare_o_essere_inferiori_al_pianificato():
    db = _db()
    db.execute("INSERT INTO consuntivi_produzione_ordini VALUES ('TRAD',1,2,3,'A',500,700,NULL)")
    db.execute("INSERT INTO consuntivi_produzione_ordini VALUES ('MISTA',2,2,4,'B',500,0,NULL)")
    assert db.execute('SELECT SUM("QtaProdotta") FROM consuntivi_produzione_ordini').fetchone()[0] == 700

def test_no_double_registration():
    db = _db()
    q = "INSERT INTO consuntivi_produzione_articoli VALUES ('MISTA',1,'A',0,20,0,'LOT','test','2026-11-04')"
    db.execute(q)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(q)

def test_non_tocca_erp():
    db = _db()
    t = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ordini_cliente_aperti" not in t
