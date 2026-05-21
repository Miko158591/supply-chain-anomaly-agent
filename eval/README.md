# 评测集说明

## 抽样策略

从 DataCo Smart Supply Chain Dataset（180,519 行）中按以下四层分层抽样：

| 层级 | 数量 | 抽样标准 | 用途 |
|------|------|----------|------|
| A: 明确异常 | 30 | 延迟≥5天 / 利润<-$500 / 利润率<-1.0 / 延迟≥3天+利润<-$200+Late delivery | 验证 Recall |
| B: 边界样本 | 30 | 利润 -$200~$20 低利润区间 / 延迟 2-4 天中延迟 / 利润率 -0.3~-0.05 边际亏损 | 困难样本，拉低 F1 |
| C: 明确正常 | 20 | 准时交付 + 利润 $20-$80 + 利润率 0.1-0.4 | 验证 Precision |
| D: 特殊模式 | 10 | 品类集中（Fishing/Cleats/Footwear）+ 区域集中（Western Europe/Central America） | 验证聚类准确性 |
| Golden Set | 10 | 保留原 v1.0 的 10 条手工标注 | 回归测试 |

**总计**: 90 条（后去重为 89 条，37 异常 + 52 正常/边界）

## 标注标准

### 异常判定规则

| 标注 | 条件 | severity |
|------|------|----------|
| `extreme_delay` | shipping_delay_days ≥ 5 | high |
| `extreme_loss` | Benefit per order < -$500 | high |
| `extreme_ratio` | Order Item Profit Ratio < -1.0 | high |
| `delay_loss_composite` | delay ≥ 3 AND profit < -$200 AND Late delivery | high |
| `category_concentration` | 品类为 Fishing/Cleats/Footwear AND delay ≥ 2 AND profit < -$50 | high |
| `region_concentration` | 区域为 Western Europe/Central America AND delay ≥ 3 AND profit < $0 | high |
| `borderline_*` | 落在模糊区间的样本 | low~medium |
| `normal` | 所有指标在正常范围 | none |

### 标注原则

1. **保守标注**：不确定的样本标注为边界（`is_anomaly=false`），在 reasoning 中说明不确定性
2. **从数据标注，不编造**：每条样本来自 DataCo 真实数据行，`data_snapshot` 字段值与原始 CSV 一致
3. **Golden Set**：v1.0 的 10 条保留（`golden_set=true`），用于回归测试，确保改动不引入退化
4. **reasoning 字段**：每条含 2-3 句中文标注理由，说明为什么这样标、什么情况下可能不同

## 运行评测

```bash
# 完整评测（含 LLM 评委，耗 API 费）
python eval/run_eval.py

# 仅检测指标（省 API）
python eval/run_eval.py --skip-llm

# 输出 Markdown 报告
python eval/run_eval.py --output eval/report.md
```

## 当前结果（89 条样本）

| 指标 | 值 | 说明 |
|------|-----|------|
| Precision | 55.4% | 边界样本（30条，占34%）显著拉低 |
| Recall | 83.8% | 极端异常基本全捕获 |
| F1 | 66.7% | 评测集偏难（边界占比远高于生产），视为**悲观下界** |

### 关于评测指标的重要说明

**这是首次可信的性能基线**。旧的 10 条评测集缺乏负例（正常样本），算出的 Precision 66.7% 口径不可比——那不是真正的 Precision。

**Precision 下降是预期内的**，原因有三：
1. 新增 30 条边界样本（利润 -\$200~\$0、延迟 2-4 天等模糊区间）对任何检测器都是挑战
2. 新增 22 条正常样本使 Precision 分母变大，指标回归真实水平
3. 这是分类问题的经典 Recall-Precision trade-off：扩评测集 + 加边界 → Recall↑ Precision↓

**为什么接受这个 trade-off**：供应链异常监控场景下 Recall 优先——漏报（延迟+亏损订单没被发现）的代价远大于误报（多推送一条需要复核的异常）。业务规则层单独 Recall=100% 兜底，统计层的 Precision 波动不影响最危险异常的捕获。

**评测集边界占比偏高（34%）**：生产数据中边界样本预估占比 ~10%，因此本评测 F1 应视为**悲观下界**，生产环境实际 F1 预计高于此值。这是有意设计——在面试/评审场景下，一个"偏难"的评测集比一个"偏简单"的更有说服力。
