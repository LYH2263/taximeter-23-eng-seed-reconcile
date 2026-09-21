# 14-taximeter（打车计价）

Taximeter — 起步价 + 里程价 + 低速时长费（夜间加价系数）

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:4300 |
| API | http://localhost:9300 |

## 主链

录行程里程与低速时长 → 拆解车费 → 行程单

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 种子对账门禁

compose 起来之后，可随时重复执行（只读检查，不写库）：

```bash
docker compose exec backend python -m app.check
```

核对条件：

1. `tariff` 表恰好一行；
2. 用库中运价现算：白天 5 公里 2 分钟应付 17.6，夜间 18 公里 12 分钟应付 69.72；
3. 种子写入的那条 fare 记录，其应付与现算结果一致；
4. `/api/health` 返回 ok 且项目标识为 `taximeter`。

全部通过时退出码为 0；任一条件失败时退出码为 1，且 stderr 只列出失败的那一条。

人为改库可验证门禁生效，例如改夜间系数或种子记录的应付：

```bash
docker compose exec backend python -c "import sqlite3; c = sqlite3.connect('/data/app.db'); c.execute('UPDATE tariff SET night_factor = 1.3'); c.commit()"
docker compose exec backend python -m app.check   # 退出码 1，stderr 指出 night_18km_12min
```

恢复种子（清空数据卷后重启即重新播种），再跑检查即恢复通过：

```bash
docker compose down -v && docker compose up --build -d
docker compose exec backend python -m app.check   # all 5 checks passed
```
