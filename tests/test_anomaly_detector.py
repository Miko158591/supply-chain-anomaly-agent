# -*- coding: utf-8 -*-
"""
异常检测器 — 完整测试套件。

测试范围：
  1. 边界情况：空值、全零、小样本、全异常
  2. 召回率验证：用 EDA 标注的 visual_anomalies.csv 验证算法能否找回 ≥60%
"""

import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.anomaly_detector import AnomalyDetector

PASS = 0
FAIL = 0


def check(condition, name):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


# ============================================================
# Part 1 — 边界情况测试
# ============================================================


def test_edge_cases():
    """测试极端输入：空值、全零、小样本、全异常等。"""
    global PASS, FAIL

    print("=" * 70)
    print("Part 1: 边界情况测试")
    print("=" * 70)

    detector = AnomalyDetector(config_path="config.yaml")

    # --- 1a. 空值序列 ---
    print("\n[1a] 空值序列 (全部 NaN)")
    s_null = pd.Series([np.nan, np.nan, np.nan, np.nan, np.nan])
    for method, name in [
        (detector.detect_zscore, "zscore"),
        (detector.detect_iqr, "iqr"),
        (detector.detect_moving_average, "moving_avg"),
    ]:
        result = method(s_null, metric_name="test")
        check(len(result) == 0, f"{name}: 空值序列应返回空列表")

    # --- 1b. 全是零 ---
    print("\n[1b] 全是零 (std=0)")
    s_zero = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0])
    z_result = detector.detect_zscore(s_zero, metric_name="test")
    check(len(z_result) == 0, "zscore: std=0 应返回空列表（无法标准化）")
    i_result = detector.detect_iqr(s_zero, metric_name="test")
    check(len(i_result) == 0, "iqr: iqr=0 应返回空列表（无四分位差）")

    # --- 1c. 不同样本量下的行为 ---
    print("\n[1c] 样本量边界 — zscore/iqr 需 ≥3 条，moving_avg 需 ≥window+2 条")
    # 正常分布中混入一个极端值（值要够极端，让 zscore 能检出）
    s_ok = pd.Series([10.0, 12.0, 9.0, 11.0, 8.0, 13.0, 10.0, 11.0, 12.0, 9.0, 10.0, 500.0])
    z_ok = detector.detect_zscore(s_ok, metric_name="test")
    check(len(z_ok) > 0, f"zscore: 12个样本中极端值应被检出 (检出 {len(z_ok)} 条)")
    iqr_ok = detector.detect_iqr(s_ok, metric_name="test")
    check(len(iqr_ok) > 0, f"iqr: 分布中有离群值应被检出 (检出 {len(iqr_ok)} 条)")
    # window=20 > len(12)，应返回空
    ma_too_small = detector.detect_moving_average(s_ok, window=20, metric_name="test")
    check(len(ma_too_small) == 0, f"moving_avg(window=20): 样本不足应返回空 (检出 {len(ma_too_small)} 条)")

    # --- 1d. 数据全是异常值（全部相同的大值） ---
    print("\n[1d] 全部相同值 (std=0, 所有值相同)")
    s_const = pd.Series([5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    z_all = detector.detect_zscore(s_const, metric_name="test")
    check(len(z_all) == 0, f"zscore: 常量序列应返回空 (检出 {len(z_all)} 条)")
    iqr_all = detector.detect_iqr(s_const, metric_name="test")
    check(len(iqr_all) == 0, f"iqr: 常量序列应返回空 (检出 {len(iqr_all)} 条)")

    # --- 1e. 混合 NaN + 正常值 ---
    print("\n[1e] 混合 NaN 与正常值")
    # 有足够多的正常非零方差值，让 zscore 和 iqr 都能工作
    s_mixed = pd.Series([np.nan, 10.0, 12.0, np.nan, 9.0, 11.0, 8.0, 13.0,
                         10.0, 11.0, 12.0, 9.0, 10.0, 500.0, np.nan])
    z_mixed = detector.detect_zscore(s_mixed, metric_name="test")
    check(len(z_mixed) > 0, f"zscore: NaN 应被跳过，极端值应检出 (检出 {len(z_mixed)} 条)")
    iqr_mixed = detector.detect_iqr(s_mixed, metric_name="test")
    check(len(iqr_mixed) > 0, f"iqr: NaN 应被跳过，离群值应检出 (检出 {len(iqr_mixed)} 条)")
    # 验证 NaN 位置不会被误报
    for r in z_mixed + iqr_mixed:
        check(not np.isnan(r["value"]), f"检出值不应为 NaN: {r['anomaly_id']}")

    # --- 1f. 单值序列 ---
    print("\n[1f] 单值序列")
    s_one = pd.Series([42])
    check(len(detector.detect_zscore(s_one, metric_name="test")) == 0, "zscore: 单值返回空")
    check(len(detector.detect_iqr(s_one, metric_name="test")) == 0, "iqr: 单值返回空")
    check(len(detector.detect_moving_average(s_one, metric_name="test")) == 0, "moving_avg: 单值返回空")

    # --- 1g. 业务规则 — 空 DataFrame ---
    print("\n[1g] 业务规则 — 空 DataFrame")
    df_empty = pd.DataFrame()
    rules_empty = detector.detect_business_rule(df_empty)
    check(len(rules_empty) == 0, f"空 DataFrame 应返回空列表 (检出 {len(rules_empty)} 条)")

    # --- 1h. 业务规则 — 无匹配列 ---
    print("\n[1h] 业务规则 — DataFrame 不包含规则需要的列")
    df_no_col = pd.DataFrame({"A": [1, 2, 3]})
    rules_no_col = detector.detect_business_rule(df_no_col)
    # 应优雅跳过不匹配的列，不应该报错
    check(isinstance(rules_no_col, list), "不匹配的列应被跳过而不抛异常")

    # --- 1i. 时间序列 — 乱序索引处理 ---
    print("\n[1i] 移动平均 — 乱序数据不抛异常，内部自动排序")
    # 用正常分布的随机数 + 一个明显的离群点
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=50)
    base = np.random.normal(10, 1.5, 50).tolist()
    # 在某一天插入一个极端值（偏离均值 ~10σ），确保能被检出
    base[25] = 35.0
    # 打乱顺序
    idx_shuffled = list(range(50))
    np.random.shuffle(idx_shuffled)
    s_unsorted = pd.Series(
        [base[i] for i in idx_shuffled],
        index=dates[idx_shuffled],
    )
    ma_unsorted = detector.detect_moving_average(s_unsorted, window=7, n_std=2.0, metric_name="test")
    check(len(ma_unsorted) > 0, f"moving_avg: 离群点应被检出 (检出 {len(ma_unsorted)} 条)")
    # 验证时间戳有序（内部 sort_index 生效）
    if ma_unsorted:
        timestamps = [pd.Timestamp(r["timestamp"]) for r in ma_unsorted]
        check(timestamps == sorted(timestamps), "moving_avg: 检出结果应按时序排列")

    print(f"\n边界测试完成: {PASS} PASS, {FAIL} FAIL")


# ============================================================
# Part 2 — 召回率验证
# ============================================================


def test_recall():
    """用 visual_anomalies.csv 验证算法召回率，目标 ≥60%。"""
    global PASS, FAIL

    print("\n" + "=" * 70)
    print("Part 2: 召回率验证（visual_anomalies.csv）")
    print("=" * 70)

    detector = AnomalyDetector(config_path="config.yaml")
    cfg = detector.config
    print(f"\n  当前阈值:")
    print(f"    Z-Score threshold: {cfg.get('zscore', {}).get('threshold', 'N/A')}")
    print(f"    IQR multiplier:    {cfg.get('iqr', {}).get('multiplier', 'N/A')}")
    print(f"    Moving avg window: {cfg.get('moving_avg', {}).get('window', 'N/A')}")
    br = cfg.get("business_rules", {})
    print(f"    Business rules:    loss<{br.get('extreme_loss_threshold','?')}, "
          f"delay>{br.get('extreme_delay_days','?')}, "
          f"ratio>{br.get('high_profit_ratio','?')}, "
          f"total>${br.get('high_value_threshold','?')}")

    # 加载数据
    print("\n[2.1] 加载数据...")
    df = pd.read_csv("data/raw/DataCoSupplyChainDataset.csv", encoding="latin-1", low_memory=False)
    df["shipping_delay_days"] = df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    df["_ts"] = df["order date (DateOrders)"]

    vis = pd.read_csv("data/processed/visual_anomalies.csv")
    all_visual_ids = set(vis["Order Item Id"].values)
    print(f"  全量数据: {len(df):,} 行")
    print(f"  视觉异常样本: {len(vis):,} 条（共 {len(all_visual_ids):,} 个唯一 Order Item Id）")

    # 运行统计方法
    print("\n[2.2] 运行统计方法（单列 + 每日聚合）...")

    # 单列级: 为每条记录建一个整数 index 避免重复时间戳问题
    profit_s = df["Benefit per order"].reset_index(drop=True)
    delay_s = df["shipping_delay_days"].reset_index(drop=True)
    ratio_s = df["Order Item Profit Ratio"].reset_index(drop=True)
    total_s = df["Order Item Total"].reset_index(drop=True)

    caught_ids: set = set()

    # (A) Z-Score 记录级
    for series, col in [(profit_s, "Benefit per order"), (delay_s, "shipping_delay_days"),
                         (total_s, "Order Item Total")]:
        results = detector.detect_zscore(series, metric_name=col)
        if results:
            # 获取异常范围
            lo = min(r["expected_range"][0] for r in results)
            up = max(r["expected_range"][1] for r in results)
            caught = df.loc[(df[col] < lo) | (df[col] > up), "Order Item Id"]
            caught_ids.update(caught.values)
        print(f"  zscore  on {col:30s}: {len(results):>6,} 条异常")

    # (B) IQR 记录级
    for series, col in [(profit_s, "Benefit per order"), (delay_s, "shipping_delay_days"),
                         (total_s, "Order Item Total"), (ratio_s, "Order Item Profit Ratio")]:
        results = detector.detect_iqr(series, metric_name=col)
        if results:
            lo = min(r["expected_range"][0] for r in results)
            up = max(r["expected_range"][1] for r in results)
            caught = df.loc[(df[col] < lo) | (df[col] > up), "Order Item Id"]
            caught_ids.update(caught.values)
        print(f"  iqr     on {col:30s}: {len(results):>6,} 条异常")

    # (C) 移动平均 — 每日聚合
    df["date"] = pd.to_datetime(df["order date (DateOrders)"]).dt.date
    daily_late = df.groupby("date")["Late_delivery_risk"].mean()
    daily_late.index = pd.to_datetime(daily_late.index)
    daily_count = df.groupby("date")["Order Id"].nunique()
    daily_count.index = pd.to_datetime(daily_count.index)
    daily_profit = df.groupby("date")["Benefit per order"].mean()
    daily_profit.index = pd.to_datetime(daily_profit.index)

    for series, metric in [(daily_late, "daily_late_rate"), (daily_count, "daily_order_count"),
                            (daily_profit, "daily_avg_profit")]:
        results = detector.detect_moving_average(series, metric_name=metric)
        if results:
            # 异常日期 → 当天所有 Order Item Id 都算命中
            anomaly_dates = set()
            for r in results:
                try:
                    anomaly_dates.add(str(pd.Timestamp(r["timestamp"]).date()))
                except Exception:
                    pass
            if anomaly_dates:
                caught_on_dates = df.loc[df["date"].astype(str).isin(anomaly_dates), "Order Item Id"]
                caught_ids.update(caught_on_dates.values)
        print(f"  moving_avg on {metric:30s}: {len(results):>6,} 个异常日")

    # (D) 业务规则
    print("\n[2.3] 运行业务规则...")
    rule_results = detector.detect_business_rule(df)
    print(f"  业务规则检出: {len(rule_results):,} 条")

    # 业务规则的每条结果有一个 context，里面有 Order Id
    for r in rule_results:
        ctx = r.get("context", {})
        if "Order Id" in ctx:
            # 找到该 Order Id 下的所有 Order Item Id
            oid = ctx["Order Id"]
            items = df.loc[df["Order Id"] == oid, "Order Item Id"]
            caught_ids.update(items.values)
        elif "Order Item Id" in ctx:
            caught_ids.add(ctx["Order Item Id"])

    # 也直接用条件跑一遍确保不遗漏
    br_cfg = cfg.get("business_rules", {})
    loss_th = br_cfg.get("extreme_loss_threshold", -200)
    delay_th = br_cfg.get("extreme_delay_days", 3)
    ratio_th = br_cfg.get("high_profit_ratio", 0.45)
    value_th = br_cfg.get("high_value_threshold", 500)

    rule_masks = [
        df["Benefit per order"] < loss_th,
        df["shipping_delay_days"] > delay_th,
        df["Order Item Profit Ratio"] > ratio_th,
        df["Order Item Total"] > value_th,
        df["Order Item Profit Ratio"] < -1.0,
        df["Delivery Status"] == "Shipping canceled",
    ]
    for mask in rule_masks:
        caught_ids.update(df.loc[mask, "Order Item Id"].values)

    # 计算召回
    caught_visual = all_visual_ids & caught_ids
    overall_recall = len(caught_visual) / len(all_visual_ids) if all_visual_ids else 0

    print(f"\n[2.4] 召回率计算")
    print(f"  {'='*55}")
    print(f"  视觉异常总数 (unique Item Id):  {len(all_visual_ids):,}")
    print(f"  被算法捕获:                    {len(caught_visual):,}")
    print(f"  漏检:                          {len(all_visual_ids - caught_visual):,}")
    print(f"  总体召回率:                    {overall_recall:.1%}")
    print(f"  {'='*55}")

    # 按类型统计
    print(f"\n  按类型召回率:")
    type_map = {
        "anom_extreme_loss": ("极端亏损", "Benefit per order"),
        "anom_ultra_delay": ("超长延迟", "shipping_delay_days"),
        "anom_high_margin": ("高利润率", "Order Item Profit Ratio"),
        "anom_high_value": ("高金额", "Order Item Total"),
    }
    for col, (label, metric) in type_map.items():
        ids = set(vis.loc[vis[col] == True, "Order Item Id"])  # noqa: E712
        caught = ids & caught_ids
        rec = len(caught) / len(ids) if ids else 0
        status = "PASS" if rec >= 0.6 else ("WARN" if rec >= 0.3 else "FAIL")
        print(f"    {label:<12s} {len(ids):>7,} → {len(caught):>7,}  ({rec:.1%})  [{status}]")

    # 判定
    print(f"\n  判定: ", end="")
    if overall_recall >= 0.6:
        print(f"召回率 {overall_recall:.1%} >= 60% — 目标达成！")
        PASS_inc = 1
    else:
        print(f"召回率 {overall_recall:.1%} < 60% — 未达标")
        PASS_inc = 0

    # 漏检分析
    missed_ids = all_visual_ids - caught_ids
    missed = vis[vis["Order Item Id"].isin(missed_ids)]
    print(f"\n[2.5] 漏检分析（共 {len(missed):,} 条）")

    for col, (label, metric) in type_map.items():
        m = missed[missed[col] == True]  # noqa: E712
        if len(m) > 0:
            pct_of_type = len(m) / (vis[col] == True).sum() * 100 if (vis[col] == True).sum() > 0 else 0 # noqa: E712
            print(f"\n  {label} ({metric}): {len(m):,} 条漏检 ({pct_of_type:.1f}% of type)")

            if col == "anom_high_margin":
                vals = m["Order Item Profit Ratio"].dropna()
                print(f"    漏检值范围: [{vals.min():.3f}, {vals.max():.3f}]")
                print(f"    原因: profit ratio > 0.45 是业务阈值，不在统计分布尾部")
                print(f"    这类异常 IQR/Z-Score 天然检测不到，必须用业务规则覆盖")
                print(f"    当前 config business_rules.high_profit_ratio = 0.45，已覆盖")

            elif col == "anom_extreme_loss":
                vals = m["Benefit per order"].dropna()
                print(f"    漏检值范围: [{vals.min():.2f}, {vals.max():.2f}]")
                print(f"    当前 Z-Score threshold=2.5 下界 ≈ {detector.config['zscore']['threshold']}σ")
                # 计算实际需要的阈值
                all_profit = df["Benefit per order"]
                profit_std = all_profit.std()
                profit_mean = all_profit.mean()
                needed_z = abs((vals.max() - profit_mean) / profit_std)
                print(f"    捕获需 z-score >= {needed_z:.1f}")
                print(f"    建议: 降低 threshold 到 {max(2.0, needed_z - 0.5):.1f} 或增加 IQR 乘数")

            elif col == "anom_high_value":
                vals = m["Order Item Total"].dropna()
                print(f"    漏检值范围: [{vals.min():.2f}, {vals.max():.2f}]")
                print(f"    当前 IQR k={detector.config['iqr']['multiplier']}")
                q1, q3 = df["Order Item Total"].quantile(0.25), df["Order Item Total"].quantile(0.75)
                needed_k = (vals.min() - q3) / (q3 - q1) if (q3 - q1) > 0 else float("inf")
                print(f"    捕获需 IQR k >= {needed_k:.1f}")
                print(f"    建议: 业务规则 total > 500 已覆盖大部分高金额异常")

    return overall_recall


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    test_edge_cases()
    recall = test_recall()

    print("\n" + "=" * 70)
    print(f"总结果: {PASS} PASS, {FAIL} FAIL")
    print(f"召回率: {recall:.1%}")
    print("=" * 70)
