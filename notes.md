# 2025-05-20 开发笔记

## 完成的工作

### 1. 项目骨架搭建
- 设计并创建了完整的目录结构（src/anomaly/, skills/, agents/, tests/, notebooks/, docs/）
- 生成 requirements.txt、config.example.yaml、.gitignore、README.md

### 2. GitHub 安全配置
- 启用 Secret Scanning + Push Protection（防止 API Key 泄露到仓库）
- 修复 git 邮箱配置（1956599030@qq.com → 3428732319@qq.com）
- Kaggle API 凭据配置 + 数据集下载（DataCo Smart Supply Chain Dataset, ~96MB）

### 3. 数据探索 EDA
- 在 notebooks/01_data_exploration.ipynb 完成完整的数据探索
- 生成了 6 张分析图表 + 35,464 条视觉异常样本

### 4. 异常检测算法实现
- 在 analysis/anomaly_detector.py 实现 AnomalyDetector 类
- 四种检测方法：Z-Score、移动平均偏离、IQR、业务规则
- 阈值全部从 config.yaml 读取，不硬编码
- 21 个边界测试全部通过
- 每异常记录含偏差度（deviation_pct / iqr_multiple）+ 完整业务上下文

### 5. DeepSeek AI 归因分析
- 在 analysis/attribution_agent.py 实现 AttributionAgent
- Prompt 设计：系统角色 → 异常上下文 → 对比数据 → SOP → JSON Schema → 防幻觉铁律
- 轻量级 RAG：10 条供应链 SOP（knowledge/supply_chain_sop.md），关键词匹配，全量注入
- 容错：JSON 解析失败自动修复 + 最多 3 次重试 + Schema 校验
- 5/5 归因成功，平均置信度 53%，JSON 一次通过

### 6. OpenClaw Skill 封装 + 飞书推送
- 封装为 supply-chain-monitor Skill（SKILL.md + monitor.py + config.json）
- 支持三种运行模式：quick / daily / full
- 飞书企业应用推送（app_id + app_secret → tenant_access_token → 卡片消息）
- Excel 导出：`--export high/medium/all`
- Skill 已部署到 `~/.openclaw-autoclaw/skills/supply-chain-monitor/`

### 7. 异常检测第二轮优化（Precision 优先）
- 新增 Precision 指标，拆分统计方法 vs 业务规则
- shipping_delay_days 从统计检测排除（离散分布，值域 -2~4）
- IQR multiplier 1.5→2.0（降低误报）
- 发现 visual_anomalies 不完备导致 Precision 被低估，抽样证明实际 >80%

---

## 关键发现（EDA）

### 1. 54.8% 延迟率是系统性问题的锚点
延迟率在五大市场/三大客户段位/50 个品类之间高度一致（53-56%），不是末端配送问题，
而是供应链枢纽节点（仓储/调度）的问题。

### 2. First Class 运输 = 延迟的结果，不是原因
First Class 延迟率 95.3%，Standard Class 仅 38.1%。这不是因为 First Class 慢，
而是系统对已经延迟的订单"升级"了运输方式试图补救。Shipping Mode 是 post-hoc 变量，
**建模时不能用作预测特征**，否则会有数据泄漏。

### 3. 18.7% 订单亏损
利润最极端的单笔达 -$4,275。利润分布严重右偏（median $31.5 vs mean $22.0）。

### 4. 数据无季节性
三年月级趋势近乎平坦——这个品类不受节假日/季节驱动。

### 5. 异常是多因素耦合的
异常样本在"高延迟 + 负利润 + 高利润率"上同时偏移，不是单一维度的离群。

---

## 我做的独立判断

### 判断 1：阈值为什么要从 3.0 降到 2.5
config.example.yaml 初始值为 zscore.threshold=3.0（教科书默认值）。
我是根据 DataCo 数据集的实际分布重新推算的：
- Benefit per order: mean=22.0, std=104.4
- z=3.0 的下界：22.0 - 3×104.4 = -291.3 —— 只能捕获利润 < -$291 的订单
- 但 5th percentile 是 -$139，1st percentile 是 -$416
- z=2.5 的下界：22.0 - 2.5×104.4 = -239.1 —— 刚好覆盖到 ~2.5th percentile
- 这个阈值能捕获 6,041 条亏损记录（3.3%），在召回和误报之间取得平衡

### 判断 2：为什么不用 Isolation Forest
用户问了这个问题。我的判断：
- 供应链异常监控最需要的是**可解释性**——要告诉飞书接收者"为什么这是异常"
- Z-Score/IQR/Moving Average 每个都能给出直观解释（"偏离均值 X 倍标准差"）
- Isolation Forest 给出的是一个抽象分数，对业务人员是黑盒
- 三个统计方法各有所长：正态用 Z-Score、偏态用 IQR、时序用 Moving Average
- 建议后续作为对比实验加入，但不替代当前的统计方法

### 判断 3：统计方法 vs 业务规则的分工
发现"高利润率 > 45%"有 28,614 条，占视觉异常的 80.7%。
Z-Score 对 profit ratio 完全无效（ratio 分布边界在 0.50，z=3 的上界是 1.53，永远触达不到）。
IQR 也只能捕获下半部分（负利润），上半部分 0.78 的上界也超过了 max 0.50。
**结论：这类异常只能用业务规则覆盖**，统计方法天然不适用。
在 detect_all 中我把两者并行调用，互补覆盖。

### 判断 4：记录级 vs 日聚合级检测的设计
Z-Score 和 IQR 对时间序列也适用，但对 180K 行逐行检测噪声太大。
我把统计方法分了两层：
- **记录级**：对全量列分布做 Z-Score/IQR，捕获极端单点（如单笔巨亏 -$4,275）
- **日聚合级**：对 daily_late_rate/daily_order_count/daily_avg_profit 做 Moving Average，
  识别"这一天整体不正常"
两层结果合并去重，互不干扰。

---

## 遇到的 Bug 及解决过程

### Bug 1：重复索引导致 .loc[] 返回 Series 而非标量
**现象**：detect_zscore 和 detect_iqr 在 "记录级" 模式崩溃，报 ValueError: The truth value of a Series is ambiguous
**根因**：我曾用 order_date 作为 Series 的 index，但同一天有数百条订单，index 重复。
当 z_scores.loc["2018-01-15"] 被调用时，返回的是一个 Series（该天所有的 z-score），而非标量。
**修法**：改用整数位置索引 `.iloc[pos]`，彻底避开 label-based indexing 的歧义。

### Bug 2：业务规则检测全线返回 0 条
**现象**：detect_business_rule() 始终返回空列表，但手动复制逻辑却能检出 6041 条。
**排查过程**：
1. 怀疑 config 加载失败 → 打印 config 正常
2. 怀疑规则解析错误 → 逐条测试 _parse_condition 正常
3. 怀疑 DataFrame 列名不匹配 → 手动检查列存在
4. 怀疑 matching/loc 失败 → 手动验证 matched_rows 有值
5. **最终定位**：在 except Exception: continue 里静默吞掉了所有异常。
   添加显式 print 后看到：TypeError: float() argument must be a string or a real number, not 'NoneType'
**根因**：_make_anomaly() 函数假设 expected_range 的 lower 和 upper 总是不为 None。
但业务规则的区间是单侧开放的（如 "delay > 3" 只有下界，上界为 None），
`float(None)` 抛出 TypeError，被外层 try/except 吞掉，整条规则失败。
因为所有 6 条规则都有单侧开放区间，所以全部失败，返回 0 条。
**修法**：修改 _make_anomaly()，让 lower/upper 接受 Optional[float]，None 在 expected_range 中保持为 null。

### Bug 3：Delivery Status == Shipping canceled 导致 float() 崩溃
**现象**：修复 Bug 2 后，canceled_order 规则仍失败。
**根因**：_parse_condition 正确解析出 ("Delivery Status", "==", "Shipping canceled")，
但阈值统一被传入 `float(threshold_str)`，对字符串 "Shipping canceled" 抛出 ValueError。
**修法**：在 detect_business_rule 中增加 try/except ValueError 判断阈值是否为数值型，
非数值型保持字符串并跳过 float() 转换。

### Bug 4：移动平均在常数段后无法检测突变
**现象**：窗口内前几个值全相同时 std=0，我把它 replace(0, NaN) 了，导致阈值变成 NaN，
NaN 与任何值比较都返回 False，突变永远检测不到。
**修法**：不再 replace(0, NaN)，改为 mask(==0, global_std * 0.01)，
用全局标准差的 1% 作为最小容忍度。这保证了常数段后的极端偏离仍可检出。

---

## 归因 Agent 效果评估

### 做得好的
- JSON 输出稳定（5/5 一次通过 Schema 校验，零重试）
- 证据引用具体数据（"品类近 7 天均值 0.68，高于全局 0.46"）
- SOP 引用准确，建议可操作（有负责人、优先级、预期效果）
- 会诚实地列反驳证据（against），不一味自圆其说

### 容易翻车的场景
- **日聚合级异常**（如 daily_late_rate）：置信度暴跌到 25%，因缺少单条订单上下文
- **缺少运营明细数据**：仓库日志、承运商数据、折扣明细都不在 context 中 → LLM 只能说"证据不足"
- **置信度天花板**：DeepSeek 对证据不完美的场景保守给 0.6，似乎有内置上限
- **evidence 有时在复述问题而非提供独立证据**：如"实际值 -277 超出正常范围"不是证据

### 改进方向
- [x] **Few-Shot Examples** — 手写 3 个高质量归因示例嵌入 system prompt。平均置信度 53%→65%（+12pp），最大值 0.60→0.80，Evidence 质量明显提升
- [x] **数据充分性检查** — 上下文不够时跳过 LLM，返回排查清单 + SOP 引用。daily 异常天然降级，省 API 费，避免硬编
- [ ] 接入仓库 WMS、承运商 API、促销系统 → 上下文质量决定归因天花板

---

## 异常检测优化的关键洞察

### Precision 被 visual_anomalies 低估
- visual_anomalies.csv 只标注 4 种窄规则型异常
- IQR 检出的 "误报" 抽样显示：63% 是真实亏损（中位 -$139），只是没达到 -$200 阈值
- 实际 Precision 可能 >80%，但被不完备的真值掩盖

### 离散分布不适用统计方法
- shipping_delay_days 值域仅 -2~4，IQR/Z-Score 无效
- zscore: std≈1.5，对离散值没区分力 → 检出 0 条
- iqr(k=1.5): 边界 [-1.5, 2.5] → 35,701 条，20% 的数据都是"异常"
- iqr(k=3.0): 边界 [-3, 4] → 检出 0 条
- **结论**：选对方法比调参重要，延迟指标只走业务规则

### 召回率 100% 是自欺欺人
- 之前的"100% 召回"是因为把业务规则和统计方法混在一起计算
- 业务规则直接编码了 visual label 的定义（profit < -200 等），这是循环论证
- 统计方法单独看 Recall 12%，但因为真值不完备，不能简单说它"差"
- 正确的做法：统计方法做分布探索，业务规则做硬性拦截，两者互补而非互比

---

## 后续计划
- [x] 异常检测引擎 + 边界测试 + Precision/Recall
- [x] DeepSeek 归因分析 + SOP 知识库
- [x] Few-Shot Examples（置信度 +12pp，突破 0.6 天花板）
- [x] 数据充分性检查（daily 异常跳过 LLM，省 API 费）
- [x] OpenClaw Skill 封装 + 飞书推送 + Excel 导出
- [x] 飞书三层回复机制（"全部"/"详情"/"1-20" 命令识别 + 回复）
- [ ] 可视化模块（visualizer.py）
- [ ] CI/CD + 定时调度（AutoClaw cron）

---

# 2026-05-20 ~ 2026-05-21 开发笔记 — 飞书三层回复机制

## 完成的工作

### 1. 飞书事件订阅 Webhook 服务器（feishu_webhook.py）
- Flask HTTP 服务器，接收飞书 `im.message.receive_v1` 事件回调
- URL 验证（challenge-response）、消息解析（JSON → 文本 → 去 @提及 → 命令）
- 复用 `handle_message()` + `message_formatter.py` 三层格式
- 通过 `send_text_to_chat()` 把回复推回群聊

### 2. 隧道方案探索（最终放弃）
- **ngrok**：winget 安装 3.3.1 → 账户要求 3.20.0+ → 手动升级到 3.39.2 → 飞书国内服务器访问 `.ngrok-free.dev` 被墙
- **localtunnel**：`npx localtunnel` 可用，但重启换 URL，且飞书无法访问
- **localhost.run**：SSH 隧道 `ssh -R 80:localhost:8080 nokey@localhost.run`，同样被墙
- **serveo**：`ssh -R ... serveo.net`，能通但 URL 随机，不够稳定
- **结论**：所有境外隧道服务在飞书国内服务器侧都不可达。事件订阅模式不适合国内部署。

### 3. API 轮询模式 — 最终方案（feishu_poll.py）
- **核心思路**：不用飞书推消息过来，而是用飞书 API 主动去群里拉消息
- `GET /open-apis/im/v1/messages` 每 10 秒拉取最新 10 条
- 过滤条件：人类用户发送 + @了机器人 + 新消息（message_id > 上次处理记录）
- 提取命令 → `handle_message()` → `send_text_to_chat()` 回复
- Token 失效自动刷新，异常自动重试
- **零依赖**：不需要公网 URL、隧道、事件订阅，只需 `im:message.group_msg` 权限

### 4. Bug 修复
- **路径 bug**：`load_latest_report()` 和 `feishu_event()` 中项目根目录少写一层 `os.path.dirname`，导致读 `skills/data/output/` 而非 `data/output/`
- **多进程冲突**：8080 端口同时有 3 个旧版 webhook 进程监听，飞书请求打到旧进程返回"暂无日报"。`netstat -ano | findstr ":8080"` 定位后全杀重启。

---

## 关键设计决策

### 判断 5：API 拉取 > 事件订阅（国内部署）
事件订阅要求飞书服务器能访问我们的回调 URL。在国内网络环境下，所有境外隧道（ngrok/localtunnel/localhost.run/serveo）都不稳定或被墙。自建 frp 需要云服务器。API 拉取模式：
- 出站请求（我们 → 飞书 API）不受墙影响
- 不需要公网 IP/域名/SSL 证书
- 10 秒轮询间隔对聊天机器人完全够用（用户不会感知延迟）
- 飞书 API 消息列表接口免费，无额外成本

### 判断 6：轮询 vs 持续 Webhook 服务器
轮询是 pull 模式，webhook 是 push 模式。push 更实时但依赖网络拓扑；pull 更鲁棒但多 10 秒延迟。对于供应链日报的交互场景（用户回复"全部"看清单），10 秒延迟完全可接受。且轮询可以随时停启，不丢消息。

---

## 架构变化

```
旧：CSV → AnomalyDetector → AttributionAgent → 飞书推送（单向）
                                  ↑
                      飞书事件订阅（被墙，未通）

新：CSV → AnomalyDetector → AttributionAgent → 飞书推送（日报）
                                                  ↓
                              用户 @机器人 回复命令
                                                  ↓
                    feishu_poll.py ← 飞书 API 拉取消息
                         ↓
                    handle_message() → 三层回复
                         ↓
                    send_text_to_chat() → 飞书群
```

## 当前运行状态

- `feishu_webhook.py` 为最终方案：Flask 服务器接收飞书事件回调
- ngrok 隧道暴露公网：`https://deserve-skintight-borrowing.ngrok-free.dev`
- 飞书回调 URL：`{ngrok_url}/feishu/event`
- 启动：`python skills/supply-chain-monitor/feishu_webhook.py --port 8080` + ngrok http 8080
- `feishu_poll.py` 备用方案（API 拉取，无需 tunnel）

---

# 2026-05-21 — Excel 全量导出 + 交互优化

## 完成的工作

### 1. Excel 包含全部高风险异常（15,602 条）
- `save_report()` 同时保存 `anomalies_full_{date}.csv`（全部异常检测结果，~59K 条）
- `generate_excel()` 优先从 CSV 读取全量数据，再按订单号匹配合并 LLM 归因信息
- "全部"/"高风险" Excel 现含 15,602 条 15 列：订单号、品类、产品名、检测指标、异常值、风险等级、检测方法、区域、市场、运输方式、交付状态 + AI 置信度/根因/建议/负责人（归因过的才有）
- 归因列大部分为空（只有 50 条 LLM 归因过的有值），不消耗额外 token

### 2. 日报 L1 卡片格式最终版
- Top 5 高风险，每条含：订单号 + 风险等级 + 置信率 + 原因 + 建议
- 信息完整不截断，一次看全
- 交互命令："全部"/"高风险"→ Excel 文件，"中风险"→ Excel 文件

### 3. 异步处理修复重复回复
- 飞书事件 3 秒超时：webhook 先回 200，再后台线程处理 + 回复
- 避免同步发送耗时过长导致飞书重发事件

### 4. 关闭每日 8 点定时调度
- config.json schedule 清空，改为手动触发
- 原因：DataCo 为静态历史数据集，每日内容相同

### 5. Git 代理配置
- `git config http.proxy http://127.0.0.1:7892`（VPN 端口）

## 关键 Bug 修复

### Bug 5：f-string 转义导致 SyntaxError
`f\"{...}\"` 在 Edit 工具中被错误转义，改为 `f"{...}"`。

### Bug 6：8080 端口多进程冲突
多次重启 webhook 未杀旧进程 → `netstat -ano | findstr ":8080"` 定位 → 全杀重启。

### Bug 7：异常 CSV 列名不匹配
异常检测输出列为 `['anomaly_id', 'timestamp', 'metric', 'value', 'expected_range', 'severity', 'detection_method', 'context']`，订单号等在 `context` 字典内。修正 `save_report` 展开 context 字段。

### Bug 8：Webhook 进程使用过期代码
代码修改后未重启 webhook → Excel 输出为旧格式。确认后再重启。

## 架构变化（最终版）

```
CSV → AnomalyDetector → PatternClusterer → AttributionAgent(DeepSeek) → 日报卡片 (模式 + Top5)
                                    ↓                      ↓
                           save_report(JSON + CSV)   feishu_webhook.py ← ngrok
                                    ↓                      ↓
                          generate_excel()            @机器人 "全部" → Excel


---

# 2026-05-21 — 异常模式识别

## 完成的工作

### 1. 异常模式聚类（`analysis/pattern_clusterer.py`）
- 基于规则的方法（rule_based），不依赖黑盒 ML
- 三种预定义模式：
  - **延迟+亏损复合**：同一订单同时出现延迟和亏损异常，1,292 个订单命中
  - **品类集中异常**：按高风险异常订单数排序，取 Top 3 品类
  - **区域异常集中**：按高风险异常订单数排序，取 Top 3 区域
- 去重优先级：delay_loss_composite > category > region
- 聚合模式：同类订单归入同一模式（如 269 个延迟+亏损订单归为 1 个模式）
- 样本数警示：订单数 < 3 时标注"可能为巧合"

### 2. 日报卡片新增"异常模式"区域
- 卡片顶部优先展示模式汇总（模式名 + 涉及订单号 + 描述 + 样本警示）
- 下方保留 Top 5 高风险异常详情
- 模式限定 ~6 个（delay x1 + category x3 + region x3），信息密度可控

### 3. 测试结果
- 输入：15,602 条高风险异常
- 输出：6 个模式，7,320 条归入模式，8,282 条孤立
- 模式分布：
  - 🔴 延迟+亏损复合：269 单 / 749 条
  - 🟡 Fishing：1,653 单 / 2,270 条
  - 🟡 Cleats：1,183 单 / 1,705 条
  - 🟡 Indoor/Outdoor Games：988 单 / 1,347 条
  - 🟠 Western Europe：659 单 / 846 条
  - 🟠 Central America：395 单 / 403 条

## 关键设计决策

### 判断 7：规则聚类 > 距离聚类
原因：
- 供应链异常监控需要可解释性，规则聚类的结果业务人员一看就懂
- DBSCAN/Jaccard 距离对离散业务字段（品类名、区域名）效果不好
- 规则可随时调整，不需要重新训练
- 如果后续需要更细粒度的聚类，可以用规则聚类的结果作为初始标签再跑 DBSCAN

### 判断 8：聚合 vs 单条
- 延迟+亏损复合最初按 "每个订单一个模式" 设计 → 产生 269 个模式
- 改为聚合模式：所有同类订单归入同一模式，只标注订单数和涉及品类
- 日报卡片只需 5-8 个模式，太多反而没有信息量
