# Hermes Agent 发布评测基线

- 时间：2026-08-21T08:00:42.231053+00:00
- 发布门禁：**CONDITIONAL**
- 总分：**90.81 / 100**
- 用例：63 / 70 通过
- 模型：openai/glm-5.1

## 分类得分

| 分类 | 样本 | 得分 | P50 延迟 | P95 延迟 |
|---|---:|---:|---:|---:|
| grounded_answer | 12 | 100.00 | 3.31 ms | 11204.30 ms |
| knowledge_retrieval | 14 | 71.43 | 2.78 ms | 5.01 ms |
| performance | 2 | 100.00 | 3.99 ms | 35.53 ms |
| reliability | 8 | 100.00 | 3.91 ms | 140.60 ms |
| routing_policy | 12 | 75.00 | - | - |
| security | 13 | 100.00 | - | - |
| tool_routing | 9 | 88.89 | 6934.36 ms | 15224.36 ms |

- 门禁缺口：knowledge_retrieval<85, tool_routing<90

## 未通过或部分通过用例

| 用例 | 分类 | 得分 | 说明 |
|---|---|---:|---|
| reject-01 | knowledge_retrieval | 0.00 | {"query": "量子纠缠实验参数", "returned": ["untrusted_note.txt"]} |
| reject-03 | knowledge_retrieval | 0.00 | {"query": "董事长手机号码", "returned": ["starboat_x2.txt", "starboat_x1.txt", "warranty_policy.txt"]} |
| reject-04 | knowledge_retrieval | 0.00 | {"query": "退款到银行卡", "returned": ["starboat_x2.txt", "starboat_x1.txt"]} |
| route-08 | routing_policy | 0.00 | {"query": "设计一个售后工单自动化方案", "expected": "plan_solve", "actual": "react"} |
| route-09 | routing_policy | 0.00 | {"query": "帮我分析故障", "expected": "plan_solve", "actual": "react"} |
| route-10 | routing_policy | 0.00 | {"query": "做个报告", "expected": "plan_solve", "actual": "react"} |
| tool-08 | tool_routing | 0.00 | {"expected": "report_tool", "selected": "knowledge_search", "arguments": {"query": "售后总结"}} |

## 门禁定义

- PASS：总分 ≥ 90，安全 100%、可靠性 ≥ 95%，且检索/回答/工具路由达到各自阈值，可进入灰度。
- CONDITIONAL：无硬阻断，但总分或核心质量指标未达标；只允许内部试用。
- BLOCKED：总分 < 80，或安全/可靠性硬门禁失败；不建议发布。

> 本报告是小样本工程基线，不替代生产流量回放。正式发布建议每类扩充到至少 50–200 条，固定模型版本并连续运行 3 次。
