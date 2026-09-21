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

## 种子对账门禁

compose 起来之后，随时可重复执行（只读检查，不改库、不改应付数字）：

```bash
docker compose exec backend python -m app.seed_gate
```

检查项：库中运价恰好一行；用库中运价现算白天 5 公里 2 分钟应付 17.6、夜间 18 公里 12 分钟应付 69.72；种子写入的 fare 记录应付与现算一致；`/api/health` 返回 ok 且项目标识为 taximeter。

全部通过退出码为 0；任一条件失败退出码非 0，且只有失败那一项会打到标准错误。API 地址默认 `http://127.0.0.1:9300`，可用环境变量 `TAXIMETER_API_BASE` 覆盖。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。
