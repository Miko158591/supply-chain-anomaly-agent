# Supply Chain Monitor Skill

供应链异常智能监控系统。异常检测（Z-Score + IQR + 业务规则）→ 模式聚类 → DeepSeek AI 归因 → 飞书推送 + 交互回复。

## 架构

```
CSV → AnomalyDetector → PatternClusterer → AttributionAgent (DeepSeek V4 Flash)
  → 日报卡片（异常模式 + Top 5 高风险）
  → Excel 导出（15,602 条高风险，含 AI 归因）
  ← feishu_webhook.py + ngrok — @机器人 交互命令
```

## 快速上手

### 1. 环境准备

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml  # 填入 DeepSeek API Key + 飞书 app_id/app_secret
```

### 2. 跑一次日报

```bash
# 检测 + 归因 + 推送飞书（默认 15 条归因）
python skills/supply-chain-monitor/monitor.py --mode daily

# 只看不推
python skills/supply-chain-monitor/monitor.py --mode daily --no-notify

# 快速检测（不调 LLM，30 秒跑完）
python skills/supply-chain-monitor/monitor.py --mode quick
```

### 3. 启动交互回复（让机器人识别群里 @它 的命令）

需要两个终端：

**终端 A** — 启动 webhook：
```bash
python skills/supply-chain-monitor/feishu_webhook.py --port 8080
```

**终端 B** — 启动 ngrok 隧道：
```bash
ngrok http 8080
```

然后去飞书开放平台 → 应用 → 事件订阅：
- 请求 URL：`https://{ngrok_url}/feishu/event`
- 订阅事件：`im.message.receive_v1`

### 4. 飞书交互命令

在群里 @机器人：

| 命令 | 效果 |
|------|------|
| `日报` | 输出日报卡片（异常模式 + Top 5 高风险） |
| `全部` / `高风险` | Excel 文件（15,602 条高风险，含 AI 归因列） |
| `中风险` | Excel 文件（32,109 条中风险） |

## 飞书日报卡片格式

```
供应链异常日报 | 2026-05-21

扫描 180,519 笔 | 高风险 15,602 | 中风险 32,109 | 低风险 11,874 | AI 归因 15

【异常模式 · 发现 6 个】

🔴 延迟+亏损复合（269 单）
> 涉及订单: #33, #35, #46, #64, #15
> 269 个订单同时出现交付延迟和财务亏损

🟡 品类集中 — Fishing（1,653 单）
🟡 品类集中 — Cleats（1,183 单）
...

【高风险异常 Top 5】

🔴 #1 订单 33 | HIGH | 置信度 60%
> 原因: 该订单严重亏损源于延迟交付触发的额外成本...
> 建议: 财务分析师复核订单全部费用明细...

（共 5 条）
```

## 项目结构

```
skills/supply-chain-monitor/
├── monitor.py              # 主入口：检测 → 归因 → 推送
├── feishu_webhook.py       # Webhook 服务器：接收飞书回调、处理命令
├── feishu_poll.py          # 备选方案：API 拉取模式（无需 tunnel）
├── message_formatter.py    # 消息格式化（日报卡片 + 交互回复）
├── session_store.py        # SQLite 会话状态 + 命令路由
├── config.json             # Skill 配置（归因数量、调度等）
├── SKILL.md                # OpenClaw Skill 元数据
└── README.md               # 本文档

analysis/
├── anomaly_detector.py     # 异常检测引擎（Z-Score + IQR + MA + 业务规则）
├── attribution_agent.py    # DeepSeek AI 归因 Agent（JSON Schema + 重试）
├── pattern_clusterer.py    # 异常模式聚类（规则法：延迟+亏损/品类/区域）

prompts/
├── attribution_prompt.py   # System prompt + Few-Shot + JSON Schema

knowledge/
├── supply_chain_sop.md     # 10 条供应链异常处置 SOP

config.yaml                 # 实时配置（API key、阈值、聚类参数，不提交到 Git）
config.example.yaml         # 配置模板（可提交）
```

## 配置说明

### config.yaml 核心配置

```yaml
llm:
  # 归因分析（主模型）
  deepseek:
    model: "deepseek-v4-flash"    # 推荐 V4 Flash，max_tokens 至少 4096
    max_tokens: 4096              # 重要：低于 4096 V4 Flash 会被截断
    temperature: 0.3

  # 评测评委（跨模型避免同模型自评，可换成任何 OpenAI 兼容模型）
  judge:
    model: "deepseek-v4-pro"      # 默认 V4 Pro 评 V4 Flash
    # 换成 GPT-4o 等只需改 model + base_url

  # 图像识别（可选，用于未来图表分析，留空跳过）
  vision:
    model: "deepseek-v4-flash"

notify:
  lark:
    app_id: "cli_xxx"            # 飞书应用 App ID
    app_secret: "xxx"            # 飞书应用 Secret
    chat_id: "oc_xxx"            # 目标群聊 ID
    enable_push: true

anomaly:
  zscore:
    threshold: 2.0               # Z-Score 阈值（106 条评测集消融校准）
  consensus_min: 2               # 至少 2 种方法标异常才确认

cluster:
  min_size: 2                    # 最少几条异常算一个模式
  method: "rule_based"           # rule_based（规则）| distance_based（距离）
  enabled_patterns:
    - delay_loss_composite       # 延迟+亏损复合
    - category_concentration     # 品类集中异常
    - region_concentration       # 区域集中异常
```

### 飞书应用权限

应用需要以下权限：
- `im:message` — 读取消息
- `im:message:send_as_bot` — 发送消息（文本 + 卡片 + 文件）
- `im:file` — 上传/发送文件（Excel）

### 模型选择

| 模型 | 用途 | 成功率 | 速度 |
|------|------|--------|------|
| `deepseek-v4-flash` | 归因（主模型） | 100%（4096 tokens） | ~17s/条 |
| `deepseek-v4-pro` | 评测评委（跨版本） | — | ~70s/条 |

归因模型和评委模型均可更换——在 `config.yaml → llm.deepseek` / `llm.judge` 中修改 `model` + `base_url`，支持任何 OpenAI 兼容 API（GPT-4o / Claude / GLM 等）。

## 评测

```bash
# 完整评测（检测 + 评委打分 + 延迟）
python eval/run_eval.py

# 跳过 LLM 评委（省 API 费）
python eval/run_eval.py --skip-llm
```

| 指标 | 值 | 评委 |
|------|-----|------|
| Precision | 64.3% | z=2.0，106 条评测集 |
| Recall | 83.3% | z=2.0，106 条评测集 |
| 检测 F1 | **72.6%** | z=2.0，106 条评测集 |
| 归因均分 | **3.4/5** | V4 Pro（跨版本） |
| 行动可操作性 | 5.0/5 | V4 Pro |
| 诚实度 | 4.8/5 | V4 Pro |
| 端到端延迟 | 95.5s | — |

测试用例：`eval/test_cases.json`（106 条分层样本，含 17 条统计型异常）。

## 命令行参数

```
python monitor.py --mode daily       # 日报模式（默认 15 条归因 + 推送）
python monitor.py --mode full        # 全量归因
python monitor.py --mode quick       # 仅检测，不归因不推送
python monitor.py --max 30           # 归因 30 条
python monitor.py --date 2017-03-04  # 指定日期
python monitor.py --lookback 7       # 回溯 7 天
python monitor.py --no-notify        # 不推送飞书
python monitor.py --export high      # 导出高风险 Excel

python feishu_webhook.py --port 8080 # Webhook 服务器
python feishu_poll.py --interval 10  # API 拉取模式（备选）
```

## 数据格式

### 异常检测输出（8 字段）

| 字段 | 说明 | 示例 |
|------|------|------|
| anomaly_id | 唯一标识 | `zscore_abc123` |
| timestamp | 时间戳 | `2018-01-15` |
| metric | 异常指标 | `Benefit per order` |
| value | 实际值 | `-277.09` |
| expected_range | 正常范围 | `[-239, 283]` |
| severity | 严重程度 | `high` / `medium` / `low` |
| detection_method | 检测方法 | `zscore` / `iqr` / `business_rule` |
| context | 业务上下文 | `{Order Id, Category Name, ...}` |

### 归因报告 JSON

```json
{
  "root_cause_hypotheses": [{
    "cause": "延迟交付触发了运费补贴...",
    "probability": 0.75,
    "evidence": ["订单状态为 Late delivery", "品类均值仅 $8.50"],
    "against": ["上下文中未提供具体运费明细"]
  }],
  "recommended_actions": [{
    "action": "财务分析师复核订单全部费用明细",
    "priority": "high",
    "owner": "财务分析师",
    "sop_ref": "SOP-002"
  }],
  "risk_level": "high",
  "confidence": 0.80,
  "summary": "一句话总结"
}
```

## 故障排查

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| 飞书收不到日报 | enable_push: false 或 app_id 未配置 | 检查 config.yaml |
| 归因大量失败 | max_tokens 过低（V4 Flash 需 ≥4096） | 改 config.yaml llm.deepseek.max_tokens |
| @机器人 不回复 | 事件订阅 URL 未配置或 ngrok 挂了 | 检查 ngrok + 飞书后台 URL |
| 回复两次（重复发文件） | webhook 端口多进程 | `netstat -ano | findstr ":8080"` 杀多余进程 |
| ModuleNotFoundError | 不在项目根目录 | `cd` 到项目根目录再运行 |
| DeepSeek API 401 | API Key 无效或余额不足 | 检查 config.yaml + DeepSeek 账户 |

## 日志

```bash
# 查看监控日志
tail -f logs/monitor.log

# 查看 webhook 日志（debug 端点）
curl http://localhost:8080/debug
```
