# -*- coding: utf-8 -*-
"""
阈值敏感性分析 — 扫描 Z-Score/IQR 阈值，画 PR 曲线。

用法: python analysis/threshold_analysis.py
输出: docs/images/pr_curve_zscore.png + pr_curve_iqr.png
"""

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.anomaly_detector import AnomalyDetector

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_test_orders(path: str = "eval/test_cases.json") -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    anomaly_oids = set()
    normal_oids = set()
    for tc in data["test_cases"]:
        oid = str(tc["data_snapshot"].get("Order Id", ""))
        if tc.get("expected", {}).get("is_anomaly"):
            anomaly_oids.add(oid)
        else:
            normal_oids.add(oid)
    return {"anomaly": anomaly_oids, "normal": normal_oids}


def compute_pr(detected_oids: set, ground_truth: dict) -> tuple:
    """只在评测集范围内计算 PR——不对全量订单做 FP 计数。"""
    all_test = ground_truth["anomaly"] | ground_truth["normal"]
    detected_in_test = detected_oids & all_test
    tp = len(detected_in_test & ground_truth["anomaly"])
    fp = len(detected_in_test & ground_truth["normal"])
    fn = len(ground_truth["anomaly"] - detected_oids)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    return precision, recall


def run_zscore_sweep(df: pd.DataFrame, ground_truth: dict) -> list:
    """Z-Score 阈值 1.5→4.0 扫描。"""
    results = []
    thresholds = np.arange(1.5, 4.1, 0.1)
    for t in thresholds:
        detector = AnomalyDetector.__new__(AnomalyDetector)
        detector.threshold_config = {
            "zscore": {"threshold": float(t), "min_periods": 7},
            "iqr": {"multiplier": 2.0},
            "moving_avg": {"window": 7, "deviation_factor": 2.0},
            "business_rules": {"extreme_loss_threshold": -200},
            "consensus_min": 1,
        }
        detector.config_path = None
        # Use detect_all with override
        import copy
        orig = AnomalyDetector.__init__

        # Simpler: just use zscore detection directly
        result = detector.detect_all(df)
        detected = set()
        for _, row in result.iterrows():
            ctx = row.get("context", {})
            oid = str(ctx.get("Order Id", "")) if isinstance(ctx, dict) else ""
            if oid:
                detected.add(oid)
        p, r = compute_pr(detected, ground_truth)
        results.append({"threshold": round(t, 1), "precision": p, "recall": r})
        print(f"  z={t:.1f}: P={p:.3f} R={r:.3f} F1={2*p*r/(p+r) if (p+r)>0 else 0:.3f}")
    return results


def run_real_sweep(df: pd.DataFrame, ground_truth: dict, current_threshold: float) -> list:
    """使用真实 detect_all 完整流程扫描 Z-Score 阈值。

    每次修改 config 中的 zscore.threshold 后重新跑检测。
    """
    results = []
    config_path = os.path.join(PROJECT, "config.yaml")
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    thresholds = np.arange(1.5, 4.1, 0.2)

    for t in thresholds:
        # 临时修改 config
        config["anomaly"]["zscore"]["threshold"] = float(t)
        tmp_path = os.path.join(PROJECT, "config_tmp.yaml")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        detector = AnomalyDetector(config_path=tmp_path)
        result = detector.detect_all(df)
        detected = set()
        for _, row in result.iterrows():
            ctx = row.get("context", {})
            oid = str(ctx.get("Order Id", "")) if isinstance(ctx, dict) else ""
            if oid:
                detected.add(oid)
        p, r = compute_pr(detected, ground_truth)
        results.append({"threshold": round(t, 1), "precision": p, "recall": r})
        os.remove(tmp_path)

        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
        marker = " <-- current" if abs(t - current_threshold) < 0.01 else ""
        print(f"  z={t:.1f}: P={p:.3f} R={r:.3f} F1={f1:.3f}{marker}")

    return results


def plot_pr_curve(results: list, current: float, metric: str, output_path: str):
    """画 PR 曲线，标注当前阈值。"""
    plt.figure(figsize=(8, 6))

    precisions = [r["precision"] for r in results]
    recalls = [r["recall"] for r in results]
    thresholds = [r["threshold"] for r in results]

    # 连线的 PR 曲线
    plt.plot(recalls, precisions, "b-o", markersize=5, linewidth=1.5, label="PR 曲线")

    # 在每个点标注阈值（每隔一个点标一次，避免重叠）
    for i, (th, pr, rc) in enumerate(zip(thresholds, precisions, recalls)):
        if i % 2 == 0:
            plt.annotate(
                f"{th:.1f}",
                (rc, pr),
                textcoords="offset points",
                xytext=(8, 4),
                fontsize=7,
                color="gray",
            )

    # 标出当前阈值
    current_idx = min(
        range(len(thresholds)), key=lambda i: abs(thresholds[i] - current)
    )
    plt.scatter(
        [recalls[current_idx]],
        [precisions[current_idx]],
        color="red",
        s=120,
        zorder=5,
        label=f"当前阈值 z={current}",
    )
    plt.annotate(
        f"  z={current}\n  P={precisions[current_idx]:.1%} R={recalls[current_idx]:.1%}",
        (recalls[current_idx], precisions[current_idx]),
        textcoords="offset points",
        xytext=(15, -15),
        fontsize=9,
        color="red",
        fontweight="bold",
    )

    plt.xlabel("Recall", fontsize=12)
    plt.ylabel("Precision", fontsize=12)
    plt.title(f"Z-Score 阈值 PR 曲线 ({metric})", fontsize=14)
    plt.legend(loc="lower left")
    plt.grid(True, alpha=0.3)
    plt.xlim(0, 1.05 if max(recalls) > 0.9 else max(recalls) + 0.1)
    plt.ylim(0, min(1.05, max(precisions) + 0.1))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"PR 曲线已保存: {output_path}")


def main():
    print("=== 阈值敏感性分析 ===")

    # 加载数据
    print("\n[1/4] 加载数据...")
    df = pd.read_csv(
        os.path.join(PROJECT, "data", "raw", "DataCoSupplyChainDataset.csv"),
        encoding="latin-1",
        low_memory=False,
    )
    df["shipping_delay_days"] = df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    print(f"  数据: {len(df):,} 行")

    ground_truth = load_test_orders()

    # 加载当前阈值
    with open(os.path.join(PROJECT, "config.yaml"), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    current_z = config["anomaly"]["zscore"]["threshold"]
    print(f"  当前 Z-Score 阈值: {current_z}")

    # Z-Score 扫描
    print("\n[2/4] Z-Score 阈值扫描 (1.5→4.0)...")
    z_results = run_real_sweep(df, ground_truth, current_z)

    # 阈值对比表
    print("\n[3/4] 阈值对比表 (用于 ADR):")
    for r in z_results:
        th = r["threshold"]
        p, rc = r["precision"], r["recall"]
        f1 = 2 * p * rc / (p + rc) if (p + rc) > 0 else 0
        if th in (2.0, 2.5, 3.0):
            print(f"  z={th:.1f}: P={p:.1%} R={rc:.1%} F1={f1:.1%}")

    # 画图
    print("\n[4/4] 生成 PR 曲线...")
    plot_pr_curve(z_results, current_z, "Z-Score", os.path.join(PROJECT, "docs", "images", "pr_curve_zscore.png"))

    # 输出阈值对比表数据
    print("\n=== 阈值对比表 (z=2.0/2.5/3.0) ===")
    for r in z_results:
        if r["threshold"] in (2.0, 2.5, 3.0):
            p, rc = r["precision"], r["recall"]
            f1 = 2 * p * rc / (p + rc) if (p + rc) > 0 else 0
            print(f"z={r['threshold']:.1f}  P={p:.1%}  R={rc:.1%}  F1={f1:.1%}")

    print("\n完成。")


if __name__ == "__main__":
    main()
