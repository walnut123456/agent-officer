# Agent 评测

离线发布门禁（不调用外部模型）：

```powershell
.\.venv\Scripts\python.exe evals\agent_release_eval.py --mode offline
```

完整基线（调用 `.env` 中的 `CHAT_MODEL`，并测量本地 1601 服务）：

```powershell
.\.venv\Scripts\python.exe evals\agent_release_eval.py --mode all
```

完整评测并把批次与用例明细写入 `.env` 指向的 MySQL：

```powershell
.\.venv\Scripts\python.exe evals\agent_release_eval.py --mode all --persist-db
```

结果写入 `evals/results/latest.json` 和 `latest.md`。评测涵盖知识检索、带引用回答、
Agent 路由、真实模型工具选择、SSRF/越权/路径逃逸、持久化、错误协议和 p50/p95。

评测数据写入独立的临时 SQLite 数据库，不会修改 `.env` 指向的 MySQL 业务库。在线模式
会读取 `CHAT_MODEL` 并产生真实模型调用。只有显式传入 `--persist-db` 时，最终报告才会写入
MySQL 的 `agent_eval_run` 和 `agent_eval_case`；知识库夹具仍留在临时数据库。

查看最近批次：

```sql
SELECT run_id, generated_at, model_name, gate, overall_score, passed_count, case_count
FROM agent_eval_run
ORDER BY id DESC
LIMIT 20;
```

查看某批次失败项：

```sql
SELECT case_id, category, score, latency_ms, details
FROM agent_eval_case
WHERE run_id = '替换成批次ID' AND passed = 0
ORDER BY category, case_id;
```

发布门禁：总分至少 90、安全 100%、可靠性至少 95%，并且知识检索至少 85%、有依据
回答至少 85%、真实工具路由至少 90%，才可进入灰度。无安全/可靠性硬阻断但核心质量
指标未达标时为 `CONDITIONAL`，只限内部试用；总分低于 80 或硬门禁失败时为 `BLOCKED`。
