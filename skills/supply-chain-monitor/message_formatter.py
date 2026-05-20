# -*- coding: utf-8 -*-
"""
飞书推送消息格式化。

Layer 1: 日报卡片 — Top 5 高风险完整信息（根因 + 建议），不截断
交互命令: "全部"/"高风险" → Excel 文件，"中风险" → Excel 文件
"""

from typing import Any, Dict, List


# ============================================================
# Layer 1 — 日报卡片（Top 5 高风险，信息完整不截断）
# ============================================================

def format_daily_summary(stats: Dict[str, Any], reports: List[Dict],
                         report_date: str) -> str:
    """日报卡片 — Top 5 高风险，每条含根因 + 行动建议。

    Feishu lark_md div 上限约 5000 字符，5 条完整信息约 2000 字，安全。
    """
    n_total = stats.get("total_anomalies", 0)
    n_high = stats.get("severity_counts", {}).get("high", 0)
    n_med = stats.get("severity_counts", {}).get("medium", 0)
    n_low = stats.get("severity_counts", {}).get("low", 0)
    n_llm = stats.get("llm_calls", 0)

    lines = [
        f"供应链异常日报 | {report_date}",
        "",
        f"扫描 {n_total:,} 笔 | 高风险 {n_high:,} | 中风险 {n_med:,} | 低风险 {n_low:,} | AI 归因 {n_llm}",
        "",
        "【Top 5 高风险异常】",
        "",
    ]

    # 取高风险 LLM 归因报告，按置信度降序
    llm_reports = [r for r in reports
                   if "error" not in r and r.get("_meta", {}).get("data_sufficient") is not False]
    high_reports = [r for r in llm_reports if r.get("risk_level") == "high"]
    high_reports.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    top5 = high_reports[:5]

    # 如果高风险不足 5 个，用中风险补齐
    if len(top5) < 5:
        med_reports = [r for r in llm_reports if r.get("risk_level") == "medium"]
        med_reports.sort(key=lambda r: r.get("confidence", 0), reverse=True)
        top5 += med_reports[:5 - len(top5)]

    for i, r in enumerate(top5, 1):
        risk = r.get("risk_level", "?")
        emoji = {"high": "\U0001F534", "medium": "\U0001F7E1", "low": "\U0001F7E2"}.get(risk, "")
        conf = r.get("confidence", 0)
        ctx = r.get("_meta", {}).get("anomaly_context", r.get("context", {}))
        oid = ctx.get("Order Id", "?")

        lines.append(f"{emoji} **#{i} 订单 {oid}** | {risk.upper()} | 置信度 {conf:.0%}")

        # 根因 + 建议
        hyps = r.get("root_cause_hypotheses", [])
        if hyps:
            cause = hyps[0].get("cause", "")
            if cause:
                lines.append(f"> 原因: {cause}")
        actions = r.get("recommended_actions", [])
        if actions:
            act = actions[0].get("action", "")
            if act:
                lines.append(f"> 建议: {act}")

        lines.append("")

    lines.append("---")
    lines.append("回复 **\"高风险\"** 下载高风险异常 Excel")
    lines.append("回复 **\"中风险\"** 下载中风险异常 Excel")

    return "\n".join(lines)


# ============================================================
# 辅助
# ============================================================

def _extract_keyword(summary: str, max_len: int = 12) -> str:
    """从 summary 中提取最核心的关键词短语。"""
    for sep in ["，", "。", "、", "——"]:
        if sep in summary:
            summary = summary.split(sep)[0]
    return summary[:max_len]


# ============================================================
# 交互回复（编号 / 详情 → 文本消息）
# ============================================================

def format_anomaly_summary(report: Dict, index: int) -> str:
    """单条异常摘要 — 约 200 字，含根因 + 行动。"""
    risk = report.get("risk_level", "medium")
    emoji = {"high": "\U0001F534", "medium": "\U0001F7E1", "low": "\U0001F7E2"}.get(risk, "")
    conf = report.get("confidence", 0)
    ctx = report.get("_meta", {}).get("anomaly_context", report.get("context", {}))
    oid = ctx.get("Order Id", "?")

    lines = [
        f"{emoji} 异常 #{index} [{risk.upper()}] 订单 {oid} | 置信度 {conf:.0%}",
        "",
    ]

    hyps = report.get("root_cause_hypotheses", [])
    if hyps:
        lines.append(f"原因: {hyps[0].get('cause', '?')}")

    actions = report.get("recommended_actions", [])
    if actions:
        act = actions[0]
        lines.append(f"建议: {act.get('action', '?')}")
        owner = act.get("owner", "")
        if owner:
            lines.append(f"负责人: {owner}")

    return "\n".join(lines)


def format_anomaly_detail(report: Dict, index: int) -> str:
    """完整归因报告 — 含全部 evidence + 建议 + SOP。"""
    risk = report.get("risk_level", "medium")
    conf = report.get("confidence", 0)
    ctx = report.get("_meta", {}).get("anomaly_context", report.get("context", {}))
    oid = ctx.get("Order Id", "?")

    lines = [
        f"\U0001F4CB 完整归因 #{index} [{risk.upper()}] 订单 {oid} | 置信度 {conf:.0%}",
        "",
        "【根因分析】",
    ]

    hyps = report.get("root_cause_hypotheses", [])
    for i, h in enumerate(hyps, 1):
        prob_bar = "#" * int(h.get("probability", 0) * 10)
        lines.append(f"{i}. [{prob_bar}] {h.get('probability', 0):.0%}  {h.get('cause', '')}")
        for e in h.get("evidence", []):
            lines.append(f"   + {e}")
        lines.append("")

    lines.append("【处置建议】")
    for i, a in enumerate(report.get("recommended_actions", []), 1):
        sop = f" (参考 {a.get('sop_ref')})" if a.get('sop_ref') and a['sop_ref'] != 'null' else ""
        lines.append(f"{i}. [{a.get('priority', '?').upper()}] {a.get('action', '')}{sop}")
        lines.append(f"   负责人: {a.get('owner', '?')}")

    return "\n".join(lines)
