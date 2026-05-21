# 供应链异常智能监控与归因 Agent — 项目上下文

> 新会话或新开发者打开项目时读此文档即可快速进入状态。

## 项目概况

**作者**：Miko（GitHub: Miko158591）  
**目标**：供应链异常智能监控系统，自动执行「CSV 加载 → 统计+业务规则检测 → 模式聚类 → DeepSeek AI 归因 → 飞书推送 + 交互回复」全流程。面向找实习的个人项目。  
**数据**：Kaggle DataCo Smart Supply Chain Dataset（180,519 行 × 53 列，2015-2018），静态数据集；支持替换为任何同 schema CSV。  
**代码**：Python 3.12，项目根目录 `supply-chain-anomaly-agent/`。

## 架构概览

```
CSV → AnomalyDetector（记录级业务规则 + 日聚合移动平均）
    → PatternClusterer（规则法：延迟+亏损 / 品类集中 / 区域集中）
    → AttributionAgent（DeepSeek V4 Flash, max_tokens=4096）
    → 飞书日报卡片（异常模式 + Top 5 高风险 + ROI）
    → Excel 导出（3 Sheet：异常明细 + 汇总统计 + ROI分析）
    ← feishu_webhook.py + ngrok → @机器人 交互命令
```

**核心模块**：
- `analysis/anomaly_detector.py` — 检测引擎（记录级 + 日聚合双粒度），61,678 条/次
- `analysis/pattern_clusterer.py` — 模式聚类（3 规则 + ROI 估算，$156K）
- `analysis/attribution_agent.py` — 归因引擎（Schema 校验 + 3 次重试 + context 按 metric 过滤）
- `analysis/threshold_analysis.py` — 消融实验 + 阈值自动校准（`--calibrate` 自动写 config）
- `prompts/attribution_prompt.py` — System prompt + Few-Shot + 证据铁律（v3）
- `eval/` — 106 条评测集 + 自动评测脚本 + 报告模板
- `skills/supply-chain-monitor/` — 飞书 Skill（monitor + webhook + formatter + session）
- `docs/architecture_decisions.md` — 6 条 ADR

## 关键数字

| 指标 | 值 | 说明 |
|------|-----|------|
| z 值 | **2.0** | 消融验证最优整数（P/R/F1 均优于 2.5） |
| 检测 F1 | 72.6% | z=2.0 下预计（z=2.5 旧基线 67.2%） |
| Precision | 64.3% | z=2.0 |
| Recall | 83.3% | z=2.0 |
| 归因整体 | 3.4/5 | V4 Pro 评委（跨版本） |
| 证据充分性 | 2.2/5 | prompt 天花板，瓶颈在数据粒度 |
| ROI | $156,139/年 | 每次 pipeline 重跑重新计算 |
| 测试 | 26/26 通过 | CI + GitHub Actions |
| 评测集 | 106 条 | 89 业务异常 + 17 统计型异常 |
| z 校准 | `--calibrate` | 自动分析新数据 → 输出候选表 → 写 config |

## 设计决策摘要

1. **双粒度检测**（ADR-001 修订）：记录级业务规则（主）+ 日聚合统计（趋势补充）。消融实验证明互补在维度层而非算法层
2. **z=2.0 数据驱动**（ADR-002）：基于消融实验选出最优整数（原 z=2.5 经校准下调），支持 `--calibrate` 自动校准
3. **Shipping Mode 数据泄漏**（ADR-003）：First Class 是延迟结果非原因
4. **轻量 RAG**（ADR-004）：10 条 SOP 全量注入，不引入向量数据库
5. **模型无关**（ADR-005）：改 config.yaml 一行换模型
6. **规则聚类**（ADR-006）：业务语义 > 数学距离

## 消融实验核心发现

Z-Score/IQR 在 106 条评测集上独立 F1=0%。检测主力是业务规则（单独 F1=64.2%），日聚合移动平均贡献 +11pp F1 增量。记录级 Z-Score/IQR 作为分布探测 safety net 保留。详见 `docs/images/pr_curve_ablation.png`。

## 快速开始

```bash
cp config.example.yaml config.yaml   # 填入 DeepSeek API Key + 飞书 app_id/secret
pip install -r requirements.txt
python skills/supply-chain-monitor/monitor.py --mode daily   # 跑一次日报

# 接入新数据后自动校准阈值
python analysis/threshold_analysis.py --calibrate

# 启动 webhook（@机器人 交互）
python skills/supply-chain-monitor/feishu_webhook.py --port 8080
ngrok http 8080
```

## 当前任务

（留空——每次新会话开始时在此填写）
