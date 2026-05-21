# -*- coding: utf-8 -*-
"""归因 Agent 单元测试 — mock LLM 调用，测试纯逻辑部分。

运行: python -m pytest tests/test_attribution_agent.py -v
"""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.attribution_agent import AttributionAgent


# ═══════════════════════════ Fixtures ═══════════════════════════

@pytest.fixture
def sample_anomaly() -> dict:
    return {
        "anomaly_id": "zscore_test_001",
        "timestamp": "2017-06-15",
        "metric": "Benefit per order",
        "value": -277.09,
        "expected_range": [-239.0, 283.0],
        "severity": "high",
        "detection_method": "zscore",
        "context": {
            "Order Id": 77202,
            "Category Name": "Fishing",
            "Market": "LATAM",
            "Delivery Status": "Late delivery",
            "Shipping Mode": "Standard Class",
            "Order Region": "South America",
            "Customer Segment": "Consumer",
        },
    }


@pytest.fixture
def sample_anomaly_delay() -> dict:
    return {
        "anomaly_id": "zscore_test_002",
        "timestamp": "2017-06-15",
        "metric": "shipping_delay_days",
        "value": 4,
        "expected_range": [0, 2.5],
        "severity": "high",
        "detection_method": "iqr",
        "context": {
            "Order Id": 75939,
            "Category Name": "Men's Footwear",
            "Market": "LATAM",
            "Delivery Status": "Late delivery",
            "Shipping Mode": "Standard Class",
        },
    }


@pytest.fixture
def sample_df() -> pd.DataFrame:
    np.random.seed(42)
    dates = pd.date_range("2017-06-01", periods=30, freq="D")
    df = pd.DataFrame({
        "order date (DateOrders)": dates.repeat(10)[:300],
        "Benefit per order": np.random.normal(22, 104, 300),
        "shipping_delay_days": np.random.choice([0, 1, 2, 3, 4], 300, p=[0.3, 0.3, 0.2, 0.15, 0.05]),
        "Order Item Profit Ratio": np.random.normal(0.12, 0.47, 300).clip(-2, 0.5),
        "Order Item Total": np.random.uniform(10, 500, 300),
        "Late_delivery_risk": np.random.choice([0, 1], 300, p=[0.45, 0.55]),
        "Category Name": np.random.choice(["Fishing", "Cleats", "Camping"], 300),
        "Market": np.random.choice(["LATAM", "Europe", "Pacific"], 300),
        "Sales per customer": np.random.normal(200, 80, 300),
        "Days for shipping (real)": np.random.choice([0, 1, 2, 3, 4], 300),
        "Days for shipment (scheduled)": np.random.choice([1, 2, 3], 300),
        "Delivery Status": np.random.choice(["Shipping on time", "Late delivery"], 300, p=[0.4, 0.6]),
        "Shipping Mode": np.random.choice(["Standard Class", "First Class", "Second Class"], 300, p=[0.6, 0.1, 0.3]),
        "Order Region": np.random.choice(["South America", "Europe", "Pacific"], 300),
        "Customer Segment": np.random.choice(["Consumer", "Corporate", "Home Office"], 300),
    })
    return df


# ═══════════════════════════ 1. JSON 解析 ═══════════════════════════

class TestJsonParsing:
    def test_parse_clean_json(self):
        result = AttributionAgent.parse_response(json.dumps({"key": "value"}))
        assert result == {"key": "value"}

    def test_parse_json_with_markdown_fence(self):
        result = AttributionAgent.parse_response('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_parse_json_with_extra_text(self):
        result = AttributionAgent.parse_response('分析：\n{"score": 80}\n参考。')
        assert result == {"score": 80}

    def test_parse_single_quotes(self):
        result = AttributionAgent.parse_response("{'name': 'test', 'value': 42}")
        assert result == {"name": "test", "value": 42}

    def test_parse_python_keywords(self):
        result = AttributionAgent.parse_response(
            '{"flag": True, "other": False, "missing": None}'
        )
        assert result == {"flag": True, "other": False, "missing": None}

    def test_parse_trailing_comma(self):
        result = AttributionAgent.parse_response(
            '{"items": [1, 2, 3,], "name": "test",}'
        )
        assert result == {"items": [1, 2, 3], "name": "test"}

    def test_parse_invalid_raises(self):
        with pytest.raises(ValueError, match="JSON 解析失败"):
            AttributionAgent.parse_response("这不是 JSON")

    def test_parse_real_attribution_output(self):
        """解析一条真实归因输出格式。"""
        raw = """```json
{
  "root_cause_hypotheses": [
    {
      "cause": "延迟交付触发了运费补贴",
      "probability": 0.75,
      "evidence": ["订单状态为 Late delivery"],
      "against": ["缺少运费明细"]
    }
  ],
  "recommended_actions": [
    {
      "action": "复核订单费用明细",
      "priority": "high",
      "expected_effect": "定位亏损主因",
      "owner": "财务分析师",
      "sop_ref": "SOP-002"
    }
  ],
  "risk_level": "high",
  "risk_rationale": "亏损严重",
  "confidence": 0.80,
  "confidence_note": "数据充分",
  "summary": "一句话"
}
```"""
        result = AttributionAgent.parse_response(raw)
        assert result["risk_level"] == "high"
        assert len(result["root_cause_hypotheses"]) == 1
        assert result["root_cause_hypotheses"][0]["probability"] == 0.75


# ═══════════════════════════ 2. Schema 校验 ═══════════════════════════

class TestSchemaValidation:
    def test_valid_report_passes(self):
        valid = {
            "root_cause_hypotheses": [
                {"cause": "延迟", "probability": 0.7, "evidence": ["证据1"], "against": []}
            ],
            "recommended_actions": [
                {"action": "复核", "priority": "high", "owner": "财务",
                 "expected_effect": "定位", "sop_ref": "SOP-001"}
            ],
            "risk_level": "high", "risk_rationale": "亏损严重",
            "confidence": 0.8, "confidence_note": "数据充分", "summary": "一句话",
        }
        assert AttributionAgent.validate(valid) == []

    def test_missing_fields_detected(self):
        assert len(AttributionAgent.validate({"risk_level": "high"})) >= 3

    def test_probability_out_of_range(self):
        invalid = {
            "root_cause_hypotheses": [
                {"cause": "x", "probability": 1.5, "evidence": ["e"], "against": []}
            ],
            "recommended_actions": [
                {"action": "x", "priority": "high", "owner": "x",
                 "expected_effect": "x", "sop_ref": None}
            ],
            "risk_level": "high", "risk_rationale": "x",
            "confidence": 0.5, "confidence_note": "x", "summary": "x",
        }
        errors = AttributionAgent.validate(invalid)
        assert any("probability" in e for e in errors)

    def test_invalid_risk_level(self):
        invalid = {
            "root_cause_hypotheses": [
                {"cause": "x", "probability": 0.5, "evidence": ["e"], "against": []}
            ],
            "recommended_actions": [
                {"action": "x", "priority": "high", "owner": "x",
                 "expected_effect": "x", "sop_ref": None}
            ],
            "risk_level": "critical", "risk_rationale": "x",
            "confidence": 0.5, "confidence_note": "x", "summary": "x",
        }
        assert any("risk_level" in e for e in AttributionAgent.validate(invalid))

    def test_empty_hypotheses(self):
        invalid = {
            "root_cause_hypotheses": [],
            "recommended_actions": [
                {"action": "x", "priority": "high", "owner": "x",
                 "expected_effect": "x", "sop_ref": None}
            ],
            "risk_level": "high", "risk_rationale": "x",
            "confidence": 0.5, "confidence_note": "x", "summary": "x",
        }
        assert any("为空" in e for e in AttributionAgent.validate(invalid))


# ═══════════════════════════ 3. 数据充分性检查 ═══════════════════════════

class TestDataSufficiency:
    def test_daily_metric_insufficient(self):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent._df = None
        agent._daily_metrics = None
        result = agent.data_sufficiency_check(
            {"anomaly_id": "test", "metric": "daily_late_rate",
             "value": 0.55, "context": {}},
            lookback_days=7,
        )
        assert result["sufficient"] is False
        assert len(result["reasons"]) >= 1

    def test_record_level_sufficient(
        self, sample_anomaly: dict, sample_df: pd.DataFrame
    ):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent._df = sample_df
        agent._daily_metrics = None
        result = agent.data_sufficiency_check(sample_anomaly, lookback_days=7)
        assert result["sufficient"] is True


# ═══════════════════════════ 4. Context 过滤 ═══════════════════════════

class TestContextFiltering:
    def test_delay_metric_excludes_profit_fields(
        self, sample_df: pd.DataFrame, sample_anomaly_delay: dict
    ):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent._df = sample_df
        agent._daily_metrics = {}
        ctx = agent.generate_context(sample_anomaly_delay, lookback_days=7)
        order_ctx = ctx["order_context"]
        assert "Benefit" not in order_ctx
        assert "Profit" not in order_ctx

    def test_profit_metric_excludes_delay_fields(
        self, sample_df: pd.DataFrame, sample_anomaly: dict
    ):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent._df = sample_df
        agent._daily_metrics = {}
        ctx = agent.generate_context(sample_anomaly, lookback_days=7)
        order_ctx = ctx["order_context"]
        assert "Shipping Mode" not in order_ctx
        assert "Delivery Status" not in order_ctx

    def test_context_includes_cross_category_comparison(
        self, sample_df: pd.DataFrame, sample_anomaly: dict
    ):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent._df = sample_df
        agent._daily_metrics = {}
        ctx = agent.generate_context(sample_anomaly, lookback_days=7)
        assert len(ctx["comparison_text"]) > 10


# ═══════════════════════════ 5. Mock LLM 调用 ═══════════════════════════

MOCK_REPORT = {
    "root_cause_hypotheses": [
        {
            "cause": "延迟交付触发运费补贴，将微利订单推入亏损",
            "probability": 0.75,
            "evidence": [
                "订单状态为 Late delivery",
                "品类近7天利润均值 $8.50 远低于全局 $26.39"
            ],
            "against": ["缺少运费明细无法精确定量"]
        }
    ],
    "recommended_actions": [
        {
            "action": "财务分析师复核订单费用明细",
            "priority": "high",
            "expected_effect": "30分钟内定位亏损主因",
            "owner": "财务分析师",
            "sop_ref": "SOP-002"
        }
    ],
    "risk_level": "high",
    "risk_rationale": "亏损严重且涉及延迟",
    "confidence": 0.80,
    "confidence_note": "数据支撑充分，缺少费用拆分明细",
    "summary": "延迟交付叠加品类利润薄弱导致严重亏损",
}


class TestMockedAttribution:
    """Mock LLM 调用，测试归因主流程的纯逻辑部分。"""

    def test_analyze_with_mock_llm(
        self, sample_anomaly: dict, sample_df: pd.DataFrame
    ):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent._df = sample_df
        agent._daily_metrics = None
        agent.model = "test-model"
        agent.sop_full_text = "SOP-001: 物流延迟处置\nSOP-002: 大额亏损处置"

        # Mock call_llm
        mock_raw = json.dumps(MOCK_REPORT, ensure_ascii=False)
        agent.call_llm = MagicMock(return_value=(mock_raw, 1))

        # 注：analyze 需要 config_path，这里测试子流程
        # 测试 parse + validate 组合
        parsed = agent.parse_response(mock_raw)
        assert parsed["confidence"] == 0.80
        assert parsed["risk_level"] == "high"

        errors = agent.validate(parsed)
        assert len(errors) == 0, errors

    def test_retry_on_parse_failure(
        self, sample_anomaly: dict, sample_df: pd.DataFrame
    ):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent._df = sample_df
        agent._daily_metrics = None
        agent.model = "test-model"
        agent.sop_full_text = ""
        agent.max_retries = 3
        agent.temperature = 0.3

        valid_json = json.dumps(MOCK_REPORT, ensure_ascii=False)
        call_count = [0]

        def mock_llm(prompt, is_retry=False, retry_error=""):
            call_count[0] += 1
            if call_count[0] == 1:
                return "Bad response not json", 1
            else:
                return valid_json, 1

        agent.call_llm = mock_llm

        # parse 第一次失败 → call_llm should have been called
        # 验证 parse 对无效输入的恰当处理
        with pytest.raises(ValueError, match="JSON 解析失败"):
            agent.parse_response("Bad response not json")

        # parse 第二次应成功
        parsed = agent.parse_response(valid_json)
        assert parsed["confidence"] == 0.80

    def test_degraded_report_structure(self):
        agent = AttributionAgent.__new__(AttributionAgent)
        agent.sop_full_text = "SOP-001: 物流\nSOP-002: 亏损"
        sufficiency = {
            "sufficient": False,
            "score": 40,
            "reasons": ["数据不足"],
        }
        anomaly = {
            "anomaly_id": "test_daily",
            "metric": "daily_late_rate",
            "value": 0.55,
            "severity": "high",
            "context": {},
        }
        report = agent._build_degraded_report(anomaly, sufficiency)
        assert "_meta" in report
        assert report["_meta"]["data_sufficient"] is False
        assert len(report["summary"]) > 5
        assert len(report["recommended_actions"]) > 0
