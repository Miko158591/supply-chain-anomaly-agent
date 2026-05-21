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
import matplotlib.font_manager as fm
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
    """全流程阈值扫描（Z-Score 阈值变化 + detect_all 完整流程）。

    每次修改 config 中的 zscore.threshold 后重新跑完整检测。
    """
    results = []
    config_path = os.path.join(PROJECT, "config.yaml")
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    thresholds = np.arange(1.5, 4.1, 0.2)

    for t in thresholds:
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

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
        marker = " <-- current" if abs(t - current_threshold) < 0.01 else ""
        print(f"  z={t:.1f}: P={p:.3f} R={r:.3f} F1={f1:.3f}{marker}")

    return results


def _get_cjk_font_prop():
    """获取中文字体属性对象。"""
    import matplotlib.font_manager as fm
    for f in fm.fontManager.ttflist:
        if "Microsoft YaHei" in f.name and "Regular" in str(f.style):
            return fm.FontProperties(fname=f.fname)
    # fallback: any YaHei
    for f in fm.fontManager.ttflist:
        if "YaHei" in f.name:
            return fm.FontProperties(fname=f.fname)
    for f in fm.fontManager.ttflist:
        if "Noto Sans SC" in f.name:
            return fm.FontProperties(fname=f.fname)
    return fm.FontProperties()


def plot_pr_curve(results: list, current: float, metric: str, output_path: str):
    """画 PR 曲线，标注当前阈值。"""
    fp = _get_cjk_font_prop()
    fp_small = fm.FontProperties(fname=fp.get_file())
    fp_small.set_size(9)
    print(f"  字体: {fp.get_file()}")

    fig, ax = plt.subplots(figsize=(10, 7))

    precisions = [r["precision"] for r in results]
    recalls = [r["recall"] for r in results]
    thresholds = [r["threshold"] for r in results]

    # 连线
    ax.plot(recalls, precisions, "b-o", markersize=6, linewidth=2, label="PR Curve")

    # 标注阈值
    for i, (th, pr, rc) in enumerate(zip(thresholds, precisions, recalls)):
        if i % 2 == 0:
            ax.annotate(
                f"z={th:.1f}",
                (rc, pr), textcoords="offset points", xytext=(10, 5),
                fontsize=8, color="#555555",
            )

    # 当前阈值红点
    current_idx = min(range(len(thresholds)), key=lambda i: abs(thresholds[i] - current))
    ax.scatter(
        [recalls[current_idx]], [precisions[current_idx]],
        color="red", s=150, zorder=5, label=f"Current z={current}",
    )
    ax.annotate(
        f"z={current}\nP={precisions[current_idx]:.1%}  R={recalls[current_idx]:.1%}",
        (recalls[current_idx], precisions[current_idx]),
        textcoords="offset points", xytext=(15, -20),
        fontsize=10, color="red", fontweight="bold",
    )

    ax.set_xlabel("Recall", fontsize=13)
    ax.set_ylabel("Precision", fontsize=13)
    ax.set_title("Z-Score Threshold PR Curve", fontsize=15, fontweight="bold")
    ax.legend(loc="lower left", fontsize=10)
    ax.grid(True, alpha=0.3)

    # 固定坐标范围，让曲线占满整个图
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_aspect("equal")

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(pad=2)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
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
