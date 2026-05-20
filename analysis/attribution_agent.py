# -*- coding: utf-8 -*-
"""
归因分析 Agent — 调用 DeepSeek API 对异常事件进行根因分析。

核心流程：
  detect_all() 检出异常 → AttributionAgent.analyze() → DeepSeek API → JSON 归因报告

工程特性：
  - 上下文自适应：根据异常指标类型，抽取最相关的对比数据
  - SOP 知识库匹配：关键词匹配供应链 SOP，嵌入 prompt
  - 容错重试：JSON 解析失败 / Schema 校验失败 → 最多重试 3 次
  - 防幻觉：prompt 明确禁止编造数据，要求每条 evidence 引用具体数字
"""

import json
import re
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from openai import OpenAI

from prompts.attribution_prompt import (
    SYSTEM_PROMPT,
    ATTRIBUTION_TEMPLATE,
    EXPECTED_SCHEMA,
    RETRY_PROMPT,
    get_anomaly_description,
)

logger = logging.getLogger(__name__)


# ============================================================
# 配置加载
# ============================================================


def _load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# SOP 知识库（轻量级关键词匹配）
# ============================================================


def _load_sops(sop_path: str = "knowledge/supply_chain_sop.md") -> str:
    """读取 SOP 全文。10 条 SOP 量很小，直接全量塞 prompt。"""
    try:
        with open(sop_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"SOP 文件 {sop_path} 未找到，跳过知识库")
        return ""


def _match_sops(metric: str, severity: str, all_sops: str, top_k: int = 3) -> str:
    """基于关键词匹配最相关的 SOP 条目。

    匹配规则：
      - "Benefit per order" / "daily_avg_profit" → SOP-002 (大额亏损), SOP-003 (利润率异常)
      - "shipping_delay_days" / "daily_late_rate" → SOP-001 (延迟飙升), SOP-008 (运输成本)
      - "Order Item Total" → SOP-002, SOP-005
      - "daily_order_count" → SOP-004 (订单骤降), SOP-005 (订单激增)
      - "Order Item Profit Ratio" → SOP-002, SOP-003
      - severity="high" → 优先匹配高严重度 SOP
    """
    keyword_map = {
        "Benefit per order": ["SOP-002", "SOP-003"],
        "shipping_delay_days": ["SOP-001", "SOP-008"],
        "Order Item Profit Ratio": ["SOP-002", "SOP-003", "SOP-009"],
        "Order Item Total": ["SOP-002", "SOP-005"],
        "daily_late_rate": ["SOP-001", "SOP-006", "SOP-007"],
        "daily_order_count": ["SOP-004", "SOP-005"],
        "daily_avg_profit": ["SOP-002", "SOP-003"],
        "Delivery Status": ["SOP-001", "SOP-009"],
    }

    target_sops = keyword_map.get(metric, ["SOP-001"])

    # 提取对应的 SOP 段落
    extracted: List[str] = []
    for sop_id in target_sops[:top_k]:
        # 在 SOP 全文中找到对应段落（以 "## SOP-XXX" 开头，到下一个 "## " 或文件尾）
        pattern = rf"(## {sop_id} .*?)(?=\n## SOP-|\n---|\Z)"
        match = re.search(pattern, all_sops, re.DOTALL)
        if match:
            extracted.append(match.group(1).strip())

    if not extracted:
        return "（无匹配的 SOP）"

    return "\n\n".join(extracted)


# ============================================================
# AttributionAgent 类
# ============================================================


class AttributionAgent:
    """供应链异常归因 Agent。

    Parameters
    ----------
    config_path : str
        YAML 配置文件路径。
    sop_path : str
        SOP 知识库路径。
    """

    def __init__(self, config_path: str = "config.yaml", sop_path: str = "knowledge/supply_chain_sop.md"):
        cfg = _load_config(config_path)

        # DeepSeek 客户端（兼容 OpenAI SDK）
        ds_cfg = cfg.get("llm", {}).get("deepseek", {})
        self.client = OpenAI(
            api_key=ds_cfg.get("api_key", "sk-placeholder"),
            base_url=ds_cfg.get("base_url", "https://api.deepseek.com"),
        )
        self.model = ds_cfg.get("model", "deepseek-chat")
        self.max_tokens = ds_cfg.get("max_tokens", 2048)
        self.temperature = ds_cfg.get("temperature", 0.3)
        self.max_retries = 3

        # SOP 知识库
        self.sop_full_text = _load_sops(sop_path)

        # 加载数据（用于构建对比上下文）
        self._df: Optional[pd.DataFrame] = None
        self._daily_metrics: Optional[Dict[str, pd.Series]] = None

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------

    def analyze(self, anomaly: Dict[str, Any], df: pd.DataFrame,
                lookback_days: int = 7) -> Dict[str, Any]:
        """对单个异常事件进行归因分析。

        先做数据充分性检查——数据不足时不浪费 API 调用，直接返回降级报告。

        Parameters
        ----------
        anomaly : dict
            来自 AnomalyDetector 的标准化异常记录（8 字段）。
        df : pd.DataFrame
            全量数据 DataFrame。
        lookback_days : int
            上下文回溯天数。

        Returns
        -------
        dict — 归因报告（经校验的 JSON），或降级报告（data_sufficient=false）。
        """
        self._df = df
        self._ensure_daily_metrics()

        # 0. 数据充分性检查 — 数据不够时不调 LLM
        sufficiency = self.data_sufficiency_check(anomaly, lookback_days)
        if not sufficiency["sufficient"]:
            logger.info(
                f"跳过 LLM 归因 [{anomaly.get('anomaly_id', '?')}]: "
                f"{'; '.join(sufficiency['reasons'])}"
            )
            return self._build_degraded_report(anomaly, sufficiency)

        # 1. 构建上下文
        context = self.generate_context(anomaly, lookback_days)

        # 2. 匹配 SOP
        sops = _match_sops(
            anomaly.get("metric", ""),
            anomaly.get("severity", "medium"),
            self.sop_full_text,
        )

        # 3. 构建 prompt
        prompt = self._build_prompt(anomaly, context, sops, lookback_days)

        # 4. 调用 LLM（带重试）
        raw_response, attempts = self.call_llm(prompt)

        # 5. 解析
        report = self.parse_response(raw_response)

        # 6. 校验 + 重试
        report = self._validate_and_retry(report, prompt, attempts)

        # 7. 附加元数据
        report["_meta"] = {
            "anomaly_id": anomaly.get("anomaly_id", ""),
            "model": self.model,
            "api_attempts": attempts,
            "data_sufficient": True,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        return report

    def analyze_batch(self, anomalies: pd.DataFrame, df: pd.DataFrame,
                      lookback_days: int = 7, max_samples: int = 5,
                      verbose: bool = True) -> List[Dict[str, Any]]:
        """批量归因分析（按 severity 优先级取 top-N）。

        Parameters
        ----------
        anomalies : pd.DataFrame
            detect_all() 返回的异常 DataFrame。
        df : pd.DataFrame
            全量数据。
        lookback_days : int
            上下文回溯天数。
        max_samples : int
            最多分析几条异常。
        verbose : bool
            是否打印进度。

        Returns
        -------
        List[Dict] — 归因报告列表。
        """
        # 严格按 severity 排序：high → medium → low，同级别按时间
        severity_order = {"high": 0, "medium": 1, "low": 2}
        anomalies = anomalies.copy()
        anomalies["_sev_rank"] = anomalies["severity"].map(severity_order).fillna(9)
        anomalies = anomalies.sort_values(["_sev_rank", "timestamp"])

        # 采样策略：severity 优先 > 订单去重 > metric 多样性
        sampled: List[Dict[str, Any]] = []
        seen_orders: set = set()

        for _, row in anomalies.iterrows():
            if len(sampled) >= max_samples:
                break

            ctx = row.get("context", {})
            order_id = ctx.get("Order Id") if isinstance(ctx, dict) else None

            # 同一订单不重复分析
            if order_id and order_id in seen_orders:
                continue

            if order_id:
                seen_orders.add(order_id)
            anomaly_dict = row.to_dict()
            if verbose:
                print(f"  分析: [{anomaly_dict['severity']}] {anomaly_dict['metric']} = {anomaly_dict['value']}  ...", end=" ")
            try:
                report = self.analyze(anomaly_dict, df, lookback_days)
                # 注入原始异常的业务上下文（Order Id, Product Name 等）
                orig_ctx = anomaly_dict.get("context", {})
                if isinstance(orig_ctx, dict) and orig_ctx:
                    report.setdefault("_meta", {})["anomaly_context"] = orig_ctx
                sampled.append(report)
                if verbose:
                    meta = report.get("_meta", {})
                    if meta.get("data_sufficient") is False:
                        print(f"跳过 (数据不足: {'; '.join(meta.get('skip_reasons', []))})")
                    else:
                        print(f"ok (置信度={report.get('confidence', '?')})")
            except Exception as e:
                if verbose:
                    print(f"失败: {e}")
                sampled.append({
                    "error": str(e),
                    "_meta": {"anomaly_id": anomaly_dict.get("anomaly_id", "")},
                })

            if len(sampled) >= max_samples:
                break

        # 汇总统计
        if verbose:
            skipped = sum(1 for r in sampled if r.get("_meta", {}).get("data_sufficient") is False)
            llm_calls = sum(1 for r in sampled if r.get("_meta", {}).get("data_sufficient") is True)
            errors = sum(1 for r in sampled if "error" in r)
            print(f"\n  汇总: {llm_calls} 次LLM调用, {skipped} 次跳过(数据不足), {errors} 次失败")

        return sampled

    # --------------------------------------------------------
    # 1. 上下文构建
    # --------------------------------------------------------

    def generate_context(self, anomaly: Dict[str, Any], lookback_days: int = 7) -> Dict[str, Any]:
        """为异常事件抽取最相关的业务上下文。

        返回的 dict 包含：
          - trend_table: 该指标最近 N 天的日度走势
          - order_context: 关联订单/产品/客户详情
          - comparison: 同维度对比数据
        """
        metric = anomaly.get("metric", "")
        ts_str = anomaly.get("timestamp", "")
        ctx = anomaly.get("context", {})

        # 解析时间
        try:
            anomaly_date = pd.Timestamp(ts_str).date()
        except Exception:
            anomaly_date = None

        # (A) 指标近期走势
        trend_table = self._build_trend_table(metric, anomaly_date, lookback_days)

        # (B) 订单上下文
        order_context = self._build_order_context(ctx)

        # (C) 同维度对比
        comparison = self._build_comparison(metric, ctx, anomaly_date, lookback_days)

        return {
            "trend_table": trend_table,
            "order_context": order_context,
            "comparison_text": comparison,
        }

    def _build_trend_table(self, metric: str, anomaly_date, lookback_days: int) -> str:
        """构建指标最近 N 天的日度走势表格。"""
        if self._daily_metrics is None or anomaly_date is None:
            return "（无日度趋势数据）"

        # 映射 metric 到 daily_metrics 的 key
        metric_to_daily = {
            "daily_late_rate": "late_rate",
            "daily_order_count": "order_count",
            "daily_avg_profit": "avg_profit",
            "Benefit per order": "avg_profit",
            "shipping_delay_days": "avg_delay",
        }
        daily_key = metric_to_daily.get(metric)
        if daily_key is None or daily_key not in self._daily_metrics:
            return f"（{metric} 无日度聚合数据）"

        series = self._daily_metrics[daily_key]
        start = anomaly_date - pd.Timedelta(days=lookback_days)
        window = series.loc[start:anomaly_date]

        if len(window) == 0:
            return f"（{start} ~ {anomaly_date} 无数据）"

        lines = ["| 日期 | 值 | 偏离均值 |", "|------|-----|---------|"]
        overall_mean = series.mean()
        for d, v in window.items():
            d_str = str(d.date()) if hasattr(d, "date") else str(d)
            dev = v - overall_mean
            dev_str = f"{dev:+.2f}"
            marker = " ← 异常日" if hasattr(d, "date") and d.date() == anomaly_date else ""
            lines.append(f"| {d_str} | {v:.4f} | {dev_str} |{marker}")

        return "\n".join(lines)

    def _build_order_context(self, ctx: Dict[str, Any]) -> str:
        """从异常记录的 context 中提取关联订单/产品/客户信息。"""
        fields = [
            ("Order Id", "订单ID"),
            ("Order Item Id", "订单项ID"),
            ("Product Name", "产品名称"),
            ("Category Name", "产品品类"),
            ("Market", "市场"),
            ("Customer Segment", "客户段位"),
            ("Delivery Status", "交付状态"),
            ("Shipping Mode", "运输方式"),
            ("Order Region", "地区"),
            ("Type", "支付方式"),
        ]
        lines = []
        for key, label in fields:
            val = ctx.get(key)
            if val is not None:
                lines.append(f"- {label}: {val}")
        return "\n".join(lines) if lines else "（无关联订单信息）"

    def _build_comparison(self, metric: str, ctx: Dict[str, Any],
                          anomaly_date, lookback_days: int) -> str:
        """构建同维度对比数据：同类产品 / 同市场 / 同客户段位的表现。"""
        if self._df is None or anomaly_date is None:
            return "（无对比数据）"

        df = self._df
        df["_date"] = pd.to_datetime(df["order date (DateOrders)"]).dt.date
        start = anomaly_date - pd.Timedelta(days=lookback_days)
        recent = df[(df["_date"] >= start) & (df["_date"] <= anomaly_date)]

        parts: List[str] = []

        # (a) 对比品类均值
        category = ctx.get("Category Name")
        if category and metric in ("Benefit per order", "shipping_delay_days",
                                     "Order Item Profit Ratio", "Order Item Total"):
            cat_data = recent[recent["Category Name"] == category]
            global_data = recent
            if len(cat_data) > 10:
                cat_mean = cat_data[metric].mean()
                global_mean = global_data[metric].mean()
                parts.append(
                    f"- 该品类「{category}」近 {lookback_days} 天 **{metric}** 均值: {cat_mean:.2f} "
                    f"（全局均值: {global_mean:.2f}，差距: {cat_mean - global_mean:+.2f}）"
                )

        # (b) 对比市场均值
        market = ctx.get("Market")
        if market and metric in ("shipping_delay_days", "Late_delivery_risk"):
            market_data = recent[recent["Market"] == market]
            if len(market_data) > 10:
                mkt_mean = market_data["Late_delivery_risk"].mean()
                global_late = recent["Late_delivery_risk"].mean()
                parts.append(
                    f"- 该市场「{market}」近 {lookback_days} 天延迟率: {mkt_mean:.2%} "
                    f"（全局: {global_late:.2%}）"
                )

        # (c) 对比运输方式均值
        shipping = ctx.get("Shipping Mode")
        if shipping and metric == "shipping_delay_days":
            ship_data = recent[recent["Shipping Mode"] == shipping]
            if len(ship_data) > 5:
                ship_mean = ship_data["shipping_delay_days"].mean()
                parts.append(
                    f"- 运输方式「{shipping}」近 {lookback_days} 天平均延迟: {ship_mean:.2f} 天"
                )

        # (d) 对比客户段位
        segment = ctx.get("Customer Segment")
        if segment and metric in ("Benefit per order", "Order Item Total"):
            seg_data = recent[recent["Customer Segment"] == segment]
            if len(seg_data) > 10:
                seg_mean = seg_data[metric].mean()
                parts.append(
                    f"- 客户段位「{segment}」近 {lookback_days} 天 **{metric}** 均值: {seg_mean:.2f}"
                )

        return "\n".join(parts) if parts else "（无可用对比维度）"

    # --------------------------------------------------------
    # 1.5 数据充分性检查
    # --------------------------------------------------------

    def data_sufficiency_check(self, anomaly: Dict[str, Any],
                                lookback_days: int = 7) -> Dict[str, Any]:
        """判断异常事件的上下文数据是否足够支撑有意义的 LLM 归因。

        数据不足时不浪费 API 调用，直接降级为"预警标记 + 人工排查"。
        这也天然覆盖了 daily 聚合级异常——它们缺少单条订单的上下文。

        检查维度：
          1. 是否为日聚合级指标（无单条订单上下文）
          2. 关键业务字段是否缺失
          3. 历史数据是否足够
          4. 对比数据是否匮乏

        Returns
        -------
        dict — {"sufficient": bool, "reasons": [str], "score": int}
        """
        reasons: List[str] = []
        score = 100  # 满分 100，逐项扣分

        metric = anomaly.get("metric", "")
        ctx = anomaly.get("context", {})

        # (A) 日聚合级指标 — 天然缺少单点上下文，扣重分
        DAILY_METRICS = {"daily_late_rate", "daily_order_count", "daily_avg_profit", "avg_delay"}
        if metric in DAILY_METRICS:
            reasons.append("日聚合级异常，无单条订单的详细信息（产品/品类/客户/运输方式）")
            score -= 50

        # (B) 关键业务字段缺失
        key_fields = {
            "Order Id": 15,
            "Product Name": 15,
            "Category Name": 10,
            "Market": 5,
            "Customer Segment": 5,
        }
        for field, penalty in key_fields.items():
            if field not in ctx or ctx[field] is None:
                reasons.append(f"缺少关键业务字段: {field}")
                score -= penalty

        # (C) 历史数据不足 — 日聚合指标数量不够
        if self._daily_metrics is not None:
            n_days = len(self._daily_metrics.get("late_rate", []))
            if n_days < 7:
                reasons.append(f"历史日聚合数据仅 {n_days} 天（需 ≥ 7 天）")
                score -= 20
        elif metric not in DAILY_METRICS:
            # 非日聚合指标但无日聚合数据，小扣分
            reasons.append("日聚合指标缓存未初始化")
            score -= 5

        # (D) 对比数据维度太少 — 几乎只有异常值本身
        has_trend = ctx.get("z_score") is not None or ctx.get("iqr_multiple") is not None
        has_dimension = any(ctx.get(f) for f in ["Category Name", "Market", "Shipping Mode"])
        if not has_trend and not has_dimension:
            reasons.append("上下文仅有异常值本身，无统计诊断信息也无业务维度信息")
            score -= 20

        # 判定
        sufficient = score >= 50 and len(reasons) <= 3

        return {
            "sufficient": sufficient,
            "reasons": reasons,
            "score": score,
        }

    def _build_degraded_report(self, anomaly: Dict[str, Any],
                                sufficiency: Dict[str, Any]) -> Dict[str, Any]:
        """构建降级报告——数据不足时跳过 LLM，返回人工排查指引。

        这不是"失败"，而是有意识的降级：与其让 LLM 在数据不足时硬编，
        不如诚实地标记为"预警"并给出排查清单。
        """
        metric = anomaly.get("metric", "")
        ctx = anomaly.get("context", {})

        # 根据指标类型生成排查清单
        checklist = []
        if "delay" in metric.lower() or "late" in metric.lower():
            checklist = [
                "检查仓库出库记录，确认是否存在拣货/打包瓶颈",
                "联系承运商获取运输轨迹，确认延误环节",
                "检查是否为节假日或天气导致的区域性延迟",
            ]
        elif "profit" in metric.lower() or "benefit" in metric.lower():
            checklist = [
                "复核订单的全部费用明细（运费、折扣、佣金）",
                "检查是否有折扣叠加或促销码误用",
                "确认该 SKU 的采购成本是否有变动",
            ]
        elif "ratio" in metric.lower():
            checklist = [
                "核查该订单的售价是否被错误上调",
                "确认成本数据是否完整（运费是否计入）",
                "对比同品类其他订单的利润率",
            ]
        elif "total" in metric.lower() or "amount" in metric.lower():
            checklist = [
                "确认该订单是否为批量采购或 B2B 订单",
                "检查是否有定价错误（如数量折扣未应用）",
                "核实订单金额的构成（单价×数量是否正确）",
            ]
        else:
            checklist = [
                "人工查看该异常发生时的业务日志",
                "与相关部门确认是否有已知的异常事件",
                "补充数据后重新运行归因分析",
            ]

        # 找到相关的 SOP
        sops = _match_sops(metric, anomaly.get("severity", "medium"), self.sop_full_text)
        sop_ids = []
        if sops:
            import re
            sop_ids = re.findall(r"SOP-\d+", sops)

        return {
            "root_cause_hypotheses": [
                {
                    "cause": "数据不足，无法自动推断根因。请按排查清单人工核查。",
                    "probability": 0.0,
                    "evidence": sufficiency["reasons"],
                    "against": [],
                }
            ],
            "recommended_actions": [
                {
                    "action": item,
                    "priority": "high" if i == 0 else "medium",
                    "expected_effect": "定位根因后可按对应 SOP 处置",
                    "owner": "运营经理",
                    "sop_ref": sop_ids[0] if sop_ids else None,
                }
                for i, item in enumerate(checklist[:3])
            ],
            "risk_level": anomaly.get("severity", "medium"),
            "risk_rationale": (
                f"异常指标 {metric} 出现偏离，但因数据不足（评分 {sufficiency['score']}/100）"
                f"无法自动归因。建议按排查清单人工跟进。"
            ),
            "confidence": 0.0,
            "confidence_note": f"数据充分性评分 {sufficiency['score']}/100，不满足 LLM 归因的最低要求（≥50）。",
            "summary": f"[预警] {metric} 异常，数据不足无法自动归因，建议人工排查。",
            "_meta": {
                "anomaly_id": anomaly.get("anomaly_id", ""),
                "model": "none (degraded — data insufficient)",
                "api_attempts": 0,
                "data_sufficient": False,
                "skip_reasons": sufficiency["reasons"],
                "sufficiency_score": sufficiency["score"],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }

    # --------------------------------------------------------
    # 2. LLM 调用
    # --------------------------------------------------------

    def _build_prompt(self, anomaly: Dict[str, Any], context: Dict[str, Any],
                      sops: str, lookback_days: int) -> str:
        """构建完整的归因 prompt。"""
        metric = anomaly.get("metric", "unknown")
        value = anomaly.get("value", 0)
        expected_range = anomaly.get("expected_range", [None, None])

        anomaly_desc = get_anomaly_description(metric, value, expected_range)

        return ATTRIBUTION_TEMPLATE.format(
            anomaly_id=anomaly.get("anomaly_id", ""),
            timestamp=anomaly.get("timestamp", ""),
            metric=metric,
            value=value,
            expected_range=expected_range,
            severity=anomaly.get("severity", "medium"),
            detection_method=anomaly.get("detection_method", ""),
            anomaly_description=anomaly_desc,
            lookback_days=lookback_days,
            trend_table=context.get("trend_table", "（无）"),
            order_context=context.get("order_context", "（无）"),
            comparison_text=context.get("comparison_text", "（无）"),
            sop_text=sops if sops else "（无匹配 SOP）",
        )

    def call_llm(self, user_prompt: str, is_retry: bool = False,
                 retry_error: str = "") -> Tuple[str, int]:
        """调用 DeepSeek API，带重试机制。

        Returns
        -------
        (response_text, attempt_count)
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if is_retry and retry_error:
            messages.append({"role": "user", "content": user_prompt})
            messages.append({"role": "assistant", "content": "[上次回复解析失败]"})
            messages.append({
                "role": "user",
                "content": RETRY_PROMPT.format(error_message=retry_error),
            })
        else:
            messages.append({"role": "user", "content": user_prompt})

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                temp = max(0.05, self.temperature - (attempt - 1) * 0.1)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=temp,
                )
                text = response.choices[0].message.content or ""
                return text, attempt
            except Exception as e:
                last_error = str(e)
                logger.warning(f"API call attempt {attempt}/{self.max_retries} failed: {e}")
                if attempt < self.max_retries:
                    time.sleep(2 ** (attempt - 1))  # 指数退避: 1s, 2s, 4s

        raise RuntimeError(f"API 调用在 {self.max_retries} 次重试后仍失败: {last_error}")

    # --------------------------------------------------------
    # 3. JSON 解析
    # --------------------------------------------------------

    @staticmethod
    def parse_response(raw: str) -> Dict[str, Any]:
        """解析 LLM 返回的 JSON。

        处理常见不规范情况：
          - JSON 被包裹在 ```json ... ``` 中
          - JSON 前后有额外文字
          - 单引号代替双引号（少见但存在）
        """
        # 尝试提取 ```json ... ``` 代码块
        code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if code_match:
            raw = code_match.group(1).strip()

        # 尝试提取 { ... } 最外层 JSON
        brace_match = re.search(r"\{[\s\S]*\}", raw)
        if brace_match:
            raw = brace_match.group(0)

        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 尝试修复常见问题后重试
        cleaned = raw.replace("'", '"')  # 单引号 → 双引号
        cleaned = re.sub(r",\s*}", "}", cleaned)  # 移除尾部逗号
        cleaned = re.sub(r",\s*]", "]", cleaned)
        cleaned = re.sub(r"None", "null", cleaned)
        cleaned = re.sub(r"True", "true", cleaned)
        cleaned = re.sub(r"False", "false", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"JSON 解析失败。原始回复片段: {raw[:300]}...\n错误: {e}"
            ) from e

    # --------------------------------------------------------
    # 4. Schema 校验
    # --------------------------------------------------------

    @staticmethod
    def validate(report: Dict[str, Any]) -> List[str]:
        """校验归因报告的字段完整性。返回错误列表，空列表表示通过。"""
        errors: List[str] = []

        # 检查顶层必填字段
        for field in EXPECTED_SCHEMA["required"]:
            if field not in report or report[field] is None:
                errors.append(f"缺少必填字段: {field}")

        # 检查 hypotheses
        hyps = report.get("root_cause_hypotheses", [])
        if not isinstance(hyps, list) or len(hyps) == 0:
            errors.append("root_cause_hypotheses 为空或不是数组")
        else:
            for i, h in enumerate(hyps):
                for req in ["cause", "probability", "evidence", "against"]:
                    if req not in h:
                        errors.append(f"root_cause_hypotheses[{i}] 缺少字段: {req}")
                if "probability" in h and not isinstance(h["probability"], (int, float)):
                    errors.append(f"root_cause_hypotheses[{i}].probability 不是数值")
                if "probability" in h and not (0 <= h["probability"] <= 1):
                    errors.append(f"root_cause_hypotheses[{i}].probability 超出 [0,1]")

        # 检查 actions
        actions = report.get("recommended_actions", [])
        if not isinstance(actions, list) or len(actions) == 0:
            errors.append("recommended_actions 为空或不是数组")
        else:
            for i, a in enumerate(actions):
                for req in ["action", "priority", "expected_effect", "owner", "sop_ref"]:
                    if req not in a:
                        errors.append(f"recommended_actions[{i}] 缺少字段: {req}")
                if "priority" in a and a["priority"] not in ("high", "medium", "low"):
                    errors.append(f"recommended_actions[{i}].priority 不合法: {a.get('priority')}")

        # 检查 risk_level
        if report.get("risk_level") not in ("high", "medium", "low"):
            errors.append(f"risk_level 不合法: {report.get('risk_level')}")

        # 检查 confidence
        conf = report.get("confidence")
        if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
            errors.append(f"confidence 不是 [0,1] 范围内的数值: {conf}")

        return errors

    def _validate_and_retry(self, report: Dict[str, Any], original_prompt: str,
                            attempts_used: int) -> Dict[str, Any]:
        """校验报告，不通过则重试。

        重试策略：首次失败用更低 temperature，第二次失败后改为更严格的 prompt。
        """
        errors = self.validate(report)
        if not errors:
            return report

        remaining = self.max_retries - attempts_used
        if remaining <= 0:
            logger.warning(f"已达最大重试次数，返回不完整报告。校验错误: {errors}")
            report["_validation_errors"] = errors
            return report

        error_msg = "；".join(errors)
        logger.info(f"Schema 校验失败 ({len(errors)} 个错误)，剩余 {remaining} 次重试...")

        try:
            retry_prompt = original_prompt + "\n\n" + RETRY_PROMPT.format(error_message=error_msg)
            raw, extra_attempts = self.call_llm(retry_prompt, is_retry=True, retry_error=error_msg)
            fixed_report = self.parse_response(raw)
            return self._validate_and_retry(fixed_report, original_prompt,
                                            attempts_used + extra_attempts)
        except Exception as e:
            logger.warning(f"重试也失败了: {e}")
            report["_validation_errors"] = errors
            return report

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    def _ensure_daily_metrics(self) -> None:
        """确保日聚合指标已计算。"""
        if self._daily_metrics is not None:
            return
        if self._df is None:
            self._daily_metrics = {}
            return

        df = self._df.copy()
        df["_dt"] = pd.to_datetime(df["order date (DateOrders)"])
        df["_day"] = df["_dt"].dt.date

        daily = df.groupby("_day").agg(
            late_rate=("Late_delivery_risk", "mean"),
            order_count=("Order Id", "nunique"),
            avg_profit=("Benefit per order", "mean"),
            avg_delay=("shipping_delay_days", "mean") if "shipping_delay_days" in df.columns else ("Days for shipping (real)", "mean"),
        )
        daily.index = pd.to_datetime(daily.index)
        daily = daily.sort_index()

        self._daily_metrics = {
            "late_rate": daily["late_rate"],
            "order_count": daily["order_count"],
            "avg_profit": daily["avg_profit"],
            "avg_delay": daily["avg_delay"],
        }
