#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化评测脚本 — 每次 git push 前跑，确保改动不破坏检测/归因质量。

用法:
  python eval/run_eval.py                    # 跑全部指标
  python eval/run_eval.py --skip-llm         # 只跑检测，不调 LLM 评委
  python eval/run_eval.py --output report.md # 输出 Markdown 报告

指标:
  1. 异常检测 Precision / Recall / F1
  2. 归因报告合理性（跨模型评委打分，避免自评作弊）
  3. 端到端延迟
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.anomaly_detector import AnomalyDetector
from analysis.attribution_agent import AttributionAgent
from analysis.pattern_clusterer import cluster_anomalies

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eval")


def load_test_cases(path: str = "eval/test_cases.json") -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("test_cases", [])


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ═══════════════════════════════════════════
# 指标 1：异常检测 Precision / Recall / F1
# ═══════════════════════════════════════════

def eval_detection(df: pd.DataFrame, test_cases: List[Dict],
                   config: dict) -> Dict[str, Any]:
    """跑异常检测，计算 Precision/Recall/F1。"""
    logger.info("=== 指标 1: 异常检测 ===")
    detector = AnomalyDetector(config_path="config.yaml")
    result = detector.detect_all(df)

    detected_ids = set()
    for _, row in result.iterrows():
        ctx = row.get("context", {})
        oid = str(ctx.get("Order Id", "")) if isinstance(ctx, dict) else ""
        if oid:
            detected_ids.add(oid)

    tp = fp = tn = fn = 0
    details = []

    for tc in test_cases:
        oid = str(tc["data_snapshot"].get("Order Id", ""))
        is_anomaly = tc.get("expected", {}).get("is_anomaly", False)
        detected = oid in detected_ids

        if is_anomaly and detected:
            tp += 1
            status = "TP"
        elif is_anomaly and not detected:
            fn += 1
            status = "FN"
        elif not is_anomaly and detected:
            fp += 1
            status = "FP"
        else:
            tn += 1
            status = "TN"

        details.append({
            "id": tc["id"],
            "status": status,
            "expected": is_anomaly,
            "detected": detected,
            "description": tc.get("description", "")[:80],
        })

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    logger.info(f"  Precision: {precision:.2%} ({tp}TP / {tp+fp}Pred)")
    logger.info(f"  Recall:    {recall:.2%} ({tp}TP / {tp+fn}Real)")
    logger.info(f"  F1:        {f1:.2%}")

    failures = [d for d in details if d["status"] in ("FN", "FP")]
    if failures:
        logger.info(f"  Failures ({len(failures)}):")
        for f in failures:
            logger.info(f"    [{f['status']}] {f['id']}: {f['description']}")

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "details": details,
        "total_anomalies_detected": len(result),
    }


# ═══════════════════════════════════════════
# 指标 2：归因合理性（跨模型评委）
# ═══════════════════════════════════════════

ATTRIBUTION_JUDGE_PROMPT = """你是供应链归因质量评审专家。请对以下 AI 归因报告打分。

评分维度（各 1-5 分）：
1. 证据充分性：evidence 是否引用了具体数据（而非泛泛而谈）
2. 逻辑连贯性：从数据到 root cause 的推理链条是否合理
3. 行动可操作性：建议是否具体、有负责人、有预期效果
4. 诚实度：是否如实标注 confidence、列出反驳证据（against）
5. 整体质量：综合判断

异常数据：
{anomaly_context}

AI 归因报告：
{attribution_json}

请直接输出 JSON（不要加其他文字）：
{{"证据充分性": 1-5, "逻辑连贯性": 1-5, "行动可操作性": 1-5, "诚实度": 1-5, "整体质量": 1-5, "评语": "一句话总结"}}"""


def eval_attribution_quality(test_cases: List[Dict], config: dict,
                             skip_llm: bool = False) -> Dict[str, Any]:
    """用跨模型评委评测归因质量。"""
    logger.info("=== 指标 2: 归因合理性 ===")
    anomaly_cases = [tc for tc in test_cases
                     if tc.get("expected", {}).get("is_anomaly")]

    if skip_llm:
        logger.info("  跳过 LLM 评委（--skip-llm）")
        return {"score": None, "skipped": True, "reason": "--skip-llm flag"}

    if not anomaly_cases:
        return {"score": None, "skipped": True, "reason": "无异常样本"}

    # 用 DeepSeek 作为归因模型
    agent = AttributionAgent(config_path="config.yaml")
    df = pd.read_csv(os.path.join("data", "raw", "DataCoSupplyChainDataset.csv"),
                     encoding="latin-1", low_memory=False)
    df["shipping_delay_days"] = (
        df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    )

    # 评委模型（可在 config.yaml → llm.judge 中配置为任何 OpenAI 兼容模型）
    from openai import OpenAI
    judge_cfg = config.get("llm", {}).get("judge", {})
    if not judge_cfg.get("api_key"):
        # 兜底：用归因模型的 key
        judge_cfg = config.get("llm", {}).get("deepseek", {})
    judge = OpenAI(
        api_key=judge_cfg.get("api_key", ""),
        base_url=judge_cfg.get("base_url", "https://api.deepseek.com"),
    )
    judge_model = judge_cfg.get("model", "deepseek-chat")
    judge_max_tokens = judge_cfg.get("max_tokens", 2048)

    scores = []
    for tc in anomaly_cases[:5]:  # 取样 5 条控制成本
        # 构造异常记录
        snapshot = tc["data_snapshot"]
        anomaly = {
            "anomaly_id": tc["id"],
            "timestamp": "2026-05-21",
            "metric": tc.get("expected", {}).get("primary_metric", "Benefit per order"),
            "value": snapshot.get("Benefit per order", 0),
            "expected_range": [None, None],
            "severity": tc.get("expected", {}).get("severity", "high"),
            "detection_method": "manual_label",
            "context": snapshot,
        }

        try:
            report = agent.analyze(anomaly, df, lookback_days=7)
            if "error" in report:
                scores.append({"id": tc["id"], "score": 0, "error": report.get("error")})
                continue

            # 让评委打分
            judge_prompt = ATTRIBUTION_JUDGE_PROMPT.format(
                anomaly_context=json.dumps(snapshot, ensure_ascii=False, indent=2),
                attribution_json=json.dumps(report, ensure_ascii=False, indent=2)[:3000],
            )

            resp = judge.chat.completions.create(
                model=judge_model,
                messages=[{"role": "user", "content": judge_prompt}],
                temperature=0.1,
                max_tokens=judge_max_tokens,
            )
            judge_result = json.loads(resp.choices[0].message.content)
            judge_result["id"] = tc["id"]
            scores.append(judge_result)
            logger.info(f"  {tc['id']}: 整体 {judge_result.get('整体质量', '?')}/5 | {judge_result.get('评语', '')}")

        except Exception as e:
            logger.warning(f"  {tc['id']} 评测失败: {e}")
            scores.append({"id": tc["id"], "score": 0, "error": str(e)})

    avg_overall = (
        sum(s.get("整体质量", 0) for s in scores) / len(scores)
        if scores else 0
    )

    return {
        "judge_model": judge_model,
        "samples_evaluated": len(scores),
        "average_score": round(avg_overall, 2),
        "dimension_scores": {
            dim: round(sum(s.get(dim, 0) for s in scores) / len(scores), 2)
            for dim in ["证据充分性", "逻辑连贯性", "行动可操作性", "诚实度", "整体质量"]
            if any(dim in s for s in scores)
        },
        "details": scores,
    }


# ═══════════════════════════════════════════
# 指标 3：端到端延迟
# ═══════════════════════════════════════════

def eval_latency(df: pd.DataFrame, config: dict) -> Dict[str, Any]:
    """测量异常检测 + 归因的端到端延迟。"""
    logger.info("=== 指标 3: 端到端延迟 ===")

    # 检测延迟
    t0 = time.time()
    detector = AnomalyDetector(config_path="config.yaml")
    result = detector.detect_all(df)
    detection_time = time.time() - t0

    # 归因延迟（单条）
    agent = AttributionAgent(config_path="config.yaml")
    high = result[result["severity"] == "high"]
    if len(high) > 0:
        sample = high.iloc[0].to_dict()
        t0 = time.time()
        try:
            agent.analyze(sample, df, lookback_days=7)
            attribution_time = time.time() - t0
        except Exception:
            attribution_time = -1
    else:
        attribution_time = 0

    total = detection_time + max(attribution_time, 0)

    logger.info(f"  检测:    {detection_time:.1f}s ({len(result):,} 条异常)")
    logger.info(f"  归因:    {attribution_time:.1f}s (单条)")
    logger.info(f"  端到端:  {total:.1f}s")

    return {
        "detection_seconds": round(detection_time, 2),
        "attribution_seconds_per_item": round(attribution_time, 2),
        "total_seconds": round(total, 2),
        "anomalies_per_second": round(len(result) / detection_time, 1)
        if detection_time > 0 else 0,
    }


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="供应链异常监控评测")
    parser.add_argument("--skip-llm", action="store_true",
                        help="跳过 LLM 评委评测（省 API 费）")
    parser.add_argument("--output", type=str, default=None,
                        help="输出 Markdown 报告路径")
    args = parser.parse_args()

    config = load_config()
    test_cases = load_test_cases()

    logger.info(f"加载 {len(test_cases)} 个测试用例")

    # 加载数据
    df = pd.read_csv(os.path.join("data", "raw", "DataCoSupplyChainDataset.csv"),
                     encoding="latin-1", low_memory=False)
    df["shipping_delay_days"] = (
        df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    )
    logger.info(f"数据加载: {len(df):,} 行")

    results = {
        "date": datetime.now().isoformat(),
        "test_cases_count": len(test_cases),
        "anomaly_cases": sum(1 for tc in test_cases
                           if tc.get("expected", {}).get("is_anomaly")),
        "normal_cases": sum(1 for tc in test_cases
                          if not tc.get("expected", {}).get("is_anomaly")),
    }

    # 指标 1
    results["detection"] = eval_detection(df, test_cases, config)

    # 指标 2
    results["attribution"] = eval_attribution_quality(
        test_cases, config, skip_llm=args.skip_llm
    )

    # 指标 3
    results["latency"] = eval_latency(df, config)

    # 输出
    print()
    print("=" * 50)
    print("  评测总结")
    print("=" * 50)
    print(f"  检测 Precision: {results['detection']['precision']:.2%}")
    print(f"  检测 Recall:    {results['detection']['recall']:.2%}")
    print(f"  检测 F1:        {results['detection']['f1']:.2%}")
    if results["attribution"].get("average_score"):
        print(f"  归因均分:      {results['attribution']['average_score']}/5")
    print(f"  端到端延迟:    {results['latency']['total_seconds']:.1f}s")
    print("=" * 50)

    # 保存 JSON
    os.makedirs("eval/results", exist_ok=True)
    json_path = f"eval/results/eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"结果已保存: {json_path}")

    # Markdown 报告
    if args.output:
        generate_markdown_report(results, args.output)
        logger.info(f"报告已保存: {args.output}")


def generate_markdown_report(results: Dict, path: str):
    """生成 Markdown 评测报告。"""
    tmpl_path = os.path.join(os.path.dirname(__file__), "report_template.md")
    if os.path.exists(tmpl_path):
        with open(tmpl_path, "r", encoding="utf-8") as f:
            template = f.read()
        report = template.format(**results, **results.get("detection", {}),
                                 **results.get("attribution", {}),
                                 **results.get("latency", {}))
    else:
        report = f"""# 供应链异常监控评测报告

**日期**: {results['date']}

## 1. 异常检测
- Precision: {results['detection']['precision']:.2%}
- Recall: {results['detection']['recall']:.2%}
- F1: {results['detection']['f1']:.2%}
- TP: {results['detection']['tp']} FP: {results['detection']['fp']} TN: {results['detection']['tn']} FN: {results['detection']['fn']}

## 2. 归因合理性
{results['attribution']}

## 3. 端到端延迟
- 检测: {results['latency']['detection_seconds']}s
- 归因单条: {results['latency']['attribution_seconds_per_item']}s
- 总延迟: {results['latency']['total_seconds']}s
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
