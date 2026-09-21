import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import seed_gate
from app.engines.tariff_breakdown import calc_fare

TARIFF = {"start_price": 11, "start_include_km": 3, "per_km": 2.5, "per_slow_min": 0.8, "night_factor": 1.2}
SEED_TOTAL = calc_fare(5, 2, False, TARIFF)["total"]  # 17.6


def _make_db(tmp_path, *, tariff_rows=None, seed_total=SEED_TOTAL):
    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE tariff(id INTEGER PRIMARY KEY, start_price REAL, start_include_km REAL, per_km REAL, per_slow_min REAL, night_factor REAL);
    CREATE TABLE calc_runs(id INTEGER PRIMARY KEY, kind TEXT, trip_id INTEGER, input_json TEXT, result_json TEXT, created_at TEXT);
    """)
    for t in (tariff_rows if tariff_rows is not None else [TARIFF]):
        conn.execute(
            "INSERT INTO tariff(start_price,start_include_km,per_km,per_slow_min,night_factor) VALUES (?,?,?,?,?)",
            (t["start_price"], t["start_include_km"], t["per_km"], t["per_slow_min"], t["night_factor"]),
        )
    if seed_total is not None:
        conn.execute(
            "INSERT INTO calc_runs(kind,trip_id,input_json,result_json,created_at) VALUES ('fare',1,?,?,datetime('now'))",
            (json.dumps({"distance_km": 5, "slow_min": 2, "night": False}), json.dumps({"total": seed_total})),
        )
    conn.commit()
    conn.close()
    return path


def _failures(results):
    return {name: problem for name, problem in results if problem}


def _run(db_path):
    return seed_gate.run_checks(db_path=db_path, api_base="")


def test_gate_passes_on_fresh_seed(tmp_path):
    assert _failures(_run(_make_db(tmp_path))) == {}


def test_gate_fails_when_night_factor_tampered(tmp_path):
    bad = {**TARIFF, "night_factor": 1.5}
    fails = _failures(_run(_make_db(tmp_path, tariff_rows=[bad])))
    assert "night_fare_recalc" in fails
    # 白天现算与种子记录不受夜间系数影响，不应误报
    assert "day_fare_recalc" not in fails
    assert "seed_fare_record" not in fails


def test_gate_fails_when_seed_record_total_tampered(tmp_path):
    fails = _failures(_run(_make_db(tmp_path, seed_total=18.0)))
    assert list(fails) == ["seed_fare_record"]


def test_gate_fails_when_tariff_not_single_row(tmp_path):
    fails = _failures(_run(_make_db(tmp_path, tariff_rows=[TARIFF, TARIFF])))
    assert "tariff_single_row" in fails


def test_gate_fails_when_tariff_empty(tmp_path):
    fails = _failures(_run(_make_db(tmp_path, tariff_rows=[])))
    assert "tariff_single_row" in fails
    assert "day_fare_recalc" in fails
    assert "night_fare_recalc" in fails


def test_gate_recovers_after_seed_restored(tmp_path):
    bad = {**TARIFF, "night_factor": 1.5}
    path = _make_db(tmp_path, tariff_rows=[bad], seed_total=18.0)
    assert _failures(_run(path))
    # 人为恢复种子：夜间系数与种子记录应付改回正确值
    conn = sqlite3.connect(path)
    conn.execute("UPDATE tariff SET night_factor=?", (TARIFF["night_factor"],))
    conn.execute(
        "UPDATE calc_runs SET result_json=? WHERE kind='fare' AND trip_id=1",
        (json.dumps({"total": SEED_TOTAL}),),
    )
    conn.commit()
    conn.close()
    assert _failures(_run(path)) == {}


class _HealthHandler(BaseHTTPRequestHandler):
    payload = {"ok": True, "project": "taximeter"}

    def do_GET(self):
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve(payload):
    handler = type("H", (_HealthHandler,), {"payload": payload})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_health_check_ok():
    srv = _serve({"ok": True, "project": "taximeter"})
    try:
        assert seed_gate.check_health_ok(f"http://127.0.0.1:{srv.server_port}") == ""
    finally:
        srv.shutdown()


def test_health_check_fails_on_wrong_project():
    srv = _serve({"ok": True, "project": "other"})
    try:
        assert seed_gate.check_health_ok(f"http://127.0.0.1:{srv.server_port}") != ""
    finally:
        srv.shutdown()


def test_health_check_fails_when_unreachable():
    srv = _serve({"ok": True, "project": "taximeter"})
    port = srv.server_port
    srv.shutdown()
    assert seed_gate.check_health_ok(f"http://127.0.0.1:{port}") != ""
