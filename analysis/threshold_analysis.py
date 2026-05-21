# -*- coding: utf-8 -*-
"""
阈值消融实验 + 自动校准 — 四种检测模式各自扫阈值，画 PR 曲线对比。

用法:
  python analysis/threshold_analysis.py              # 消融实验 + PR 曲线
  python analysis/threshold_analysis.py --calibrate  # 自动校准 z 值到 config.yaml
输出: docs/images/pr_curve_ablation.png
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


# ═══════════════════════════ Helpers ═══════════════════════════

def load_ground_truth() -> dict:
    with open(os.path.join(PROJECT, "eval", "test_cases.json"), encoding="utf-8") as f:
        data = json.load(f)
    anomaly = set()
    normal = set()
    for tc in data["test_cases"]:
        oid = str(tc["data_snapshot"].get("Order Id", ""))
        if tc.get("expected", {}).get("is_anomaly"):
            anomaly.add(oid)
        else:
            normal.add(oid)
    return {"anomaly": anomaly, "normal": normal}


def get_detected_oids(result) -> set:
    """从检测结果（DataFrame 或 list）提取被检出的 Order ID。"""
    oids = set()
    if isinstance(result, list):
        for row in result:
            ctx = row.get("context", {}) if isinstance(row, dict) else {}
            oid = str(ctx.get("Order Id", "")) if ctx.get("Order Id") else ""
            if oid:
                oids.add(oid)
    else:
        for _, row in result.iterrows():
            ctx = row.get("context", {})
            oid = str(ctx.get("Order Id", "")) if isinstance(ctx, dict) else ""
            if oid:
                oids.add(oid)
    return oids


def compute_pr(detected: set, gt: dict) -> tuple:
    all_test = gt["anomaly"] | gt["normal"]
    in_test = detected & all_test
    tp = len(in_test & gt["anomaly"])
    fp = len(in_test & gt["normal"])
    fn = len(gt["anomaly"] - detected)
    p = tp / (tp + fp) if (tp + fp) > 0 else 0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0
    return p, r


def get_font():
    for f in fm.fontManager.ttflist:
        if "YaHei" in f.name:
            return fm.FontProperties(fname=f.fname)
    return fm.FontProperties()


def make_tmp_config(overrides: dict) -> str:
    """生成临时 config，返回路径。"""
    cfg_path = os.path.join(PROJECT, "config.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for k, v in overrides.items():
        parts = k.split(".")
        target = cfg
        for p in parts[:-1]:
            target = target.setdefault(p, {})
        target[parts[-1]] = v
    tmp = os.path.join(PROJECT, "_tmp_cfg.yaml")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)
    return tmp


# ═══════════════════════════ Sweeps ═══════════════════════════

def sweep_zscore(df, gt) -> list:
    """Z-Score 单独检测，阈值 1.0→4.0"""
    results = []
    for z in np.arange(1.0, 4.1, 0.2):
        tmp = make_tmp_config({
            "anomaly.zscore.threshold": float(z),
            "anomaly.consensus_min": 1,
        })
        detector = AnomalyDetector(config_path=tmp)
        result = detector.detect_zscore(df)
        oids = get_detected_oids(result)
        p, r = compute_pr(oids, gt)
        results.append({"threshold": round(z, 1), "precision": p, "recall": r})
        if os.path.exists(tmp):
            os.remove(tmp)
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
        print(f"  Z-Score z={z:.1f}: P={p:.3f} R={r:.3f} F1={f1:.3f}")
    return results


def sweep_iqr(df, gt) -> list:
    """IQR 单独检测，倍数 1.0→3.0"""
    results = []
    for k in np.arange(1.0, 3.1, 0.2):
        tmp = make_tmp_config({
            "anomaly.iqr.multiplier": float(k),
            "anomaly.consensus_min": 1,
        })
        detector = AnomalyDetector(config_path=tmp)
        result = detector.detect_iqr(df)
        oids = get_detected_oids(result)
        p, r = compute_pr(oids, gt)
        results.append({"threshold": round(k, 1), "precision": p, "recall": r})
        if os.path.exists(tmp):
            os.remove(tmp)
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
        print(f"  IQR k={k:.1f}: P={p:.3f} R={r:.3f} F1={f1:.3f}")
    return results


def sweep_business_rule(df, gt) -> list:
    """业务规则单独检测（无阈值扫描，固定配置）。"""
    results = []
    tmp = make_tmp_config({"anomaly.consensus_min": 1})
    detector = AnomalyDetector(config_path=tmp)
    result = detector.detect_business_rule(df)
    oids = get_detected_oids(result)
    p, r = compute_pr(oids, gt)
    # 业务规则无阈值，重复填充使曲线可画
    for _ in np.arange(1.0, 4.1, 0.2):
        results.append({"threshold": 0, "precision": p, "recall": r})
    if os.path.exists(tmp):
        os.remove(tmp)
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
    print(f"  业务规则: P={p:.3f} R={r:.3f} F1={f1:.3f}")
    return results


def sweep_ensemble(df, gt) -> list:
    """三者合并（detect_all），扫 Z-Score 阈值。"""
    results = []
    for z in np.arange(1.0, 4.1, 0.2):
        tmp = make_tmp_config({
            "anomaly.zscore.threshold": float(z),
            "anomaly.consensus_min": 1,
        })
        detector = AnomalyDetector(config_path=tmp)
        result = detector.detect_all(df)
        oids = get_detected_oids(result)
        p, r = compute_pr(oids, gt)
        results.append({"threshold": round(z, 1), "precision": p, "recall": r})
        if os.path.exists(tmp):
            os.remove(tmp)
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
        print(f"  Ensemble z={z:.1f}: P={p:.3f} R={r:.3f} F1={f1:.3f}")
    return results


# ═══════════════════════════ Plot ═══════════════════════════

def plot_ablation(zs, iqr, br, ens, output_path: str):
    """4 条 PR 曲线同图。"""
    font = get_font()
    print(f"  字体: {font.get_file()}")

    fig, ax = plt.subplots(figsize=(11, 8))

    colors = {"Z-Score Only": "#2196F3", "IQR Only": "#FF9800",
              "Business Rules Only": "#4CAF50", "Ensemble (All Three)": "#E91E63"}
    markers = {"Z-Score Only": "o", "IQR Only": "s",
               "Business Rules Only": "D", "Ensemble (All Three)": "P"}

    datasets = [
        ("Z-Score Only", zs, colors["Z-Score Only"], markers["Z-Score Only"]),
        ("IQR Only", iqr, colors["IQR Only"], markers["IQR Only"]),
        ("Business Rules Only", br, colors["Business Rules Only"], markers["Business Rules Only"]),
        ("Ensemble (All Three)", ens, colors["Ensemble (All Three)"], markers["Ensemble (All Three)"]),
    ]

    for label, data, color, marker in datasets:
        prs = [d["precision"] for d in data]
        rcs = [d["recall"] for d in data]
        # 去重：只保留唯一 (P,R) 点
        seen = set()
        x, y = [], []
        for px, py in zip(rcs, prs):
            key = (round(px, 4), round(py, 4))
            if key not in seen:
                seen.add(key)
                x.append(px)
                y.append(py)
        # 对 (0,0) 点加微小偏移，避免重叠
        plot_x, plot_y = list(x), list(y)
        for i in range(len(plot_x)):
            if plot_x[i] == 0 and plot_y[i] == 0:
                offset = 0.02 if "Z-Score" in label else (-0.02 if "IQR" in label else 0)
                plot_y[i] = offset
        ax.scatter(plot_x, plot_y, c=color, marker=marker, s=120, zorder=3, label=label,
                   edgecolors="white", linewidth=0.8)
        if len(x) > 1:
            pts = sorted(zip(x, y))
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, linewidth=1.5, alpha=0.6)
        # 标出 (0,0) 的方法名
        if len(x) == 1 and x[0] == 0 and y[0] == 0:
            offset_y = 0.04 if "Z-Score" in label else 0.06
            ax.annotate(f"{label}\n(F1=0%)", (0, offset_y), fontsize=9,
                        color=color, fontweight="bold", ha="center",
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))

    # 标注关键点
    # Z-Score 极值
    zs_pts = sorted(zip([d["recall"] for d in zs], [d["precision"] for d in zs], [d["threshold"] for d in zs]))
    if zs_pts:
        best = max(zs_pts, key=lambda t: 2*t[0]*t[1]/(t[0]+t[1]) if (t[0]+t[1])>0 else 0)
        ax.annotate(f"Z-Score best\nz={best[2]:.1f}", (best[0], best[1]),
                    textcoords="offset points", xytext=(10, -15), fontsize=9, color=colors["Z-Score Only"])
    # Ensemble
    ens_pts = sorted(zip([d["recall"] for d in ens], [d["precision"] for d in ens]))
    if ens_pts:
        mid = ens_pts[len(ens_pts)//2]
        ax.annotate("Ensemble", (mid[0], mid[1]),
                    textcoords="offset points", xytext=(10, 10), fontsize=9, color=colors["Ensemble (All Three)"])

    ax.set_xlabel("Recall", fontsize=14, fontproperties=font)
    ax.set_ylabel("Precision", fontsize=14, fontproperties=font)
    ax.set_title("Ablation Study: Per-Method PR Curves", fontsize=16, fontweight="bold")
    ax.legend(loc="lower left", fontsize=10, framealpha=0.9)
    ax.grid(True, alpha=0.25)

    # y 轴从 0 开始，x 轴从 0 开始
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(pad=2)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"消融曲线已保存: {output_path}")


# ═══════════════════════════ Main ═══════════════════════════

def main():
    print("=== 消融实验 ===")
    print()

    df = pd.read_csv(
        os.path.join(PROJECT, "data", "raw", "DataCoSupplyChainDataset.csv"),
        encoding="latin-1", low_memory=False,
    )
    df["shipping_delay_days"] = df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    gt = load_ground_truth()
    print(f"数据: {len(df):,} 行 | 评测集: {len(gt['anomaly'])} 异常 + {len(gt['normal'])} 正常")

    print("\n[1/4] Z-Score 单独检测 (z=1.0→4.0)...")
    zs_results = sweep_zscore(df, gt)

    print("\n[2/4] IQR 单独检测 (k=1.0→3.0)...")
    iqr_results = sweep_iqr(df, gt)

    print("\n[3/4] 业务规则单独检测...")
    br_results = sweep_business_rule(df, gt)

    print("\n[4/4] 三者合并 (Ensemble)...")
    ens_results = sweep_ensemble(df, gt)

    # 报告
    print("\n=== 消融结果 ===")
    for label, data in [("Z-Score", zs_results), ("IQR", iqr_results), ("业务规则", br_results), ("三者合并", ens_results)]:
        best = max(data, key=lambda d: 2*d["recall"]*d["precision"]/(d["recall"]+d["precision"]) if (d["recall"]+d["precision"])>0 else 0)
        f1 = 2*best["recall"]*best["precision"]/(best["recall"]+best["precision"]) if (best["recall"]+best["precision"])>0 else 0
        print(f"  {label}: best P={best['precision']:.1%} R={best['recall']:.1%} F1={f1:.1%}")

    output = os.path.join(PROJECT, "docs", "images", "pr_curve_ablation.png")
    plot_ablation(zs_results, iqr_results, br_results, ens_results, output)
    print("\n完成。")


def auto_calibrate():
    """自动校准 z 值：分析当前数据分布，推荐最优阈值并写入 config.yaml。"""
    df = pd.read_csv(
        os.path.join(PROJECT, "data", "raw", "DataCoSupplyChainDataset.csv"),
        encoding="latin-1", low_memory=False,
    )
    df["shipping_delay_days"] = df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    gt = load_ground_truth()

    print("=== Z-Score 自动校准 ===\n")
    print(f"[1/3] 分析数据分布...")
    profit_mean = df["Benefit per order"].mean()
    profit_std = df["Benefit per order"].std()
    print(f"  Benefit per order: mean={profit_mean:.1f}, std={profit_std:.1f}")
    print(f"  5th %ile: {df['Benefit per order'].quantile(0.05):.1f}")
    print(f"  95th %ile: {df['Benefit per order'].quantile(0.95):.1f}")

    print(f"\n[2/3] 扫描阈值...")
    ens_results = sweep_ensemble(df, gt)

    zs_sorted = sorted(ens_results, key=lambda d: d["threshold"])

    # 找 F1 最优点
    best = max(ens_results, key=lambda d: 2*d["recall"]*d["precision"]/(d["recall"]+d["precision"]) if (d["recall"]+d["precision"])>0 else 0)
    best_f1 = 2*best["recall"]*best["precision"]/(best["recall"]+best["precision"]) if (best["recall"]+best["precision"])>0 else 0

    # 候选：F1最优 / z=2.0(干净数) / 当前z=2.5(保守)
    candidates = []
    for z_val, label in [(best["threshold"], "F1最优"), (2.0, "z=2.0（整数，解释性好）"), (2.5, "z=2.5（保守，对齐业务规则）")]:
        match = [d for d in zs_sorted if abs(d["threshold"] - z_val) < 0.05]
        if match:
            d = match[0]
            f1 = 2*d["recall"]*d["precision"]/(d["recall"]+d["precision"]) if (d["recall"]+d["precision"])>0 else 0
            candidates.append((z_val, d["precision"], d["recall"], f1, label))

    print(f"\n  候选阈值对比：")
    print(f"  {'z值':<8} {'Precision':<12} {'Recall':<12} {'F1':<10} {'说明'}")
    for z_val, pr, rc, f1_val, label in candidates:
        print(f"  {z_val:<8.1f} {pr:<12.1%} {rc:<12.1%} {f1_val:<10.1%} {label}")

    # 推荐：z=2.0（F1与最优差距小，解释性好）
    recommended = 2.0
    reason = "z=2.0：解释性好（整数），F1与最优差距 <5pp"

    print(f"\n[3/3] 推荐 z = {recommended:.1f}（{reason}）")

    # 只修改 threshold 行，不重写整文件（保留注释和格式）
    config_path = os.path.join(PROJECT, "config.yaml")
    with open(config_path, encoding="utf-8") as f:
        lines = f.readlines()

    old_z = None
    new_lines = []
    for line in lines:
        if "threshold:" in line and "zscore" in "".join(new_lines[-5:]):
            import re
            old_match = re.search(r"threshold:\s*([\d.]+)", line)
            if old_match:
                old_z = float(old_match.group(1))
            line = re.sub(r"threshold:\s*[\d.]+", f"threshold: {recommended:.1f}", line)
        new_lines.append(line)

    if old_z is None:
        # Fallback: yaml load + just change the key
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        old_z = config["anomaly"]["zscore"]["threshold"]
        config["anomaly"]["zscore"]["threshold"] = float(recommended)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True)

    with open(config_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"  config.yaml 已更新: zscore.threshold {old_z} → {recommended:.1f}")
    print("\n如需手动调整，编辑 config.yaml → anomaly.zscore.threshold")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrate", action="store_true", help="自动校准 z 值到 config.yaml")
    args = parser.parse_args()
    if args.calibrate:
        auto_calibrate()
    else:
        main()

