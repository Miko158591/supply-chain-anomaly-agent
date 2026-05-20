# 供应链异常智能监控与归因 Agent — 项目上下文

> 新会话开始时读此文档即可快速进入状态。保持 1500 字以内。

## 项目概况

**目标**：搭建供应链异常智能监控系统，自动执行「数据监控 → 统计异常检测 → DeepSeek AI 归因 → 飞书推送」全流程。面向找实习的个人项目。作者 WANG Chuncheng（GitHub: Miko158591）。

**数据**：Kaggle DataCo Smart Supply Chain Dataset（180,519 行 × 53 列，2015-2018），一张大宽表含订单/物流/产品/客户四维度。

**代码**：`D:/OneDrive/Desktop/supply product/`，Python 3.12，已部署到 AutoClaw（`~/.openclaw-autoclaw/skills/supply-chain-monitor/`）。

## 技术栈

| 层 | 技术 |
|------|------|
| 异常检测 | Z-Score + IQR + 移动平均偏离 + 业务规则（纯统计，不用 ML） |
| LLM 归因 | DeepSeek API（OpenAI 兼容 SDK）+ MiniMax API |
| Prompt 工程 | Few-Shot（3 示例）+ JSON Schema 校验 + 防幻觉铁律 |
| Agent 框架 | OpenClaw / AutoClaw（Skill 机制） |
| 存储 | SQLite + CSV |
| 推送 | 飞书企业应用 API（app_id + app_secret → tenant_access_token） |
| 可视化 | matplotlib + plotly |

## 架构概览

```
CSV → AnomalyDetector(Z-Score+IQR+MA+规则) → AttributionAgent(DeepSeek+SOP+Few-Shot)
  → 飞书三层消息(L1日报/L2摘要/L3详情) + JSON报告 + Excel导出
  ← feishu_poll.py 拉取群消息 → handle_message() → 文本回复
```

**关键模块**：
- `analysis/anomaly_detector.py` — 检测引擎，输出 8 字段标准化异常记录
- `analysis/attribution_agent.py` — 归因引擎，含数据充分性检查 + Schema 校验 + 重试
- `prompts/attribution_prompt.py` — System prompt + Few-Shot 示例 + JSON Schema
- `knowledge/supply_chain_sop.md` — 10 条供应链异常处置 SOP
- `skills/supply-chain-monitor/` — OpenClaw Skill 封装
  - `monitor.py` — 主入口（检测 → 归因 → 推送）
  - `message_formatter.py` — 三层消息格式（L1 日报 / L2 摘要 / L3 详情）
  - `session_store.py` — SQLite 会话状态 + `handle_message()` 命令路由
  - `feishu_poll.py` — API 拉取模式（轮询群消息，识别命令并回复，无需 tunnel）
  - `feishu_webhook.py` — Webhook 模式（备用，需公网 URL/tunnel）

## 已完成里程碑

| 里程碑 | 关键 Commit | 核心成果 |
|--------|------------|----------|
| 骨架 + EDA | `db037e9` | 目录结构、6 张图表、35,464 条视觉标注 |
| 异常检测引擎 | `db037e9`→`d3b6831` | 4 种方法、21 边界测试、偏差度字段、IQR k=2.0 |
| DeepSeek 归因 | `1ee26d6` | AttributionAgent、SOP 知识库、JSON Schema 校验 |
| Few-Shot + 数据检查 | `a01c085`, `4d79313` | 置信度 53%→65%、daily 异常自动降级跳过 LLM |
| Skill + 飞书推送 | `bddd831`→`fdafd72` | OpenClaw 封装、飞书企业应用推送、Excel 导出 |
| 三层消息 | `7f4af53` | L1 日报/L2 摘要/L3 详情、SQLite 会话状态 |

## 关键设计决策

1. **统计方法 > ML**（ADR-001）：供应链监控需要可解释性，Z-Score 告诉运营"偏离均值 X 倍"，Isolation Forest 只能说"分数 0.73"。纯统计零训练成本。
2. **阈值 2.5 而非 3.0**（ADR-002）：基于 DataCo 实际分位数推算。z=3.0 只能捕获 0.5% 订单，z=2.5 覆盖 ~2.5%。
3. **Shipping Mode 不作为预测特征**（ADR-003）：First Class 延迟率 95.3% 是因（延迟后升级运输）而非果。使用会数据泄漏。
4. **Precision > Recall**：宁可漏检不要误报。visual_anomalies.csv 不完备（仅 4 种规则型标注），统计方法的"误报"大多是真实异常。
5. **shipping_delay_days 不走统计方法**：值域 -2~4 的离散分布，IQR/Z-Score 不适用，只走业务规则。
6. **归因前先检查数据充分性**：daily 聚合级异常天然缺单点上下文，跳过 LLM 省 API 费、避免硬编。
7. **飞书消息三层信息密度**：L1 3 秒扫完（<500 字）→ L2 10 秒读懂（<150 字）→ L3 完整报告。会话状态 SQLite 30 分钟过期。

## 已知问题

| 优先级 | 问题 | 状态 |
|--------|------|------|
| P1 | 飞书回复"1"/"详情"/"全部"命令识别 | 已解决（feishu_poll.py API 拉取模式，无需隧道） |
| P2 | 日报卡片中"全部"清单 Order Id 显示 #?（format_all_anomalies 的 context 读取路径待确认） | 跟踪中 |
| P3 | 退出码 128 TLS 偶发错误，重试可恢复 | 偶发 |
| P3 | Windows 终端 GBK 编码导致 emoji 输出崩溃（已用 ASCII 替代） | 已解决 |

## 代码风格

- Python 3.10+，所有函数有 type hints 和 docstring
- 配置在 `config.yaml`，阈值不硬编码。`config.example.yaml` 是提交模板
- 日志用 `logging` 不用 `print`；注释用中文
- 测试文件在 `tests/`，命名 `test_*.py`
- 模块化：每个文件单一职责

## 当前任务

（留空——每次新会话开始时在此填写）
