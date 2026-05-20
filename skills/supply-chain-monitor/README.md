# Supply Chain Monitor Skill

供应链异常智能监控 OpenClaw Skill。每天自动跑，异常检测 → AI 归因 → 飞书推送 + 交互回复。

## 快速开始

```bash
# 1. 日报模式：检测 + 归因 + 推送飞书
python skills/supply-chain-monitor/monitor.py --mode daily --max 5

# 2. 启动消息轮询（让机器人能识别群里的回复命令）
python skills/supply-chain-monitor/feishu_poll.py --interval 10
```

## 飞书交互命令

日报推送后，在群里 @机器人 发送以下命令：

| 命令 | 效果 | 示例 |
|------|------|------|
| `全部` | 列出今日所有已归因异常 | @机器人 全部 |
| `1` ~ `20` | 查看对应编号异常的摘要（Layer 2） | @机器人 3 |
| `详情` | 查看上一次查看的异常的完整报告（Layer 3） | @机器人 详情 |
| `导出` | 提示 Excel 导出命令 | @机器人 导出 |

**消息轮询 (`feishu_poll.py`)** 负责接收这些命令并回复。需后台常驻运行。
前置条件：飞书应用已开通 `im:message.group_msg` 权限。

## 手动测试

```bash
# 快速模式：只检测，不归因，不推送（30 秒跑完）
python skills/supply-chain-monitor/monitor.py --mode quick

# 日报模式：检测 + 归因 top-5 + 推送飞书
python skills/supply-chain-monitor/monitor.py --mode daily --max 5

# 全量模式：检测 + 归因 top-10
python skills/supply-chain-monitor/monitor.py --mode full --max 10

# 指定日期 + 不看飞书
python skills/supply-chain-monitor/monitor.py --date 2017-03-04 --no-notify

# 回溯 3 天
python skills/supply-chain-monitor/monitor.py --lookback 3
```

## 安装到 AutoClaw

### 方式 1：OpenClaw Skills 管理器

```bash
# 在 OpenClaw 项目目录下执行
openclaw skills install /path/to/supply-chain-anomaly-agent/skills/supply-chain-monitor
```

### 方式 2：手动配置

在 OpenClaw 的 `openclaw.json` 中注册 skill：

```json
{
  "skills": {
    "entries": {
      "supply-chain-monitor": {
        "path": "/path/to/supply-chain-anomaly-agent/skills/supply-chain-monitor",
        "enabled": true,
        "schedule": "0 8 * * *"
      }
    }
  }
}
```

### 方式 3：环境变量

```bash
# 设置 DeepSeek API Key（如果不在 config.yaml 中）
export DEEPSEEK_API_KEY="sk-your-key"
```

## 飞书推送效果

推送的卡片消息格式：

```
┌─────────────────────────────────────┐
│ 📦 供应链异常日报 | 2025-05-21     │
├─────────────────────────────────────┤
│ 共检出 59,585 个异常，其中 🔴 高   │
│ 风险 15,602 个                      │
│ AI 归因 4 个 | 数据不足降级 1 个    │
├─────────────────────────────────────┤
│ 1. [HIGH] 订单 #33 严重亏损，建议   │
│    复核折扣叠加和运费明细...         │
│    置信度: 80%                      │
│    建议: 财务分析师复核订单费用明细  │
├─────────────────────────────────────┤
│ 2. [MED] 订单延迟 4 天，可能为仓库  │
│    操作异常...                       │
│    ...                              │
├─────────────────────────────────────┤
│ ⚠️ 1 个异常因数据不足跳过归因       │
├─────────────────────────────────────┤
│ 由 AutoClaw · Supply Chain Monitor  │
│ 自动生成 | 2025-05-21               │
└─────────────────────────────────────┘
```

## 故障排查

### 问题：`ModuleNotFoundError: No module named 'analysis'`

```bash
# 确保在项目根目录运行
cd /path/to/supply-chain-anomaly-agent
python skills/supply-chain-monitor/monitor.py
```

### 问题：飞书收不到消息

1. 检查 `config.yaml` 中 `notify.lark.webhook_url` 是否正确
2. 检查 `notify.lark.enable_push: true`
3. 飞书机器人需要在群里有权限
4. 运行 `python monitor.py --no-notify` 跳过推送，检查日志

### 问题：API 调用失败

1. 确认 `config.yaml` 中 `llm.deepseek.api_key` 已填写
2. 确认 DeepSeek 账户有余额
3. `--mode quick` 可以跳过 API 调用

### 问题：数据文件不存在

确保 `data/raw/DataCoSupplyChainDataset.csv` 已下载（参考项目 README 数据集下载步骤）。

## 日志

日志文件：`logs/monitor.log`

正常运行的日志示例：
```
2025-05-21 08:00:01 [INFO] ===== 供应链异常监控启动 | mode=daily | date=2025-05-21 =====
2025-05-21 08:00:03 [INFO] 数据加载完成: 180,519 行
2025-05-21 08:00:15 [INFO] 检出 59,585 条异常 (high=15,602, medium=32,109, low=11,874)
2025-05-21 08:00:45 [INFO] 归因完成: 4 次 LLM, 1 次降级, 0 次失败
2025-05-21 08:00:46 [INFO] 报告已保存: data/output/daily_report_2025-05-21.json
2025-05-21 08:00:47 [INFO] 飞书推送成功
2025-05-21 08:00:47 [INFO] ===== 完成 | 耗时 46.3s =====
```

## 完整 JSON 报告格式

保存到 `data/output/daily_report_{date}.json`：

```json
{
  "date": "2025-05-21",
  "generated_at": "2025-05-21T08:00:47",
  "stats": {
    "total_anomalies": 59585,
    "severity_counts": {"high": 15602, "medium": 32109, "low": 11874},
    "llm_calls": 4,
    "degraded": 1,
    "errors": 0
  },
  "reports": [...]
}
```
