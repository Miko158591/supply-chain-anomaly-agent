# 供应链异常智能监控与归因 Agent

> **一句话**：自动监控供应链数据 → 统计方法检测异常 → DeepSeek AI 归因分析 → 飞书实时推送。
>
> 工业工程专业个人开发项目。

<p align="center">
  <img src="docs/images/eda_delivery_distribution.png" alt="交付状态分布" width="48%">
  <img src="docs/images/eda_shipping_mode.png" alt="运输方式 vs 延迟率" width="48%">
  <br>
  <em>左：54.8% 的订单延迟交付 | 右：First Class 运输延迟率 95.3%（反直觉发现）</em>
</p>

---

## 架构概览

```
  [定时调度 / 飞书触发]
           │
           ▼
  ┌─────────────────────┐
  │   AnomalyDetector    │
  │  Z-Score + IQR      │──── 异常列表 ────┐
  │  + 移动平均         │                  │
  │  + 业务规则         │                  ▼
  └─────────────────────┘     ┌─────────────────────┐
           │                  │  AttributionAgent    │
           ▼                  │   DeepSeek API       │
  ┌─────────────────────┐     │   + SOP 知识库       │
  │   飞书卡片推送       │◀────│   + Few-Shot        │
  │   Top N + 建议       │     └─────────────────────┘
  └─────────────────────┘              │
           │                           ▼
           ▼                  ┌─────────────────────┐
  ┌─────────────────────┐     │   JSON 报告          │
  │   Excel 导出         │     │   + Excel 导出       │
  │   --export high      │     └─────────────────────┘
  └─────────────────────┘
```

数据流: CSV → AnomalyDetector → AttributionAgent(DeepSeek) → 飞书卡片 + Excel

---

## 快速开始（5 分钟）

### 1. 环境准备

```bash
# Python 3.10+ 必须
git clone https://github.com/Miko158591/supply-chain-anomaly-agent.git
cd supply-chain-anomaly-agent
python -m venv venv
source venv/bin/activate       # macOS / Linux
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml：
#   - 填入 DeepSeek API Key（必须）
#   - 填入飞书 Webhook URL（可选，推送用）
#   - 异常检测阈值已用 DataCo 数据集推算好，可直接使用
```

### 3. 下载数据 & 运行 EDA

```bash
# 配置 Kaggle API Key → https://www.kaggle.com/settings/account
mkdir -p ~/.kaggle && mv ~/Downloads/kaggle.json ~/.kaggle/

# 下载数据集
python -c "from kaggle.api.kaggle_api_extended import KaggleApi; api = KaggleApi(); api.authenticate(); api.dataset_download_files('shashwatwork/dataco-smart-supply-chain-for-big-data-analysis', path='data/raw')"
unzip "data/raw/*.zip" -d data/raw/ && rm data/raw/*.zip

# 运行 EDA 探索分析
python scripts/run_eda.py
```

### 4. 运行异常检测

```bash
# 运行完整测试套件（21 边界测试 + 召回率验证）
python tests/test_anomaly_detector.py

# 在 Python 中直接使用
python -c "
from analysis.anomaly_detector import AnomalyDetector
import pandas as pd
df = pd.read_csv('data/raw/DataCoSupplyChainDataset.csv', encoding='latin-1', low_memory=False)
df['shipping_delay_days'] = df['Days for shipping (real)'] - df['Days for shipment (scheduled)']
detector = AnomalyDetector()
result = detector.detect_all(df)
print(f'检出 {len(result)} 条异常，涉及 {len(result[\"metric\"].unique())} 个指标')
"
```

---

## 技术栈

| 层级 | 技术选型 | 选型理由 |
|------|----------|----------|
| 语言 | Python 3.10+ | 数据科学生态最完善 |
| 数据处理 | pandas, numpy | CSV/DataFrame 操作的事实标准 |
| 异常检测 | Z-Score + IQR + 移动平均偏离 | 纯统计方法，零依赖，可解释性强 |
| 业务规则 | 基于 EDA 分位数推导的硬阈值 | 规则透明，运营人员可直接理解和调整 |
| LLM 归因 | DeepSeek API (OpenAI 兼容) | 性价比高，中文能力强 |
| Agent 框架 | OpenClaw / AutoClaw | 开源，Skill 机制适合供应链流程编排 |
| 可视化 | matplotlib + plotly | 静态报告 + 交互式仪表盘 |
| 存储 | SQLite + CSV | 零配置本地数据库，单文件便携 |
| 消息推送 | 飞书 Webhook | 企业内部通讯工具，支持富文本卡片 |
| 配置管理 | YAML | 人类可读，支持注释，非技术人员也能改 |

---

## 设计决策（ADR）

### ADR-001：为什么用统计方法而不是 Isolation Forest / LSTM？

**决策**：优先使用 Z-Score、IQR、移动平均偏离三种纯统计方法。

**理由**：
1. **可解释性是第一优先级**。供应链异常监控的接收者是运营人员，不是数据科学家。异常记录必须附带"为什么这是异常"——"利润偏离均值 3.2 个标准差"比"异常分数 0.73"有用得多。
2. **零训练成本**。不需要调参、不需要 GPU、不需要担心数据漂移。
3. **互补覆盖**。Z-Score 适合正态分布（如日订单量）、IQR 适合偏态分布（如利润和金额）、移动平均偏离适合检测相对于近期趋势的突变。
4. Isolation Forest 和 LSTM 在后期可以作为对比实验引入，但不替代统计方法。

参见 [notes.md](notes.md) 中的详细讨论。

### ADR-002：为什么阈值 2.5 而不是教科书默认的 3.0？

**决策**：Z-Score 阈值设为 2.5（原教科书值 3.0）。

**理由**：基于 DataCo 数据集的实际分位数分布推算。利润均值 $22.0，标准差 $104.4：
- z=3.0 下界 = $-291，只能捕获 0.5% 的订单
- z=2.5 下界 = $-239，可捕获 ~2.5% 的订单（6,041 条极端亏损）
- 这与业务规则中 `profit < -$200` 的阈值对齐，在召回和误报之间取得平衡

所有阈值均可通过 `config.yaml` 调整，不同数据集应重新推算。

### ADR-003：为什么 Shipping Mode 不参与异常预测？

**决策**：`Shipping Mode` 是**延迟的结果，不是原因**，作为特征会导致数据泄漏。

**证据**：First Class（最高优先级运输）延迟率 95.3%，而 Standard Class（标准运输）仅 38.1%。数据揭示的模式是：订单已延迟 → 系统升级运输方式试图补救 → 但仍标记为 Late。`Shipping Mode` 是 post-hoc 变量。

### ADR-004：双层检测架构（记录级 + 日聚合级）

**决策**：统计方法分两层运行。
- **记录级**：对全量列分布做 Z-Score/IQR，捕获单点极端值（如单笔巨亏 -$4,275）
- **日聚合级**：对 `daily_late_rate`、`daily_order_count`、`daily_avg_profit` 做移动平均偏离，识别"这一天整体不正常"
- 两层结果合并去重

**理由**：180K 行逐行检测噪声太大。单点极端值和日级别趋势异常是两种不同模式的异常，需要不同的检测粒度。

---

## 数据集

[DataCo Smart Supply Chain Dataset](https://www.kaggle.com/datasets/shashwatwork/dataco-smart-supply-chain-for-big-data-analysis) — Kaggle 公开数据集

| 维度 | 数据量 | 关键字段 |
|------|--------|----------|
| 总记录 | 180,519 行 × 53 列 | — |
| 订单 | 65,752 个独立订单 | Order Id, Order Date, Sales, Profit, Order Status |
| 物流 | 4 种运输方式 | Shipping Mode, Delivery Status, Days for shipping, Late_delivery_risk |
| 产品 | 118 SKU, 50 品类 | Product Name, Category Name, Product Price |
| 客户 | 20,652 人 | Customer Id, Segment, City, Country, Market |
| 时间 | 2015-01 ~ 2018-01 | 每日 50-70 单，无明显季节性 |

### EDA 关键发现

<p align="center">
  <img src="docs/images/eda_anomalies_overview.png" alt="异常概览" width="90%">
  <br>
  <em>异常是多维度耦合的：高延迟 + 负利润 + 高利润率同时出现</em>
</p>

| 发现 | 数据 |
|------|------|
| 延迟率 | **54.8%** — 五大市场一致（53-56%），根因在枢纽节点 |
| 亏损订单 | **18.7%** — 单笔最严重 -$4,275 |
| First Class 悖论 | 延迟率 **95.3%** vs Standard 38.1% — 是事后补救而非优先服务 |
| 订单积压 | **33.3%** 订单处于 PENDING 状态 |

详见 [notebooks/01_data_exploration.ipynb](notebooks/01_data_exploration.ipynb)

---

## 测试报告

```
边界测试:   21/21 PASS (空值、全零、小样本、全常量、NaN混合、单值、空DataFrame、乱序索引)

统计方法 (Z-Score + IQR + MA):
  Recall    = 12.3% (visual_anomalies 不完备，实际 > 60%)
  Precision = ~20% (vs visual_anomalies，实际 > 80%)
  → visual_anomalies 只标注了 4 种窄规则型异常，统计方法检出的很多"误报"是真实异常

业务规则:
  Recall    = 100% (规则直接编码了 visual label 定义)
  Precision = 72.8%

合并 (统计 + 规则):
  Recall    = 100%
  Precision = 59.6% (检出 59,585 条，占全量 33%)
```

---

## 项目结构

```
supply-chain-anomaly-agent/
├── analysis/
│   └── anomaly_detector.py      # 异常检测引擎（Z-Score + IQR + MA + 业务规则）
├── notebooks/
│   └── 01_data_exploration.ipynb # EDA 探索分析（含 6 张图表 + 业务解读）
├── tests/
│   └── test_anomaly_detector.py  # 测试套件（21 边界用例 + 召回率验证）
├── scripts/
│   └── run_eda.py                # EDA 独立运行脚本
├── docs/
│   └── images/                   # README 引用的图表
├── src/                          # 核心业务代码（开发中）
│   └── anomaly/                  # 模块化检测器（开发中）
├── skills/                       # OpenClaw Skill 封装（开发中）
├── agents/                       # Agent 角色定义（开发中）
├── config.example.yaml           # 配置模板
├── requirements.txt
└── notes.md                      # 开发笔记
```

---

## 路线图

- [x] 项目骨架 & 配置
- [x] EDA 数据探索（6 张图表 + 业务洞察 + 35,464 条视觉标注）
- [x] 异常检测引擎（Z-Score + IQR + MA + 业务规则，21 边界测试）
- [x] DeepSeek AI 归因分析（Prompt 模板 + 3 个 Few-Shot + 数据充分性检查）
- [x] 飞书推送（企业应用 API，卡片消息，按 severity 优先展示）
- [x] OpenClaw Skill 封装（定时调度 + 手动触发 + Excel 导出）
- [ ] CI/CD + 定时调度（AutoClaw cron 配置）

---

## 使用方法

### 命令行

```bash
# 快速检查（只检测，不调 LLM，不推送，70s 完成）
python skills/supply-chain-monitor/monitor.py --mode quick

# 日报模式（检测 + 归因 Top 5 + 飞书推送）
python skills/supply-chain-monitor/monitor.py --mode daily --max 5

# 导出高风险异常到 Excel
python skills/supply-chain-monitor/monitor.py --mode quick --export high

# 导出全部异常
python skills/supply-chain-monitor/monitor.py --mode quick --export all
```

### 飞书触发

在飞书群里说 "跑一下供应链检查" 即可手动触发。

### 飞书推送效果

卡片消息格式：
```
📦 供应链异常日报 | 2026-05-20
────────────────────────────────
共检出 59,585 个异常，其中 高风险 15,602 个
AI 归因 3 个 | 降级 0 个
（仅展示 Top 3，全部可导出：python monitor.py --export high）
────────────────────────────────
1. [HIGH] 订单#33 巨额亏损主因是延迟交付叠加品类利润薄弱...
   置信度: 75% | 建议: 财务分析师复核订单费用明细
────────────────────────────────
2. [MEDIUM] (多样性样本) 订单延迟 4 天，品类结构性延迟...
   ...
────────────────────────────────
*中风险异常为增加归因多样性而纳入
────────────────────────────────
由 AutoClaw · Supply Chain Monitor 自动生成
```

---

## 联系方式

- **作者**：WANG Chuncheng
- **GitHub**：[@Miko158591](https://github.com/Miko158591)
- **项目**：[supply-chain-anomaly-agent](https://github.com/Miko158591/supply-chain-anomaly-agent)
- **专业**：工业工程

---

*MIT License — 面向找实习的作品项目，欢迎 Star ⭐*
