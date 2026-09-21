"""种子记录对账门禁（只读检查，可重复调用）。

compose 起来之后执行：

    docker compose exec backend python -m app.seed_gate

检查项：
  tariff_single_row   库中运价恰好一行
  day_fare_recalc     用库中运价现算：白天 5 公里 2 分钟应付 17.6
  night_fare_recalc   用库中运价现算：夜间 18 公里 12 分钟应付 69.72
  seed_fare_record    种子写入的 fare 记录，其应付与现算一致
  health_ok           /api/health 返回 ok 且项目标识为 taximeter

全部通过退出码 0；任一失败退出码 1，且只有失败项会打到标准错误。
本模块以 sqlite 只读模式打开数据库，只查不写，绝不改应付数字。
"""

import json
import os
import sqlite3
import sys
import urllib.request

from app.config import DATA_DIR, DB_FILENAME
from app.engines.tariff_breakdown import calc_fare

EXPECTED_DAY_TOTAL = 17.6     # 白天 5 公里 2 分钟
EXPECTED_NIGHT_TOTAL = 69.72  # 夜间 18 公里 12 分钟
DEFAULT_API_BASE = "http://127.0.0.1:9300"

_EPS = 1e-9


def _connect_ro(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _active_tariff(conn):
    row = conn.execute("SELECT * FROM tariff ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row else None


def check_tariff_single_row(conn):
    n = conn.execute("SELECT COUNT(*) AS c FROM tariff").fetchone()["c"]
    if n != 1:
        return f"tariff 表行数为 {n}，须恰好 1 行"
    return ""


def check_day_fare_recalc(conn):
    t = _active_tariff(conn)
    if not t:
        return "tariff 表为空，无法现算"
    total = calc_fare(5, 2, False, t)["total"]
    if abs(total - EXPECTED_DAY_TOTAL) > _EPS:
        return f"白天 5 公里 2 分钟现算应付 {total}，须为 {EXPECTED_DAY_TOTAL}"
    return ""


def check_night_fare_recalc(conn):
    t = _active_tariff(conn)
    if not t:
        return "tariff 表为空，无法现算"
    total = calc_fare(18, 12, True, t)["total"]
    if abs(total - EXPECTED_NIGHT_TOTAL) > _EPS:
        return f"夜间 18 公里 12 分钟现算应付 {total}，须为 {EXPECTED_NIGHT_TOTAL}"
    return ""


def check_seed_fare_record(conn):
    row = conn.execute(
        "SELECT result_json FROM calc_runs WHERE kind='fare' AND trip_id=1 ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return "未找到种子写入的 fare 记录（calc_runs kind=fare trip_id=1）"
    try:
        stored = float(json.loads(row["result_json"])["total"])
    except (KeyError, TypeError, ValueError):
        return f"种子 fare 记录结果无法解析: {row['result_json']}"
    t = _active_tariff(conn)
    if not t:
        return "tariff 表为空，无法现算比对"
    expect = calc_fare(5, 2, False, t)["total"]
    if abs(stored - expect) > _EPS:
        return f"种子 fare 记录应付 {stored} 与现算 {expect} 不一致"
    return ""


def check_health_ok(api_base):
    url = api_base.rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return f"请求 {url} 失败: {exc}"
    if payload.get("ok") is not True or payload.get("project") != "taximeter":
        return f"{url} 返回异常: {payload}"
    return ""


def run_checks(db_path=None, api_base=DEFAULT_API_BASE):
    """返回 [(检查名, 失败原因)]，原因空串表示通过。api_base 为空串则跳过 health。"""
    db_path = db_path or (DATA_DIR / DB_FILENAME)
    db_checks = [
        ("tariff_single_row", check_tariff_single_row),
        ("day_fare_recalc", check_day_fare_recalc),
        ("night_fare_recalc", check_night_fare_recalc),
        ("seed_fare_record", check_seed_fare_record),
    ]
    try:
        conn = _connect_ro(db_path)
    except sqlite3.Error as exc:
        return [("db_open", f"无法只读打开数据库 {db_path}: {exc}")]
    results = []
    try:
        for name, fn in db_checks:
            try:
                results.append((name, fn(conn)))
            except sqlite3.Error as exc:
                results.append((name, f"查询失败: {exc}"))
    finally:
        conn.close()
    if api_base:
        results.append(("health_ok", check_health_ok(api_base)))
    return results


def main():
    api_base = os.environ.get("TAXIMETER_API_BASE", DEFAULT_API_BASE)
    results = run_checks(api_base=api_base)
    failed = False
    for name, problem in results:
        if problem:
            print(f"FAIL {name}: {problem}", file=sys.stderr)
            failed = True
        else:
            print(f"OK   {name}")
    if failed:
        return 1
    print("seed_gate: 全部检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
