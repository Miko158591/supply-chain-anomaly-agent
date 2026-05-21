# 供应链异常智能监控与归因 Agent — 项目上下文

> 新会话或新开发者打开项目时读此文档即可快速进入状态。

## 项目概况

**目标**：搭建供应链异常智能监控系统，自动执行「数据监控 → 统计异常检测 → 模式聚类 → DeepSeek AI 归因 → 飞书推送」全流程。面向找实习的个人项目。作者 Miko（GitHub: Miko158591）。

**数据**：Kaggle DataCo Smart Supply Chain Dataset（180,519 行 × 53 列，2015-2018），一张大宽表含订单/物流/产品/客户四维度。

**代码**：Python 3.12，项目根目录 `supply-chain-anomaly-agent/`。

## 技术栈

| 层 | 技术 |
|------|------|
| 异常检测 | Z-Score + IQR + 移动平均偏离 + 业务规则（纯统计，不用 ML） |
| 模式聚类 | 规则法（延迟+亏损复合 / 品类集中 / 区域集中） |
| LLM 归因 | DeepSeek API（OpenAI 兼容 SDK），默认 V4 Flash + 4096 tokens |
| Prompt 工程 | Few-Shot（3 示例）+ JSON Schema 校验 + 反模板 + 证据质量铁律 |
| 评测体系 | 89 条分层抽样测试集 + 跨模型评委（V4 Pro 评 V4 Flash） |
| 存储 | SQLite + CSV |
| 推送 | 飞书企业应用 API（app_id + app_secret → 交互卡片 + Excel 文件） |
| CI/CD | GitHub Actions（21 边界测试 + 21 归因测试 + mypy 类型检查） |
| 部署 | Docker 一键启动（docker-compose.yml） |

## 架构概览

```
CSV → AnomalyDetector → PatternClusterer → AttributionAgent(DeepSeek V4 Flash)
  → 飞书日报卡片（异常模式 + Top 5 + ROI）
  → Excel 导出（3 Sheet：异常明细 + 汇总统计 + ROI分析）
  ← feishu_webhook.py + ngrok → @机器人 交互命令
```

**关键模块**：
- `analysis/anomaly_detector.py` — 检测引擎，4 种方法，59,585 条异常/次
- `analysis/pattern_clusterer.py` — 模式聚类（3 种规则 + ROI 估算，潜在挽回 $156K）
- `analysis/attribution_agent.py` — 归因引擎（Schema 校验 + 重试 + context 过滤）
- `analysis/threshold_analysis.py` — 阈值敏感性分析 + PR 曲线生成
- `prompts/attribution_prompt.py` — System prompt + Few-Shot + JSON Schema（v3 版）
- `prompts/good_evidence.json` + `bad_evidence.json` — 证据质量示例
- `knowledge/supply_chain_sop.md` — 10 条供应链异常处置 SOP
- `eval/` — 89 条评测集 + 自动评测脚本 + 报告模板
- `skills/supply-chain-monitor/` — 飞书 Skill 封装
  - `monitor.py` — 主入口
  - `feishu_webhook.py` — 飞书事件回调 + 异步命令处理 + 去重
  - `message_formatter.py` — 日报卡片格式化（模式 + Top 5 + ROI）
  - `session_store.py` — SQLite 会话状态

## 已完成里程碑

| 里程碑 | 核心成果 |
|--------|----------|
| 骨架 + EDA | 目录结构、6 张图表、35,464 条视觉标注 |
| 异常检测引擎 | 4 种方法、21 边界测试、IQR k=2.0 |
| DeepSeek 归因 | AttributionAgent、SOP 知识库、JSON Schema 校验 |
| 飞书推送 + 交互 | Webhook 服务器、@机器人命令、Excel 文件推送 |
| 归因质量优化 | 三版 prompt 迭代、句式多样性 2→4、证据 2.0→2.2 |
| 异常模式聚类 | 3 种规则 + ROI 分析（$156K） |
| 评测体系 | 89 条分层标注 + V4 Pro 评委 + 跨模型对照实验 |
| Docker + CI | Dockerfile/docker-compose + GitHub Actions + mypy |
| PR 曲线 | Z-Score 阈值扫描、docs/images/pr_curve_zscore.png |

## 关键设计决策（ADR）

1. **统计方法 > ML**（ADR-001）：可解释性优先
2. **阈值 2.5 而非 3.0**（ADR-002）：数据驱动推导 + PR 曲线验证
3. **Shipping Mode 不作为预测特征**（ADR-003）：数据泄漏（First Class 是延迟结果非原因）
4. **轻量 RAG 替代向量数据库**（ADR-004）：10 条 SOP 全量注入 prompt
5. **模型无关设计**（ADR-005）：`config.yaml` 改一行换模型，支持任何 OpenAI 兼容 API
6. **规则聚类 > ML 聚类**（ADR-006）：离散业务特征 + 可解释性

详见 `docs/architecture_decisions.md`

## 评测基线

| 指标 | 值 |
|------|-----|
| 检测 F1 | 66.7%（89 条，悲观下界） |
| 归因整体 | 3.4/5（V4 Pro 评委） |
| 证据充分性 | 2.2/5（prompt 触顶，瓶颈在数据粒度） |
| ROI 潜在挽回 | $156,139/年 |

## 当前任务

（留空——每次新会话开始时在此填写）
