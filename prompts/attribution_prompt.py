# -*- coding: utf-8 -*-
"""
归因分析 Prompt 模板 — 让 DeepSeek 扮演供应链专家，分析异常根因。

设计原则：
  - 严格禁止幻觉：LLM 只能基于 prompt 中提供的上下文数据推断，不得编造数字
  - 结构化输出：要求 JSON 格式，附带 JSON Schema 约束
  - 可操作建议：每条建议必须指定负责人和优先级，能直接转为工单
"""

# ============================================================
# 系统角色提示
# ============================================================

SYSTEM_PROMPT = """你是一位资深的供应链分析师，拥有 15 年全球供应链管理经验。你的专长是：
- 从物流数据中识别异常的根因
- 基于数据（而非直觉）给出可操作的处置建议
- 用简洁清晰的中文撰写分析报告

**核心纪律**：
1. 你只能基于我提供的上下文数据进行分析，**绝对禁止编造或猜测数据中没有的数字**
2. 如果数据不足以确定某个原因，请如实标注 confidence 为低（< 0.5）并说明证据不足
3. 所有金额使用美元($)表示，时间使用 YYYY-MM-DD 格式
4. 回复必须是合法的 JSON，不包含任何 JSON 之外的文字"""

# ============================================================
# 异常归因主模板
# ============================================================

ATTRIBUTION_TEMPLATE = """## 异常事件

| 属性 | 值 |
|------|-----|
| 异常ID | {anomaly_id} |
| 发生时间 | {timestamp} |
| 异常指标 | {metric} |
| 实际值 | {value} |
| 正常范围 | {expected_range} |
| 严重程度 | {severity} |
| 检测方法 | {detection_method} |

{anomaly_description}

## 业务上下文

### 该指标的近期走势（前 {lookback_days} 天）
{trend_table}

### 关联订单/产品/客户信息
{order_context}

### 同维度对比数据
{comparison_text}

## 参考 SOP
{sop_text}

## 分析要求

请基于以上信息，按以下 JSON Schema 输出分析结果：

```json
{{
  "root_cause_hypotheses": [
    {{
      "cause": "可能的原因描述（用中文，具体而非笼统）",
      "probability": 0.0到1.0之间的数值,
      "evidence": ["基于上下文中具体数据的证据1", "证据2", "证据3"],
      "against": ["反驳该原因的证据，如果没有则为空数组"]
    }}
  ],
  "recommended_actions": [
    {{
      "action": "具体可执行的处置措施",
      "priority": "high | medium | low",
      "expected_effect": "预期效果描述",
      "owner": "责任人角色（如物流经理、财务分析师）",
      "sop_ref": "引用的SOP编号（如SOP-001），无则填null"
    }}
  ],
  "risk_level": "high | medium | low",
  "risk_rationale": "为什么是这个风险等级的简要说明",
  "confidence": 0.0到1.0之间的数值,
  "confidence_note": "置信度的说明——如果证据充分写'数据充分'，如果证据不足说明缺什么数据",
  "summary": "50字以内的一句话总结"
}}
```

**重要提醒**：
- root_cause_hypotheses 必须给出恰好 3 个假设（不足 3 个时probability 设为 0 并说明证据不足）
- recommended_actions 必须给出恰好 3 条措施
- 所有数值必须来源于上下文数据，不得编造
- 每条 evidence 必须引用上下文中的具体数字或事实
- 如果某个假设缺乏证据，probability 应 < 0.3，并在 against 中说明

请直接输出 JSON："""

# ============================================================
# JSON Schema（用于校验）
# ============================================================

EXPECTED_SCHEMA = {
    "type": "object",
    "required": [
        "root_cause_hypotheses",
        "recommended_actions",
        "risk_level",
        "risk_rationale",
        "confidence",
        "confidence_note",
        "summary",
    ],
    "properties": {
        "root_cause_hypotheses": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["cause", "probability", "evidence", "against"],
            },
        },
        "recommended_actions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["action", "priority", "expected_effect", "owner", "sop_ref"],
            },
        },
        "risk_level": {"type": "string", "enum": ["high", "medium", "low"]},
        "risk_rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "confidence_note": {"type": "string"},
        "summary": {"type": "string"},
    },
}

# ============================================================
# 异常描述模板（按指标类型定制）
# ============================================================

ANOMALY_DESCRIPTIONS = {
    "Benefit per order": (
        "该订单利润异常，相比正常区间 {expected_range} 出现显著偏离。\n"
        "负利润意味着该订单的收入无法覆盖成本，需要重点关注折扣叠加、运费补贴和采购成本。"
    ),
    "shipping_delay_days": (
        "该订单实际运输天数与计划天数的偏差异常，正常区间为 {expected_range}。\n"
        "运输延迟直接影响客户满意度和复购率，需要排查仓库出库效率和承运商履约情况。"
    ),
    "Order Item Profit Ratio": (
        "该订单的利润率异常，正常区间为 {expected_range}。\n"
        "利润率异常偏高可能意味着定价错误或成本数据缺失；异常偏低意味着严重亏损。"
    ),
    "Order Item Total": (
        "该订单金额显著偏离正常范围 {expected_range}。\n"
        "大额订单本身不一定异常，但需要排查是否为批发订单、是否存在价格错误或折扣异常。"
    ),
    "daily_late_rate": (
        "当日的延迟交付率显著偏离正常水平 {expected_range}。\n"
        "日度延迟率的突然波动通常意味着仓库、承运商或订单结构发生了短期变化。"
    ),
    "daily_order_count": (
        "当日的订单量显著偏离正常范围 {expected_range}。\n"
        "订单量的突然变化可能是外部因素（促销、竞品、季节）或内部因素（系统故障、价格错误）导致。"
    ),
    "daily_avg_profit": (
        "当日的平均利润显著偏离正常范围 {expected_range}。\n"
        "日均利润波动通常是订单结构变化（高/低毛利产品占比改变）或折扣力度改变的信号。"
    ),
}

_DEFAULT_DESCRIPTION = "该指标的观测值 {value} 超出了正常范围 {expected_range}，需要排查原因。"


def get_anomaly_description(metric: str, value: float, expected_range: list) -> str:
    """根据指标类型返回定制化的异常描述文本。"""
    template = ANOMALY_DESCRIPTIONS.get(metric, _DEFAULT_DESCRIPTION)
    lo = expected_range[0] if expected_range[0] is not None else "-∞"
    hi = expected_range[1] if expected_range[1] is not None else "+∞"
    range_str = f"[{lo}, {hi}]"
    return template.format(value=value, expected_range=range_str)


# ============================================================
# 重试 Prompt（JSON 解析失败时使用）
# ============================================================

RETRY_PROMPT = """你上一次的回复不是合法的 JSON，解析错误如下：
{error_message}

请严格按照 JSON Schema 重新输出。记住：
1. 只输出 JSON，不要加任何解释文字
2. 确保所有 required 字段都存在
3. 数值类型不要加引号
4. 字符串中的双引号需要转义

直接输出 JSON："""
