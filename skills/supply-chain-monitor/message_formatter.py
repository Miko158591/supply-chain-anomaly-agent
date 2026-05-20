# -*- coding: utf-8 -*-
"""
飞书推送消息格式化 — 三层信息密度设计。

Layer 1: 日报汇总（每天 8:00，不超过 500 字）
Layer 2: 单条摘要（回复编号时，控制在 150 字内）
Layer 3: 完整归因报告（回复"详情"时，含 evidence + 行动 + SOP）
"""

from typing import Any, Dict, List


# ============================================================
# Layer 1 — 日报汇总
# ============================================================

def format_daily_summary(stats: Dict[str, Any], reports: List[Dict],
                         report_date: str) -> str:
    """日报汇总消息 — 3 秒可扫完。

    格式：概览统计 + 需立即关注 Top 3 + 交互指引。
    """
    n_total = stats.get("total_anomalies", 0)
    n_high = stats.get("severity_counts", {}).get("high", 0)
    n_med = stats.get("severity_counts", {}).get("medium", 0)
    n_low = stats.get("severity_counts", {}).get("low", 0)
    n_llm = stats.get("llm_calls", 0)

    lines = [
        f"供应链异常日报 | {report_date}",
        "",
        f"扫描 {n_total:,} 笔记录",
        f"高风险 {n_high:,}  |  中风险 {n_med:,}  |  低风险 {n_low:,}  |  AI 归因 {n_llm}",
        "",
        "【需关注】",
    ]

    # Top 3 — 只取高风险中有归因的
    llm_reports = [r for r in reports
                   if "error" not in r and r.get("_meta", {}).get("data_sufficient") is not False]
    top3 = [r for r in llm_reports if r.get("risk_level") == "high"][:3]

    # 如果高风险不足 3 个，用中风险补齐
    if len(top3) < 3:
        med_reports = [r for r in llm_reports if r.get("risk_level") == "medium"]
        top3 += med_reports[:3 - len(top3)]

    for i, r in enumerate(top3, 1):
        risk = r.get("risk_level", "?")
        emoji = {"high": "1", "medium": "2", "low": "3"}.get(risk, "")
        # 从 summary 中提取关键词（10 字以内）
        summary = r.get("summary", "")
        keyword = _extract_keyword(summary)
        conf = r.get("confidence", 0)
        lines.append(f"{i}. [{risk.upper()}] {keyword} | {conf:.0%}")

    lines.append("")
    lines.append("回复编号看摘要 | 回复\"全部\"看名单 | 回复\"导出\"下载 Excel")

    return "\n".join(lines)


def _extract_keyword(summary: str, max_len: int = 12) -> str:
    """从 summary 中提取最核心的关键词短语。"""
    # 取第一个逗号或句号之前的部分
    for sep in ["，", "。", "、", "——"]:
        if sep in summary:
            summary = summary.split(sep)[0]
    return summary[:max_len]


# ============================================================
# Layer 2 — 单条异常摘要
# ============================================================

def format_anomaly_summary(report: Dict, index: int) -> str:
    """单条异常摘要 — 控制在 150 字内，业务人员 10 秒读懂。

    包含：异常指标数值 + 核心原因 + 首条建议。
    """
    risk = report.get("risk_level", "medium")
    emoji = {"high": "1", "medium": "2", "low": "3"}.get(risk, "")
    conf = report.get("confidence", 0)
    summary = report.get("summary", "")
    meta = report.get("_meta", {})

    # 提取关键数据锚点
    meta = report.get("_meta", {})
    ctx = meta.get("anomaly_context", report.get("context", {}))
    order_id = ctx.get("Order Id", "?")

    lines = [
        f"异常 #{index}  [{risk.upper()}]",
        f"订单 {order_id}  |  置信度 {conf:.0%}",
        "",
    ]

    # 根因（top 1，精简）
    hyps = report.get("root_cause_hypotheses", [])
    if hyps:
        cause = hyps[0].get("cause", "")
        lines.append(f"分析: {_shorten(cause, 80)}")

    # 首条建议
    actions = report.get("recommended_actions", [])
    if actions:
        act = actions[0]
        lines.append(f"行动: {_shorten(act.get('action', ''), 60)}")
        owner = act.get("owner", "")
        if owner:
            lines.append(f"负责人: {owner}")

    lines.append("")
    lines.append("回复\"详情\"看完整归因报告")

    return "\n".join(lines)


# ============================================================
# Layer 3 — 完整归因报告
# ============================================================

def format_anomaly_detail(report: Dict, index: int) -> str:
    """完整归因报告 — 含全部 evidence、建议、SOP 引用。

    用飞书富文本分段，适合认真阅读场景。
    """
    risk = report.get("risk_level", "medium")
    conf = report.get("confidence", 0)
    ctx = report.get("_meta", {}).get("anomaly_context", report.get("context", {}))
    order_id = ctx.get("Order Id", "?")

    lines = [
        f"完整归因报告 #{index}  [{risk.upper()}]",
        f"订单 {order_id}  |  置信度 {conf:.0%}  |  {report.get('summary', '')[:50]}",
        "",
        "【根因分析】",
    ]

    hyps = report.get("root_cause_hypotheses", [])
    for i, h in enumerate(hyps, 1):
        prob_bar = "#" * int(h.get("probability", 0) * 10)
        lines.append(f"{i}. [{prob_bar}] {h.get('probability', 0):.0%}  {h.get('cause', '')}")
        for e in h.get("evidence", []):
            lines.append(f"   + {e}")
        against = [a for a in h.get("against", []) if a]
        for a in against:
            lines.append(f"   - {a}")
        lines.append("")

    lines.append("【处置建议】")
    actions = report.get("recommended_actions", [])
    for i, a in enumerate(actions, 1):
        sop = f" (参考 {a.get('sop_ref')})" if a.get('sop_ref') and a['sop_ref'] != 'null' else ""
        lines.append(f"{i}. [{a.get('priority', '?').upper()}] {a.get('action', '')}{sop}")
        lines.append(f"   负责人: {a.get('owner', '?')}  |  预期: {a.get('expected_effect', '')}")

    lines.append("")
    lines.append(f"风险评估: {report.get('risk_rationale', '')}")
    lines.append(f"置信度说明: {report.get('confidence_note', '')}")

    return "\n".join(lines)


# ============================================================
# 辅助函数
# ============================================================

def _shorten(text: str, max_len: int) -> str:
    """精简文本：优先在句号/逗号处截断。"""
    if len(text) <= max_len:
        return text
    for sep in ["。", "！", "？", "；", "，", "、", " "]:
        pos = text[:max_len].rfind(sep)
        if pos > max_len * 0.6:
            return text[:pos + len(sep)]
    return text[:max_len - 1] + "…"


def format_all_anomalies(reports: List[Dict], stats: Dict) -> str:
    """回复"全部"时列出今天所有已归因异常。"""
    llm_reports = [r for r in reports
                   if "error" not in r and r.get("_meta", {}).get("data_sufficient") is not False]

    if not llm_reports:
        return "今日无已完成归因的异常。"

    lines = [f"今日异常清单（共 {len(llm_reports)} 个）", ""]
    for i, r in enumerate(llm_reports, 1):
        risk = r.get("risk_level", "?")
        ctx = r.get("_meta", {}).get("anomaly_context", r.get("context", {}))
        oid = ctx.get("Order Id", "?")
        keyword = _extract_keyword(r.get("summary", ""), 15)
        conf = r.get("confidence", 0)
        lines.append(f"{i}. [{risk.upper()}] 订单#{oid} | {keyword} | {conf:.0%}")

    lines.append("")
    lines.append("回复编号看摘要，回复\"详情\"看完整报告")
    return "\n".join(lines)
