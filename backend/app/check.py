"""种子记录对账门禁（只读检查，可重复调用）。

核对条件：
  1. tariff 表恰好一行；
  2. 用库中运价现算：白天 5 公里 2 分钟应付 17.6，夜间 18 公里 12 分钟应付 69.72；
  3. 种子写入的那条 fare 记录，其应付与现算结果一致；
  4. /api/health 返回 ok 且项目标识为 taximeter。

全部通过退出码为 0；任一条件失败退出码为 1，且 stderr 只列出失败项。
数据库以只读模式打开，检查过程不写库、不改任何应付数字。

用法（compose 起来之后）：
  docker compose exec backend python -m app.check
"""

import json
import os
import sqlite3
import sys
import urllib.request

from app.config import DATA_DIR, DB_FILENAME
from app.engines.tariff_breakdown import calc_fare

DB_PATH = DATA_DIR / DB_FILENAME
API_BASE = os.environ.get("API_BASE", "http://localhost:9300")

# 固定期望：白天 5km/2min = 17.6，夜间 18km/12min = 69.72
FARE_CASES = (
    ("day_5km_2min", 5, 2, False, 17.6),
    ("night_18km_12min", 18, 12, True, 69.72),
)


def _same_money(a, b) -> bool:
    return abs(float(a) - float(b)) < 1e-9


def check_db(db_path=DB_PATH):
    """只读打开数据库，返回 [(name, ok, detail), ...]，绝不写库。"""
    results = []
    add = lambda name, ok, detail="": results.append((name, ok, detail))

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        add("db_readonly_open", False, f"{db_path}: {exc}")
        for name, *_ in FARE_CASES:
            add(name, False, "database unreadable")
        add("seed_fare_record", False, "database unreadable")
        return results

    try:
        try:
            tariffs = [dict(r) for r in conn.execute("SELECT * FROM tariff ORDER BY id")]
            tariff_err = None
        except sqlite3.Error as exc:
            tariffs, tariff_err = None, str(exc)
        try:
            row = conn.execute(
                "SELECT id, result_json FROM calc_runs WHERE kind = 'fare' ORDER BY id LIMIT 1"
            ).fetchone()
            seed_row, seed_err = (dict(row) if row is not None else None), None
        except sqlite3.Error as exc:
            seed_row, seed_err = None, str(exc)
    finally:
        conn.close()

    # 1. 运价表必须恰好一行
    if tariff_err:
        add("tariff_single_row", False, tariff_err)
    else:
        add("tariff_single_row", len(tariffs) == 1,
            f"expected exactly 1 tariff row, found {len(tariffs)}")
    tariff = tariffs[0] if tariffs and len(tariffs) == 1 else None

    # 2. 用库中运价现算白天/夜间应付
    day_total = None
    for name, km, minutes, night, expect in FARE_CASES:
        if tariff is None:
            add(name, False, "no single tariff row to recompute from")
            continue
        total = calc_fare(km, minutes, night, tariff)["total"]
        if not night:
            day_total = total
        add(name, _same_money(total, expect),
            f"recomputed {total}, expected {expect}")

    # 3. 种子 fare 记录的应付必须与现算一致
    if seed_err:
        add("seed_fare_record", False, seed_err)
    elif seed_row is None:
        add("seed_fare_record", False, "no fare run found in calc_runs")
    elif day_total is None:
        add("seed_fare_record", False, "no tariff to recompute the day fare against")
    else:
        try:
            stored = json.loads(seed_row["result_json"])["total"]
            add("seed_fare_record", _same_money(stored, day_total),
                f"stored total {stored} != recomputed {day_total} (calc_runs id {seed_row['id']})")
        except (ValueError, KeyError, TypeError) as exc:
            add("seed_fare_record", False,
                f"unreadable result_json on calc_runs id {seed_row['id']}: {exc}")

    return results


def check_health(api_base=API_BASE):
    """health 必须为 ok 且项目标识为 taximeter。"""
    url = f"{api_base}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read().decode())
        ok = body.get("ok") is True and body.get("project") == "taximeter"
        return [("health_taximeter", ok, f"{url} -> {body}")]
    except Exception as exc:
        return [("health_taximeter", False, f"{url}: {exc}")]


def run(db_path=DB_PATH, api_base=API_BASE):
    return check_db(db_path) + check_health(api_base)


def main() -> int:
    results = run()
    for name, ok, _detail in results:
        if ok:
            print(f"ok   {name}")
    failed = [(name, detail) for name, ok, detail in results if not ok]
    for name, detail in failed:
        print(f"FAIL {name}: {detail}", file=sys.stderr)
    if failed:
        return 1
    print(f"all {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
