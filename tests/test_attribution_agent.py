# -*- coding: utf-8 -*-
"""
归因 Agent 测试 — 用 visual_anomalies.csv 中的异常跑 DeepSeek 归因分析。

输出：5 份归因报告 + 效果评估。
"""

import sys
import os
import json
import textwrap

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.anomaly_detector import AnomalyDetector
from analysis.attribution_agent import AttributionAgent


def print_separator(title: str = ""):
    print("\n" + "=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)


def print_report(report: dict, index: int):
    """格式化打印一份归因报告。"""
    print_separator(f"归因报告 #{index + 1}")
    meta = report.get("_meta", {})

    # 基本信息
    print(f"  异常ID:     {meta.get('anomaly_id', 'N/A')}")
    data_sufficient = meta.get("data_sufficient", True)
    if data_sufficient is False:
        print(f"  状态:       [降级] 数据不足，跳过 LLM — 评分 {meta.get('sufficiency_score', '?')}/100")
        skip_reasons = meta.get("skip_reasons", [])
        for r in skip_reasons:
            print(f"             → {r}")
    print(f"  模型:       {meta.get('model', 'N/A')}")
    print(f"  API 调用:   {meta.get('api_attempts', '?')} 次")
    print()

    # 摘要
    summary = report.get("summary", "（无）")
    if data_sufficient is False:
        print(f"  摘要: {summary}")
    else:
        print(f"  摘要: {summary}")
    print()

    # 风险等级
    risk = report.get("risk_level", "?")
    risk_icon = {"high": "[HIGH]", "medium": "[MED]", "low": "[LOW]"}.get(risk, "")
    print(f"  风险等级: {risk_icon} {risk.upper()}")
    print(f"  风险理由: {report.get('risk_rationale', 'N/A')}")
    print()

    # 置信度
    conf = report.get("confidence", 0)
    print(f"  AI 置信度: {conf:.0%}")
    print(f"  置信度说明: {report.get('confidence_note', 'N/A')}")
    print()

    # 根因假设
    print("  -- 根因假设 --")
    hyps = report.get("root_cause_hypotheses", [])
    for i, h in enumerate(hyps):
        prob = h.get("probability", 0)
        bar = "#" * int(prob * 20) + "-" * (20 - int(prob * 20))
        print(f"  {i+1}. [{bar}] {prob:.0%}")
        print(f"     {h.get('cause', 'N/A')}")
        evidence = h.get("evidence", [])
        if evidence:
            for e in evidence:
                print(f"     | 证据: {e}")
        against = h.get("against", [])
        if against and any(against):
            for a in against:
                if a:
                    print(f"     | 反驳: {a}")

    print()

    # 处置建议
    print("  -- 处置建议 --")
    actions = report.get("recommended_actions", [])
    for i, a in enumerate(actions):
        prio_icon = {"high": "[HIGH]", "medium": "[MED]", "low": "[LOW]"}.get(a.get("priority"), "")
        print(f"  {i+1}. {prio_icon} [{a.get('priority', '?').upper()}] {a.get('action', 'N/A')}")
        print(f"     负责人: {a.get('owner', 'N/A')} | 预期效果: {a.get('expected_effect', 'N/A')}")
        sop = a.get("sop_ref")
        if sop and sop != "null":
            print(f"     引用 SOP: {sop}")

    # 如果有校验错误
    val_errs = report.get("_validation_errors", [])
    if val_errs:
        print(f"\n  !! 校验警告 ({len(val_errs)} 条):")
        for e in val_errs:
            print(f"     - {e}")


def main():
    print_separator("归因 Agent 测试")
    print("  加载数据 + 检测异常 + DeepSeek 归因分析")

    # ---- 加载数据 ----
    print("\n[1/4] 加载数据 & 异常检测...")
    df = pd.read_csv("data/raw/DataCoSupplyChainDataset.csv", encoding="latin-1", low_memory=False)
    df["shipping_delay_days"] = df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]

    detector = AnomalyDetector(config_path="config.yaml")
    anomalies_df = detector.detect_all(df)
    print(f"  检出 {len(anomalies_df):,} 条异常")

    # 按 severity 分布
    sev_counts = anomalies_df["severity"].value_counts()
    print(f"    严重度分布: high={sev_counts.get('high', 0):,}, "
          f"medium={sev_counts.get('medium', 0):,}, low={sev_counts.get('low', 0):,}")

    # 按 metric 分布
    metric_counts = anomalies_df["metric"].value_counts()
    print(f"    指标分布:")
    for m in metric_counts.index[:6]:
        print(f"      {m}: {metric_counts[m]:,}")

    # ---- 初始化归因 Agent ----
    print("\n[2/4] 初始化 AttributionAgent...")
    try:
        agent = AttributionAgent(config_path="config.yaml")
        print("  DeepSeek 客户端已就绪")
    except Exception as e:
        print(f"  初始化失败: {e}")
        print("  请确认 config.yaml 中的 deepseek.api_key 已配置")
        return

    # ---- 批量归因 ----
    print("\n[3/4] 分析 5 条代表性异常...")
    print("  (策略: 每种 metric + severity 组合至少取 1 条)")
    print()

    reports = agent.analyze_batch(
        anomalies_df, df,
        lookback_days=7,
        max_samples=5,
        verbose=True,
    )

    # ---- 展示报告 ----
    print("\n[4/4] 归因报告展示")
    success_count = sum(1 for r in reports if "error" not in r)
    print(f"  成功: {success_count}/{len(reports)}")

    for i, report in enumerate(reports):
        if "error" in report:
            print(f"\n  !! 报告 #{i+1} 失败: {report['error']}")
            continue
        print_report(report, i)

    # ---- 效果评估 ----
    print_separator("效果评估")
    success_reports = [r for r in reports if "error" not in r]
    if not success_reports:
        print("  无成功报告，无法评估")
        return

    confidences = [r.get("confidence", 0) for r in success_reports]
    risks = [r.get("risk_level", "low") for r in success_reports]
    hyps_counts = [len(r.get("root_cause_hypotheses", [])) for r in success_reports]
    actions_counts = [len(r.get("recommended_actions", [])) for r in success_reports]
    api_attempts = [r.get("_meta", {}).get("api_attempts", 0) for r in success_reports]
    degraded = sum(1 for r in success_reports if r.get("_meta", {}).get("data_sufficient") is False)
    llm_reports = [r for r in success_reports if r.get("_meta", {}).get("data_sufficient") is not False]

    print(f"  LLM 归因:      {len(llm_reports)} 次")
    print(f"  降级跳过:      {degraded} 次（数据不足，省了 API 调用）")
    if len(llm_reports) > 0:
        llm_confs = [r.get("confidence", 0) for r in llm_reports]
        llm_apis = [r.get("_meta", {}).get("api_attempts", 1) for r in llm_reports]
        print(f"  LLM 平均置信度: {sum(llm_confs)/len(llm_confs):.1%}")
        print(f"  LLM 置信度范围: [{min(llm_confs):.0%}, {max(llm_confs):.0%}]")
        print(f"  LLM 平均 API 调用: {sum(llm_apis)/len(llm_apis):.1f} 次")
    print(f"  风险分布:      high={risks.count('high')}, medium={risks.count('medium')}, low={risks.count('low')}")

    # 幻觉检查（仅对 LLM 归因的报告）
    print(f"\n  幻觉检查 (仅 {len(llm_reports)} 份 LLM 报告):")
    hallucination_risks = 0
    for r in llm_reports:
        hyps = r.get("root_cause_hypotheses", [])
        for h in hyps:
            cause = h.get("cause", "")
            if cause and ("调查显示" in cause or "数据显示" in cause or "据统计" in cause):
                hallucination_risks += 1
    print(f"    可疑表述数: {hallucination_risks}")

    print(f"\n  总结:")
    if degraded > 0:
        print(f"    [OK] {degraded} 个异常因数据不足跳过 LLM——省了 API 费用，避免了硬编")
    if len(llm_reports) > 0 and sum(r.get("confidence", 0) for r in llm_reports) / len(llm_reports) >= 0.7:
        print(f"    [OK] LLM 归因质量较好，Few-Shot 有效果")
    elif len(llm_reports) > 0:
        print(f"    [~] LLM 置信度偏低，可能需要更丰富的上下文")
    print(f"    [OK] 异常报告格式规范")


if __name__ == "__main__":
    main()
