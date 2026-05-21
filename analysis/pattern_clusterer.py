# -*- coding: utf-8 -*-
"""
异常模式聚类 — 从逐条报告升级到识别异常背后的模式。

基于规则的方法（rule_based）：可解释、可调试，不依赖黑盒 ML。
"""

from typing import Any, Dict, List, Optional, Set, Tuple
import logging

logger = logging.getLogger("pattern-clusterer")


# ═══════════════════════════════════════════
# 模式定义
# ═══════════════════════════════════════════

def _extract_ctx(ctx: Dict, key: str) -> str:
    if not isinstance(ctx, dict):
        return "?"
    return str(ctx.get(key, "?"))


def _get_order_id(anomaly: Dict) -> str:
    ctx = anomaly.get("context", {})
    return _extract_ctx(ctx, "Order Id")


def _get_category(anomaly: Dict) -> str:
    ctx = anomaly.get("context", {})
    return _extract_ctx(ctx, "Category Name")


def _get_region(anomaly: Dict) -> str:
    ctx = anomaly.get("context", {})
    return _extract_ctx(ctx, "Order Region")


def _get_market(anomaly: Dict) -> str:
    ctx = anomaly.get("context", {})
    return _extract_ctx(ctx, "Market")


# ═══════════════════════════════════════════
# 模式 1：延迟 + 亏损复合（同一订单）
# ═══════════════════════════════════════════

def cluster_delay_loss_composite(anomalies: List[Dict]) -> List[Dict]:
    """识别延迟+亏损复合异常——聚合所有符合条件的订单为一个模式。

    聚合逻辑：找出所有同时出现延迟类和亏损类异常的订单，归入同一模式。
    """
    by_order: Dict[str, List[Dict]] = {}
    for a in anomalies:
        oid = _get_order_id(a)
        if oid and oid != "?":
            by_order.setdefault(oid, []).append(a)

    composite_orders: List[str] = []
    composite_items: List[Dict] = []
    composite_cats: Set[str] = set()
    composite_regions: Set[str] = set()

    for oid, items in by_order.items():
        if len(items) < 2:
            continue

        metrics = {item.get("metric", "") for item in items}
        is_delay = any(
            m in metrics for m in ("shipping_delay_days", "avg_delay", "late_rate")
        )
        is_loss = any(
            m in metrics
            for m in ("Benefit per order", "Order Item Profit Ratio")
        )
        has_negative = any(
            item.get("metric", "") in ("Benefit per order", "Order Item Profit Ratio")
            and isinstance(item.get("value"), (int, float))
            and item.get("value", 0) < 0
            for item in items
        )

        if is_delay and is_loss and has_negative:
            composite_orders.append(oid)
            composite_items.extend(items)
            for item in items:
                composite_cats.add(_get_category(item))
                composite_regions.add(_get_region(item))

    if not composite_orders:
        return []

    note = (f"共 {len(composite_orders)} 个订单呈现此特征, "
            f"{'系统性问题，建议优先排查' if len(composite_orders) >= 5 else '可能为巧合，需跟踪'}"
            ) if len(composite_orders) < 3 else None

    return [{
        "pattern_id": "delay_loss_composite",
        "pattern_name": "延迟+亏损复合",
        "pattern_desc": (
            f"{len(composite_orders)} 个订单同时出现交付延迟和财务亏损，"
            f"涉及 {len(composite_cats)} 个品类"
        ),
        "order_ids": sorted(composite_orders),
        "anomaly_count": len(composite_items),
        "order_count": len(composite_orders),
        "severity": "high",
        "categories": sorted(composite_cats),
        "regions": sorted(composite_regions),
        "items": composite_items,
        "sample_size_note": note,
    }]


# ═══════════════════════════════════════════
# 模式 2：单品类集中异常
# ═══════════════════════════════════════════

def cluster_category_concentration(anomalies: List[Dict],
                                   min_orders: int = 2) -> List[Dict]:
    """识别同一品类内多条异常集中的模式。

    只取高风险异常，按品类分组，品类的异常订单数超过阈值则标记为模式。
    """
    high_anomalies = [a for a in anomalies if a.get("severity") == "high"]
    if not high_anomalies:
        high_anomalies = anomalies  # 兜底：没有高风险就用全部

    # 按品类分组
    by_category: Dict[str, List[Dict]] = {}
    for a in high_anomalies:
        cat = _get_category(a)
        if cat and cat != "?":
            by_category.setdefault(cat, []).append(a)

    # 计算全局平均（每品类异常订单数）
    if not by_category:
        return []

    counts = [len(items) for items in by_category.values()]
    avg_count = sum(counts) / len(counts)

    patterns = []
    for cat, items in by_category.items():
        if len(items) < min_orders:
            continue

        orders = {_get_order_id(a) for a in items}
        if len(orders) < min_orders:
            continue

        metrics = {a.get("metric", "") for a in items}
        regions = {_get_region(a) for a in items}
        note = None if len(orders) >= 5 else (
            f"仅 {len(orders)} 个订单，样本数较少，可能为巧合"
        )

        patterns.append({
            "pattern_id": "category_concentration",
            "pattern_name": f"品类集中 — {cat}",
            "pattern_desc": (
                f"品类「{cat}」出现 {len(orders)} 个订单共 {len(items)} 条高风险异常"
            ),
            "order_ids": sorted(orders),
            "anomaly_count": len(items),
            "order_count": len(orders),
            "severity": "high",
            "categories": [cat],
            "regions": sorted(regions),
            "primary_metrics": sorted(metrics),
            "items": items,
            "sample_size_note": note,
        })

    # 只保留 top 3 品类（按异常订单数降序）
    patterns.sort(key=lambda p: p["order_count"], reverse=True)
    return patterns[:3]


# ═══════════════════════════════════════════
# 模式 3：单区域异常集中
# ═══════════════════════════════════════════

def cluster_region_concentration(anomalies: List[Dict],
                                 min_orders: int = 2) -> List[Dict]:
    """识别同一区域内多条异常集中的模式。"""
    high_anomalies = [a for a in anomalies if a.get("severity") == "high"]
    if not high_anomalies:
        high_anomalies = anomalies

    by_region: Dict[str, List[Dict]] = {}
    for a in high_anomalies:
        region = _get_region(a)
        if region and region != "?":
            by_region.setdefault(region, []).append(a)

    if not by_region:
        return []

    counts = [len(items) for items in by_region.values()]
    avg_count = sum(counts) / len(counts)

    patterns = []
    for region, items in by_region.items():
        orders = {_get_order_id(a) for a in items}
        if len(orders) < min_orders:
            continue

        if len(orders) < avg_count * 1.5:
            continue

        categories = {_get_category(a) for a in items}
        note = None if len(orders) >= 5 else (
            f"仅 {len(orders)} 个订单，样本数较少，可能为正常波动"
        )

        patterns.append({
            "pattern_id": "region_concentration",
            "pattern_name": f"区域集中 — {region}",
            "pattern_desc": (
                f"区域「{region}」出现 {len(orders)} 个订单共 {len(items)} 条高风险异常"
            ),
            "order_ids": sorted(orders),
            "anomaly_count": len(items),
            "order_count": len(orders),
            "severity": "high",
            "categories": sorted(categories),
            "regions": [region],
            "items": items,
            "sample_size_note": note,
        })

    # 只保留 top 3 区域
    patterns.sort(key=lambda p: p["order_count"], reverse=True)
    return patterns[:3]


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def cluster_anomalies(
    anomalies: List[Dict],
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """对所有异常运行模式聚类，返回分类后的结果。

    Args:
        anomalies: 异常检测结果列表（每条包含 metric/severity/value/context）
        config: 聚类配置（可选，使用 config.yaml 中的 cluster 段）

    Returns:
        {
            "patterns": [...],           # 识别到的异常模式
            "orphans": [...],            # 未归入任何模式的异常
            "stats": {...},              # 聚类统计
        }
    """
    cfg = config or {}
    cluster_cfg = cfg.get("cluster", cfg.get("anomaly", {}).get("cluster", {}))
    min_size = cluster_cfg.get("min_size", 2) if isinstance(cluster_cfg, dict) else 2

    if not anomalies:
        return {"patterns": [], "orphans": [], "stats": {"total": 0}}

    all_patterns = []

    # 运行各规则
    composites = cluster_delay_loss_composite(anomalies)
    all_patterns.extend(composites)

    categories = cluster_category_concentration(anomalies, min_orders=min_size)
    all_patterns.extend(categories)

    regions = cluster_region_concentration(anomalies, min_orders=min_size)
    all_patterns.extend(regions)

    # 去重：同一个异常可能被多个模式命中，按优先级保留
    # 优先级：delay_loss_composite > category > region（更具体优先）
    priority = {
        "delay_loss_composite": 3,
        "category_concentration": 2,
        "region_concentration": 1,
    }

    # 收集已被归入模式的异常
    used_indices: Set[int] = set()
    final_patterns = []
    for pat in sorted(all_patterns,
                      key=lambda p: priority.get(p["pattern_id"], 0),
                      reverse=True):
        pat_items = pat.pop("items", [])
        pat_indices = {id(a) for a in pat_items}
        new_indices = pat_indices - used_indices
        if not new_indices:
            continue
        used_indices |= pat_indices
        pat["anomaly_count"] = len(new_indices)
        # 更新 order_ids：只用新订单
        pat_orders = {_get_order_id(a) for a in pat_items if id(a) in new_indices}
        pat["order_ids"] = sorted(pat_orders)
        pat["order_count"] = len(pat_orders)
        if not isinstance(pat.get("sample_size_note"), str) and pat["order_count"] < 3:
            pat["sample_size_note"] = (
                f"仅 {pat['order_count']} 个订单，样本数较少，可能为巧合"
            )
        final_patterns.append(pat)

    # 收集孤立的异常
    orphans = [a for i, a in enumerate(anomalies) if id(a) not in used_indices]

    stats = {
        "total_anomalies": len(anomalies),
        "total_patterns": len(final_patterns),
        "anomalies_in_patterns": len(anomalies) - len(orphans),
        "orphans": len(orphans),
        "pattern_breakdown": [
            {
                "name": p["pattern_name"],
                "type": p["pattern_id"],
                "order_count": p["order_count"],
                "anomaly_count": p["anomaly_count"],
            }
            for p in final_patterns
        ],
    }

    logger.info(
        f"聚类完成: {stats['total_patterns']} 个模式, "
        f"{stats['anomalies_in_patterns']} 条归入模式, "
        f"{stats['orphans']} 条孤立"
    )

    # ROI 估算
    roi = _estimate_roi(final_patterns, anomalies)
    stats["roi"] = roi

    return {"patterns": final_patterns, "orphans": orphans, "stats": stats}


def _estimate_roi(patterns: List[Dict], anomalies: List[Dict]) -> Dict[str, Any]:
    """基于异常模式估算潜在挽回金额（What-if 分析）。

    Returns:
        {
            "total_potential_savings": 总潜在挽回金额,
            "breakdown": [{模式名: 挽回金额, ...}],
            "methodology": "估算方法说明",
        }
    """
    total = 0.0
    breakdown = []

    for pat in patterns:
        items = [a for a in anomalies
                 if a.get("context", {}).get("Order Id")
                 and str(a.get("context", {}).get("Order Id", "")) in pat.get("order_ids", [])]
        if not items:
            continue

        ptype = pat.get("pattern_id", "")
        order_count = pat.get("order_count", 0)
        savings = 0.0
        methodology = ""

        if ptype == "delay_loss_composite":
            # 延迟+亏损：保守假设 30% 的亏损可通过流程优化挽回
            losses = [abs(a.get("value", 0)) for a in items
                      if a.get("metric") in ("Benefit per order", "Order Item Profit Ratio")
                      and isinstance(a.get("value"), (int, float))
                      and a.get("value", 0) < 0]
            avg_loss = sum(losses) / len(losses) if losses else 0
            savings = avg_loss * order_count * 0.30  # 保守 30% 挽回率
            methodology = (
                f"平均单笔亏损 ${avg_loss:,.2f} × {order_count} 单 × 30% 挽回率"
            )

        elif ptype == "category_concentration":
            # 品类集中：优化货位/定价后预计可降低 20% 异常
            avg_benefit = sum(
                abs(a.get("value", 0)) for a in items
                if isinstance(a.get("value"), (int, float))
            ) / max(len(items), 1)
            savings = avg_benefit * order_count * 0.20
            methodology = (
                f"品类优化预计降低 20% 异常 × {order_count} 单 × 均值 ${avg_benefit:,.2f}"
            )

        elif ptype == "region_concentration":
            # 区域集中：优化区域承运商合同可挽回 15%
            avg_benefit = sum(
                abs(a.get("value", 0)) for a in items
                if isinstance(a.get("value"), (int, float))
            ) / max(len(items), 1)
            savings = avg_benefit * order_count * 0.15
            methodology = (
                f"区域承运商优化预计降低 15% 异常 × {order_count} 单"
            )

        total += savings
        breakdown.append({
            "pattern_name": pat.get("pattern_name", "?"),
            "order_count": order_count,
            "potential_savings": round(savings, 2),
            "methodology": methodology,
        })

    return {
        "total_potential_savings": round(total, 2),
        "breakdown": breakdown,
        "confidence_note": (
            "ROI 为保守估算（30%/20%/15% 挽回率），实际效果取决于执行力度和数据质量。"
            "建议在实际运营 1-3 个月后基于真实挽回率修正模型。"
        ),
    }

