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
- 21 个边界测试全部通过，召回率 100%

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

## 后续计划
- [ ] DeepSeek 归因分析（attributor.py）
- [ ] 可视化模块（visualizer.py）
- [ ] 飞书推送（notifier.py）
- [ ] OpenClaw Skill 封装
- [ ] 完整 Pipeline 串联
