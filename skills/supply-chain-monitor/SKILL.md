---
name: supply-chain-monitor
description: 供应链异常智能监控与归因 — 自动检测异常、DeepSeek AI 归因、飞书推送报告
user-invocable: true
metadata: {"openclaw":{"emoji":"📦","requires":{"bins":["python"],"env":["DEEPSEEK_API_KEY"]},"primaryEnv":"DEEPSEEK_API_KEY"}}
---

# 供应链异常智能监控与归因

自动执行「数据加载 → 统计异常检测 → DeepSeek AI 归因分析 → 飞书推送」全流程。

## 触发方式

| 方式 | 触发条件 | 说明 |
|------|----------|------|
| **定时触发** | 每天早上 8:00（OpenClaw cron）| 自动跑昨日数据，推送日报 |
| **手动触发** | 用户在飞书发 "跑一下供应链检查" / "供应链日报" | 即时跑当前数据 |
| **CLI 触发** | `python {baseDir}/monitor.py --mode daily` | 命令行手动执行 |

## 输入

- `data/raw/DataCoSupplyChainDataset.csv` — DataCo 供应链数据集
- `config.yaml` — API Key 和异常检测阈值配置

## 输出

- 飞书卡片消息：异常概览 + Top 5 异常归因 + 处置建议
- 日志文件：`logs/monitor.log`
- 完整报告：`data/output/daily_report_{date}.json`

## 核心依赖

- `analysis/anomaly_detector.py` — 异常检测引擎（Z-Score + IQR + MA + 业务规则）
- `analysis/attribution_agent.py` — DeepSeek AI 归因
- `knowledge/supply_chain_sop.md` — 10 条处置 SOP

## 参数

可通过 `{baseDir}/config.json` 或 OpenClaw 调用时传入：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `daily` | `daily`（日报）/ `full`（全量）/ `quick`（只检测不归因）|
| `lookback_days` | 1 | 回溯天数（daily 模式查最近 N 天）|
| `max_anomalies` | 5 | 飞书推送中展示的异常数量上限 |
| `notify` | true | 是否推送飞书 |

## 故障排查

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| 飞书收不到消息 | webhook URL 未配置或过期 | 检查 `config.yaml` 中 `notify.lark.webhook_url` |
| 归因全部跳过 | DeepSeek API Key 未配置 | 检查 `config.yaml` 中 `llm.deepseek.api_key` |
| 检测不到异常 | 数据文件缺失 | 确认 `data/raw/DataCoSupplyChainDataset.csv` 存在 |
| 日志报错 | Python 依赖缺失 | `pip install -r requirements.txt` |
