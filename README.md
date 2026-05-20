# 供应链异常智能监控与归因 Agent

基于 OpenClaw（AutoClaw）框架的供应链智能监控系统，自动执行「数据监控 → 异常检测 → AI 归因分析 → 飞书推送」全流程。

## 项目背景

供应链运营中，订单延误、物流异常、库存波动等问题往往发现滞后，排查耗时。本系统通过统计方法自动检测异常点，借助 DeepSeek 大模型进行根因分析，并通过飞书机器人实时推送报告，帮助供应链团队快速响应。

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| 数据处理 | pandas, numpy |
| 异常检测 | Z-Score / 移动平均偏离 / IQR（纯统计方法） |
| LLM | DeepSeek API（归因分析）+ MiniMax API（图像识别） |
| Agent 框架 | OpenClaw / AutoClaw |
| 可视化 | matplotlib, plotly |
| 存储 | SQLite + CSV |
| 推送 | 飞书 Webhook |

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      Pipeline (pipeline.py)                  │
│  每日定时触发 / CLI 手动触发                                  │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                     ▼
   ┌───────────┐     ┌──────────────┐     ┌──────────────┐
   │ Monitor   │     │  Analyst     │     │  Reporter    │
   │ Agent     │ ──▶ │  Agent       │ ──▶ │  Agent       │
   │ 数据加载  │     │  异常检测+   │     │  生成图表+   │
   │ 预处理    │     │  AI 归因     │     │  飞书推送    │
   └───────────┘     └──────────────┘     └──────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
   ┌───────────┐     ┌──────────────┐     ┌──────────────┐
   │ Skills    │     │  Skills      │     │  Skills      │
   │ data_load │     │  detect +    │     │  visualize + │
   │           │     │  root_cause  │     │  push        │
   └───────────┘     └──────────────┘     └──────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
   ┌───────────┐     ┌──────────────┐     ┌──────────────┐
   │ SQLite    │     │  DeepSeek    │     │  Feishu      │
   │ CSV       │     │  API         │     │  Webhook     │
   └───────────┘     └──────────────┘     └──────────────┘
```

## 项目结构

```
supply-chain-anomaly-agent/
├── README.md                    # 项目说明
├── requirements.txt             # Python 依赖
├── config.example.yaml          # 配置模板（复制为 config.yaml 后填入真实值）
├── .gitignore
├── pipeline.py                  # 主调度入口
├── cli.py                       # 命令行接口
├── data/
│   ├── raw/                     # Kaggle 原始 CSV
│   ├── processed/               # SQLite 数据库
│   └── output/                  # 图表、报告
├── logs/                        # 日志文件
├── src/                         # 核心业务代码
│   ├── config.py                # 配置加载器
│   ├── data_loader.py           # CSV 读取与清洗
│   ├── database.py              # SQLite 操作封装
│   ├── features.py              # 特征工程
│   ├── anomaly/                 # 异常检测模块
│   │   ├── base.py              # 检测器基类
│   │   ├── zscore.py            # Z-Score 检测
│   │   ├── moving_avg.py        # 移动平均偏离检测
│   │   └── iqr.py               # IQR 检测
│   ├── attributor.py            # DeepSeek 归因分析
│   ├── visualizer.py            # 可视化图表
│   └── notifier.py              # 飞书推送
├── skills/                      # OpenClaw Skill 封装
│   ├── data_loading_skill.py
│   ├── anomaly_detect_skill.py
│   ├── root_cause_skill.py
│   └── report_push_skill.py
├── agents/                      # Agent 角色定义
│   ├── monitor_agent.py
│   ├── analyst_agent.py
│   └── reporter_agent.py
├── tests/                       # 单元测试
├── notebooks/                   # Jupyter 探索笔记
└── docs/
    └── architecture.md          # 详细架构文档
```

## 数据流

```
data/raw/*.csv
    │
    ▼
DataLoader ──────────────────────── 读取、清洗、合并 4 个维度数据
    │
    ▼
SQLite ─────────────────────────── 结构化存储，支持时间窗口查询
    │
    ▼
FeatureEngineering ──────────────── 按天/周聚合，计算同比环比
    │
    ▼
AnomalyDetector ────────────────── Z-Score + 移动平均 + IQR 三重检测
    │  (至少 2 种方法标异常才确认)
    ▼
Attributor (DeepSeek) ──────────── 构建上下文 Prompt → AI 归因分析
    │
    ▼
Visualizer ─────────────────────── 生成异常趋势图、归因瀑布图
    │
    ▼
Notifier (飞书 Webhook) ─────────── 卡片消息推送（图表 + 文本报告）
```

## 安装与配置

### 前提条件

- Python 3.10+
- Git
- [Kaggle API Key](https://www.kaggle.com/settings/account)（下载数据集用）

### 1. 克隆仓库

```bash
git clone https://github.com/Miko158591/supply-chain-anomaly-agent.git
cd supply-chain-anomaly-agent
```

### 2. 创建虚拟环境

```bash
python -m venv venv
source venv/bin/activate  # macOS / Linux
venv\Scripts\activate     # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置

```bash
# 复制配置模板
cp config.example.yaml config.yaml

# 编辑 config.yaml，填入你的 API Key
# - DeepSeek API Key（必须）
# - MiniMax API Key（可选，用于图像识别）
# - 飞书 Webhook URL（可选，用于推送）
```

### 5. 下载数据集

```bash
# 配置 Kaggle API Key
mkdir -p ~/.kaggle
# 将 kaggle.json 放入 ~/.kaggle/
chmod 600 ~/.kaggle/kaggle.json

# 下载 DataCo Smart Supply Chain Dataset
python -c "from kaggle.api.kaggle_api_extended import KaggleApi; api = KaggleApi(); api.authenticate(); api.dataset_download_files('shashwatwork/dataco-smart-supply-chain-for-big-data-analysis', path='data/raw')"
unzip "data/raw/*.zip" -d data/raw/ && rm data/raw/*.zip

# 数据集包含:
#   DataCoSupplyChainDataset.csv        主数据（~96MB，53 列宽表，含订单/物流/客户/产品）
#   DescriptionDataCoSupplyChain.csv    数据字典
#   tokenized_access_logs.csv          访问日志（可选）
```

### 6. 运行

```bash
# 单次运行
python cli.py run

# 设定每天 8:00 定时运行
python cli.py schedule --time 08:00
```

## 异常检测方法

| 方法 | 原理 | 适用场景 |
|------|------|----------|
| Z-Score | 偏离均值超过 N 个标准差 | 正态分布数据 |
| 移动平均偏离 | 偏离近期移动平均超过 N 倍标准差 | 有趋势/季节性数据 |
| IQR | 超出 Q1 - 1.5×IQR 或 Q3 + 1.5×IQR | 偏态分布或含离群值 |

采用**共识机制**：至少 2 种方法同时标记为异常，才确认为真正的异常事件，降低误报率。

## License

MIT

---

*面向找实习的作品项目，持续开发中。*
