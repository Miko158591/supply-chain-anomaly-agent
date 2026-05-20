# -*- coding: utf-8 -*-
"""
供应链异常检测器 — 基于统计学方法的三合一检测引擎。

支持四种检测策略：
  - Z-Score: 偏离均值超过 N 个标准差
  - 移动平均偏离: 偏离近期滚动均值超过 N 倍滚动标准差
  - IQR: 超出四分位距 k 倍范围
  - 业务规则: 基于领域知识的硬阈值规则

所有阈值从 config.yaml 读取，不硬编码。
"""

import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import yaml

# ============================================================
# 配置加载
# ============================================================


def _load_config(config_path: str = "config.yaml") -> dict:
    """加载 YAML 配置文件，返回 anomaly 相关参数。"""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("anomaly", {})


# ============================================================
# 辅助函数
# ============================================================


def _make_anomaly(
    timestamp: Any,
    metric: str,
    value: float,
    lower: Optional[float],
    upper: Optional[float],
    method: str,
    severity: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建标准化的异常记录字典。

    lower/upper 可以为 None（表示单侧开放区间，如业务规则 "delay > 3"
    只有下界，没有上界）。None 在 expected_range 中保持为 null。
    """
    lo = round(float(lower), 4) if lower is not None else None
    hi = round(float(upper), 4) if upper is not None else None
    return {
        "anomaly_id": f"{method}_{uuid.uuid4().hex[:8]}",
        "timestamp": str(timestamp),
        "metric": metric,
        "value": round(float(value), 4),
        "expected_range": [lo, hi],
        "severity": severity,
        "detection_method": method,
        "context": context or {},
    }


def _severity_from_z(z_score: float, threshold: float) -> str:
    """根据 z-score 偏离程度判定严重等级。"""
    abs_z = abs(z_score)
    if abs_z > threshold * 1.5:
        return "high"
    elif abs_z > threshold * 1.2:
        return "medium"
    return "low"


def _severity_from_iqr(value: float, q1: float, q3: float, iqr: float, k: float) -> str:
    """根据 IQR 偏离程度判定严重等级。"""
    if value < q1 - k * 2 * iqr or value > q3 + k * 2 * iqr:
        return "high"
    elif value < q1 - k * 1.5 * iqr or value > q3 + k * 1.5 * iqr:
        return "medium"
    return "low"


# ============================================================
# AnomalyDetector 类
# ============================================================


class AnomalyDetector:
    """供应链异常检测器。

    Parameters
    ----------
    config_path : str
        YAML 配置文件路径（默认 "config.yaml"）。
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.config = _load_config(config_path)
        self._results_cache: List[Dict[str, Any]] = []

    # --------------------------------------------------------
    # 1. Z-Score 检测
    # --------------------------------------------------------

    def detect_zscore(
        self,
        series: pd.Series,
        threshold: Optional[float] = None,
        metric_name: str = "value",
    ) -> List[Dict[str, Any]]:
        """Z-Score 异常检测。

        对序列中的每个值计算 z = (x - μ) / σ，|z| > threshold 视为异常。

        Parameters
        ----------
        series : pd.Series
            数值型时间序列（index 为时间戳）。
        threshold : float, optional
            Z-Score 阈值，默认从 config 读取。
        metric_name : str
            指标名称，用于返回结果中的 ``metric`` 字段。

        Returns
        -------
        List[Dict] — 标准化异常记录列表。
        """
        if threshold is None:
            threshold = self.config.get("zscore", {}).get("threshold", 3.0)

        clean = series.dropna()
        if len(clean) < 3:
            return []

        mean = clean.mean()
        std = clean.std(ddof=1)  # 样本标准差
        if std == 0 or np.isnan(std):
            return []

        z_scores = (clean - mean) / std
        anomaly_mask = z_scores.abs() > threshold
        anomaly_positions = np.where(anomaly_mask.values)[0]  # 整数位置，避免重复索引问题

        results: List[Dict[str, Any]] = []
        for pos in anomaly_positions:
            z = float(z_scores.iloc[pos])
            val = float(clean.iloc[pos])
            ts = str(clean.index[pos])
            deviation_pct = abs((val - mean) / mean * 100) if mean != 0 else float("inf")
            direction = "偏高" if val > mean else "偏低"
            results.append(
                _make_anomaly(
                    timestamp=ts,
                    metric=metric_name,
                    value=val,
                    lower=mean - threshold * std,
                    upper=mean + threshold * std,
                    method="zscore",
                    severity=_severity_from_z(z, threshold),
                    context={
                        "z_score": round(z, 4),
                        "mean": round(float(mean), 2),
                        "std": round(float(std), 2),
                        "deviation_pct": round(deviation_pct, 1),
                        "direction": direction,
                        "description": f"偏离均值 {deviation_pct:.1f}%（{direction}），"
                                       f"z={z:.2f}（阈值 {threshold}）",
                    },
                )
            )
        return results

    # --------------------------------------------------------
    # 2. 移动平均偏离检测
    # --------------------------------------------------------

    def detect_moving_average(
        self,
        series: pd.Series,
        window: Optional[int] = None,
        n_std: Optional[float] = None,
        metric_name: str = "value",
    ) -> List[Dict[str, Any]]:
        """移动平均偏离检测。

        对时间序列计算滚动均值和滚动标准差，当前值偏离滚动均值
        超过 ``n_std`` 倍滚动标准差时判定为异常。

        适合检测相对于近期趋势的突然变化，对具有缓慢漂移的指标更鲁棒。

        Parameters
        ----------
        series : pd.Series
            数值型时间序列，index 须为有序时间戳。
        window : int, optional
            滚动窗口大小（天数），默认从 config 读取。
        n_std : float, optional
            偏离倍数阈值，默认从 config 读取。
        metric_name : str
            指标名称。

        Returns
        -------
        List[Dict] — 标准化异常记录列表。
        """
        if window is None:
            window = self.config.get("moving_avg", {}).get("window", 7)
        if n_std is None:
            n_std = self.config.get("moving_avg", {}).get("deviation_factor", 2.0)

        clean = series.dropna().sort_index()
        if len(clean) < window + 2:
            return []

        rolling_mean = clean.rolling(window=window, min_periods=window).mean()
        rolling_std = clean.rolling(window=window, min_periods=window).std(ddof=1)

        # 避免除零：std=0 的窗口（常数段）用全局 std 的 1% 兜底
        global_std = clean.std(ddof=1)
        min_std = max(global_std * 0.01, 1e-6) if global_std > 0 else 1e-6
        rolling_std = rolling_std.mask(rolling_std == 0, min_std)

        deviation = (clean - rolling_mean).abs()
        threshold_line = n_std * rolling_std

        anomaly_mask = deviation > threshold_line
        anomaly_indices = clean.index[anomaly_mask]

        results: List[Dict[str, Any]] = []
        for idx in anomaly_indices:
            r_mean = float(rolling_mean.loc[idx])
            r_std = float(rolling_std.loc[idx]) if not np.isnan(rolling_std.loc[idx]) else min_std
            dev = float(deviation.loc[idx])
            band = n_std * r_std
            results.append(
                _make_anomaly(
                    timestamp=idx,
                    metric=metric_name,
                    value=float(clean.loc[idx]),
                    lower=r_mean - band,
                    upper=r_mean + band,
                    method="moving_avg",
                    severity=("high" if dev > 2 * band else ("medium" if dev > 1.5 * band else "low")),
                    context={
                        "rolling_mean": round(r_mean, 2),
                        "rolling_std": round(r_std, 2),
                        "deviation": round(dev, 2),
                    },
                )
            )
        return results

    # --------------------------------------------------------
    # 3. IQR 检测
    # --------------------------------------------------------

    def detect_iqr(
        self,
        series: pd.Series,
        k: Optional[float] = None,
        metric_name: str = "value",
    ) -> List[Dict[str, Any]]:
        """IQR（四分位距）异常检测。

        计算 Q1（25% 分位）和 Q3（75% 分位），超出
        [Q1 - k*IQR, Q3 + k*IQR] 范围的值视为异常。

        对偏态分布（如利润、金额）比 Z-Score 更鲁棒。

        Parameters
        ----------
        series : pd.Series
            数值型序列。
        k : float, optional
            IQR 乘数（默认 1.5，严格用 3.0），默认从 config 读取。
        metric_name : str
            指标名称。

        Returns
        -------
        List[Dict] — 标准化异常记录列表。
        """
        if k is None:
            k = self.config.get("iqr", {}).get("multiplier", 1.5)

        clean = series.dropna()
        if len(clean) < 4:
            return []

        q1 = clean.quantile(0.25)
        q3 = clean.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            return []

        lower = q1 - k * iqr
        upper = q3 + k * iqr

        anomaly_mask = (clean < lower) | (clean > upper)
        anomaly_positions = np.where(anomaly_mask.values)[0]

        results: List[Dict[str, Any]] = []
        for pos in anomaly_positions:
            val = float(clean.iloc[pos])
            ts = str(clean.index[pos])
            # 计算相对于 IQR 的偏离倍数
            median = clean.quantile(0.5)
            if val < q1:
                iqr_multiple = abs(val - q1) / iqr if iqr > 0 else 0
                direction = "偏低"
            else:
                iqr_multiple = abs(val - q3) / iqr if iqr > 0 else 0
                direction = "偏高"
            results.append(
                _make_anomaly(
                    timestamp=ts,
                    metric=metric_name,
                    value=val,
                    lower=lower,
                    upper=upper,
                    method="iqr",
                    severity=_severity_from_iqr(val, q1, q3, iqr, k),
                    context={
                        "q1": round(float(q1), 2),
                        "q3": round(float(q3), 2),
                        "iqr": round(float(iqr), 2),
                        "median": round(float(median), 2),
                        "iqr_multiple": round(iqr_multiple, 2),
                        "direction": direction,
                        "description": f"超出{'上' if val > q3 else '下'}界 {iqr_multiple:.1f} 倍 IQR"
                                       f"（{'高于' if val > q3 else '低于'}{'Q3' if val > q3 else 'Q1'}）",
                    },
                )
            )
        return results

    # --------------------------------------------------------
    # 4. 业务规则检测
    # --------------------------------------------------------

    def detect_business_rule(
        self,
        df: pd.DataFrame,
        rules: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """基于业务规则的异常检测。

        对 DataFrame 的每一行按规则逐条检查，命中任一条即标记为异常。
        规则以字典形式传入，格式为::

            {
                "规则名": {
                    "condition": "列名 运算符 阈值",  # 如 "Benefit per order < -200"
                    "metric": "指标名",
                    "severity": "high|medium|low",
                    "message": "业务解释"
                },
                ...
            }

        也支持内置的默认规则集（延迟、亏损、高利润率）。

        Parameters
        ----------
        df : pd.DataFrame
            待检测的数据表。
        rules : dict, optional
            自定义规则字典，为 None 时使用内置默认规则。

        Returns
        -------
        List[Dict] — 标准化异常记录列表。
        """
        if rules is None:
            rules = self._default_rules()

        results: List[Dict[str, Any]] = []
        timestamp_col = _find_time_column(df)

        for rule_name, rule_def in rules.items():
            condition = rule_def["condition"]
            metric = rule_def.get("metric", rule_name)
            severity = rule_def.get("severity", "medium")
            context_template = rule_def.get("context", {})

            try:
                col, op, threshold_str = _parse_condition(condition)
                if col not in df.columns:
                    continue

                # 数值型阈值 vs 字符串型阈值（如 "Shipping canceled"）
                is_numeric = True
                try:
                    threshold_val = float(threshold_str)
                except ValueError:
                    threshold_val = threshold_str  # type: ignore[assignment]
                    is_numeric = False

                matching = _apply_condition(df[col], op, threshold_val)
                matched_rows = df.loc[matching]

                for idx in matched_rows.index:
                    row = matched_rows.loc[idx]
                    ts = row[timestamp_col] if timestamp_col else str(idx)
                    raw_value = row[col]
                    display_value = float(raw_value) if is_numeric else str(raw_value)
                    ctx = dict(context_template)
                    ctx.update({c: row[c] for c in ["Order Id", "Category Name", "Product Name",
                                                       "Delivery Status", "Market", "Customer Segment"]
                                if c in row.index})

                    # 单侧开放区间：">" 只有下界，"<" 只有上界，"==" 两侧相等
                    if op in (">", ">="):
                        lo, hi = threshold_val if is_numeric else threshold_val, None
                    elif op in ("<", "<="):
                        lo, hi = None, threshold_val if is_numeric else threshold_val
                    else:
                        lo, hi = threshold_val if is_numeric else threshold_val, threshold_val if is_numeric else threshold_val

                    results.append(
                        _make_anomaly(
                            timestamp=ts,
                            metric=metric,
                            value=display_value,
                            lower=lo,
                            upper=hi,
                            method="business_rule",
                            severity=severity,
                            context=ctx,
                        )
                    )
            except Exception:
                # 单条规则失败不影响其他规则
                continue

        return results

    def _default_rules(self) -> Dict[str, Dict[str, Any]]:
        """内置业务规则集 — 阈值从 config.yaml 读取，硬编码仅作兜底。

        规则覆盖供应链中常见的硬异常场景：
        - 极端亏损、超长延迟、异常高利润率、大额订单、取消订单、深度负利润。
        """
        br = self.config.get("business_rules", {})

        loss_threshold = br.get("extreme_loss_threshold", -200)
        delay_days = br.get("extreme_delay_days", 3)
        high_ratio = br.get("high_profit_ratio", 0.45)
        high_value = br.get("high_value_threshold", 500)

        return {
            "extreme_profit_loss": {
                "condition": f"Benefit per order < {loss_threshold}",
                "metric": "Benefit per order",
                "severity": "high",
                "message": f"单笔订单亏损超过 ${abs(loss_threshold)}",
                "context": {},
            },
            "extreme_delay": {
                "condition": f"shipping_delay_days > {delay_days}",
                "metric": "shipping_delay_days",
                "severity": "high",
                "message": f"延迟超过 {delay_days} 天",
                "context": {},
            },
            "high_profit_ratio": {
                "condition": f"Order Item Profit Ratio > {high_ratio}",
                "metric": "Order Item Profit Ratio",
                "severity": "medium",
                "message": f"利润率异常偏高（>{high_ratio*100:.0f}%），可能是定价错误或数据问题",
                "context": {},
            },
            "high_value_order": {
                "condition": f"Order Item Total > {high_value}",
                "metric": "Order Item Total",
                "severity": "low",
                "message": f"单笔金额偏高（>${high_value}）",
                "context": {},
            },
            "canceled_order": {
                "condition": "Delivery Status == Shipping canceled",
                "metric": "Delivery Status",
                "severity": "medium",
                "message": "订单被取消",
                "context": {},
            },
            "deep_negative_margin": {
                "condition": "Order Item Profit Ratio < -1.0",
                "metric": "Order Item Profit Ratio",
                "severity": "high",
                "message": "利润率 <-100%，严重亏损",
                "context": {},
            },
        }

    # --------------------------------------------------------
    # 5. 综合检测
    # --------------------------------------------------------

    def detect_all(
        self,
        df: pd.DataFrame,
        daily_metrics: Optional[Dict[str, pd.Series]] = None,
    ) -> pd.DataFrame:
        """综合异常检测 — 调用全部四种方法，合并去重后返回。

        Parameters
        ----------
        df : pd.DataFrame
            原始数据（含所有记录列）。
        daily_metrics : dict, optional
            按天聚合的指标 Series，key 为指标名，value 为以日期为 index 的 Series。
            如 ``{"late_rate": daily_late_series, "order_count": daily_count_series}``。
            不传则自动从 df 聚合。

        Returns
        -------
        pd.DataFrame — 所有异常记录的合并结果，按 timestamp 排序。
        """
        all_results: List[Dict[str, Any]] = []

        # (A) 统计方法 — 对按天聚合的指标做时间序列检测
        if daily_metrics is None:
            daily_metrics = _build_daily_metrics(df)

        for metric, series in daily_metrics.items():
            # Z-Score
            z_results = self.detect_zscore(series, metric_name=metric)
            all_results.extend(z_results)

            # 移动平均偏离
            ma_results = self.detect_moving_average(series, metric_name=metric)
            all_results.extend(ma_results)

            # IQR
            iqr_results = self.detect_iqr(series, metric_name=metric)
            all_results.extend(iqr_results)

        # (B) 统计方法 — 对全量记录的分布做 IQR + Z-Score
        # 用整数 index 避免重复时间戳导致的 .loc 问题
        # 注意: shipping_delay_days 是离散分布（仅 -2~4），IQR/Z-Score 不适用，由业务规则覆盖
        record_metrics = {
            "Benefit per order": df["Benefit per order"].reset_index(drop=True),
            "Order Item Profit Ratio": df["Order Item Profit Ratio"].reset_index(drop=True),
            "Order Item Total": df["Order Item Total"].reset_index(drop=True),
        }
        # 业务上下文字段（注入到统计检测结果中，不只输出数字）
        BUSINESS_CONTEXT_COLS = [
            "Order Id", "Order Item Id", "Category Name", "Product Name",
            "Delivery Status", "Market", "Customer Segment", "Shipping Mode",
            "Order Region", "Type",
        ]

        for metric, series in record_metrics.items():
            iqr_records = self.detect_iqr(series, metric_name=metric)
            for r in iqr_records:
                try:
                    pos = int(r["timestamp"])
                    r["timestamp"] = str(df.iloc[pos]["order date (DateOrders)"])
                    # 注入业务上下文：从 df 的对应行读取关键字段
                    row = df.iloc[pos]
                    r["context"].update(
                        {c: row[c] for c in BUSINESS_CONTEXT_COLS if c in row.index}
                    )
                except (ValueError, KeyError, IndexError):
                    pass
            all_results.extend(iqr_records)

            z_records = self.detect_zscore(series, metric_name=metric)
            for r in z_records:
                try:
                    pos = int(r["timestamp"])
                    r["timestamp"] = str(df.iloc[pos]["order date (DateOrders)"])
                    row = df.iloc[pos]
                    r["context"].update(
                        {c: row[c] for c in BUSINESS_CONTEXT_COLS if c in row.index}
                    )
                except (ValueError, KeyError, IndexError):
                    pass
            all_results.extend(z_records)

        # (C) 业务规则
        rule_results = self.detect_business_rule(df)
        all_results.extend(rule_results)

        # 去重：同一 timestamp + metric 的异常只保留 severity 最高的
        result_df = pd.DataFrame(all_results)
        if result_df.empty:
            return result_df

        severity_order = {"high": 3, "medium": 2, "low": 1}
        result_df["sev_rank"] = result_df["severity"].map(severity_order).fillna(0)
        result_df = result_df.sort_values("sev_rank", ascending=False)
        result_df = result_df.drop_duplicates(subset=["timestamp", "metric", "value"], keep="first")
        result_df = result_df.drop(columns=["sev_rank"])
        result_df = result_df.sort_values("timestamp").reset_index(drop=True)

        self._results_cache = result_df.to_dict("records")
        return result_df


# ============================================================
# 内部辅助函数
# ============================================================


def _build_daily_metrics(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """从原始 DataFrame 构建每日聚合指标。"""
    df = df.copy()
    df["order_date_dt"] = pd.to_datetime(df["order date (DateOrders)"])
    df["date"] = df["order_date_dt"].dt.date

    daily = df.groupby("date").agg(
        late_rate=("Late_delivery_risk", "mean"),
        order_count=("Order Id", "nunique"),
        avg_profit=("Benefit per order", "mean"),
        avg_delay=("shipping_delay_days", "mean"),
    )

    daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()

    return {
        "late_rate": daily["late_rate"],
        "order_count": daily["order_count"],
        "avg_profit": daily["avg_profit"],
        "avg_delay": daily["avg_delay"],
    }


def _find_time_column(df: pd.DataFrame) -> Optional[str]:
    """在 DataFrame 中查找可用的时间列。"""
    for col in ["order date (DateOrders)", "order_date", "date", "timestamp"]:
        if col in df.columns:
            return col
    return None


def _parse_condition(condition: str) -> Tuple[str, str, str]:
    """解析条件字符串为 (列名, 运算符, 阈值)。

    支持格式: ``"列名 运算符 阈值"`` 或 ``"列名 运算符 阈值字符串"``。
    示例: ``"Benefit per order < -200"`` → ``("Benefit per order", "<", "-200")``
    """
    # 按 ==, >=, <=, !=, >, < 依次尝试（长的优先，避免 >= 被拆成 >）
    for op in ["==", ">=", "<=", "!=", ">", "<"]:
        if f" {op} " in condition:
            col, rest = condition.split(f" {op} ", 1)
            return col.strip(), op, rest.strip()
        # 也支持不带前后空格的情况
        if op in condition:
            parts = condition.split(op, 1)
            if len(parts) == 2:
                return parts[0].strip(), op, parts[1].strip()
    raise ValueError(f"无法解析条件: {condition}")


def _apply_condition(series: pd.Series, op: str, threshold: float) -> pd.Series:
    """对 Series 应用比较运算符，返回布尔 mask。"""
    if op == ">":
        return series > threshold
    elif op == ">=":
        return series >= threshold
    elif op == "<":
        return series < threshold
    elif op == "<=":
        return series <= threshold
    elif op == "==":
        # 对于字符串相等比较，尝试 numeric 再 fallback
        try:
            return series.astype(str).str.strip() == str(threshold).strip()
        except Exception:
            return series == threshold
    elif op == "!=":
        try:
            return series.astype(str).str.strip() != str(threshold).strip()
        except Exception:
            return series != threshold
    raise ValueError(f"不支持的运算符: {op}")
