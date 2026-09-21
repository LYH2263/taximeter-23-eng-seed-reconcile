import json
import sqlite3

from app import check
from app.engines.tariff_breakdown import calc_fare

TARIFF = {"start_price": 11, "start_include_km": 3, "per_km": 2.5, "per_slow_min": 0.8, "night_factor": 1.2}


def _seed(db):
    """与 app.seed 一致的种子：一行运价 + 一条 fare 记录。"""
    conn = sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE tariff(id INTEGER PRIMARY KEY, start_price REAL, start_include_km REAL, per_km REAL, per_slow_min REAL, night_factor REAL);
    CREATE TABLE calc_runs(id INTEGER PRIMARY KEY, kind TEXT, trip_id INTEGER, input_json TEXT, result_json TEXT, created_at TEXT);
    """)
    conn.execute("INSERT INTO tariff(start_price,start_include_km,per_km,per_slow_min,night_factor) VALUES (11,3,2.5,0.8,1.2)")
    r = calc_fare(5, 2, False, TARIFF)
    conn.execute(
        "INSERT INTO calc_runs(kind,trip_id,input_json,result_json,created_at) VALUES ('fare',1,?,?,datetime('now'))",
        (json.dumps({"distance_km": 5, "slow_min": 2, "night": False}), json.dumps(r)),
    )
    conn.commit()
    conn.close()


def _exec(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _failed(results):
    return {name for name, ok, _ in results if not ok}


def test_gate_passes_on_fresh_seed(tmp_path):
    db = tmp_path / "app.db"
    _seed(db)
    assert _failed(check.check_db(db)) == set()


def test_gate_fails_when_night_factor_tampered(tmp_path):
    db = tmp_path / "app.db"
    _seed(db)
    _exec(db, "UPDATE tariff SET night_factor = 1.3")
    assert _failed(check.check_db(db)) == {"night_18km_12min"}


def test_gate_fails_when_seed_total_tampered(tmp_path):
    db = tmp_path / "app.db"
    _seed(db)
    conn = sqlite3.connect(db)
    rid, result_json = conn.execute("SELECT id, result_json FROM calc_runs WHERE kind = 'fare'").fetchone()
    result = json.loads(result_json)
    result["total"] = 18.6
    conn.execute("UPDATE calc_runs SET result_json = ? WHERE id = ?", (json.dumps(result), rid))
    conn.commit()
    conn.close()
    assert _failed(check.check_db(db)) == {"seed_fare_record"}


def test_gate_fails_when_tariff_not_single_row(tmp_path):
    db = tmp_path / "app.db"
    _seed(db)
    _exec(db, "INSERT INTO tariff(start_price,start_include_km,per_km,per_slow_min,night_factor) VALUES (11,3,2.5,0.8,1.2)")
    assert "tariff_single_row" in _failed(check.check_db(db))


def test_gate_passes_again_after_seed_restored(tmp_path):
    db = tmp_path / "app.db"
    _seed(db)
    _exec(db, "UPDATE tariff SET night_factor = 1.3")
    assert _failed(check.check_db(db)) != set()
    _exec(db, "UPDATE tariff SET night_factor = 1.2")
    assert _failed(check.check_db(db)) == set()
