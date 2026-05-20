# -*- coding: utf-8 -*-
"""飞书推送模块测试 — 智能截断、卡片长度校验。"""
import sys, os, json, importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_skill_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "skills", "supply-chain-monitor", "monitor.py")
_spec = importlib.util.spec_from_file_location("monitor", _skill_path)
_monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_monitor)

smart_truncate = _monitor.smart_truncate
build_feishu_card = _monitor.build_feishu_card

PASS = FAIL = 0


def check(condition, name):
    global PASS, FAIL
    if condition:
        PASS += 1; print(f"  [PASS] {name}")
    else:
        FAIL += 1; print(f"  [FAIL] {name}")


def test_smart_truncate():
    global PASS, FAIL
    print("=== smart_truncate ===")
    check(smart_truncate("短文本", 200) == "短文本", "短文本原样返回")
    check(smart_truncate("", 10) == "", "空字符串")
    check(smart_truncate("abc", 3) == "abc", "恰好等于上限")

    # 句号截断: 在最后20%范围内找到"。"，截断在句子边界
    t = "第一句话。第二句话很长很长很长很长很长很长很长很长很长很长很长很长。第三句。"
    result = smart_truncate(t, 40)
    check(result.endswith("。") or result.endswith("句"),
          f"句号截断: len={len(result)}, ends=[{result[-5:]}]")

    # 换行截断
    t2 = "标题\n正文内容很长很长很长很长很长很长很长很长很长很长很长很长"
    result2 = smart_truncate(t2, 20)
    check("\n" in result2 and len(result2) <= 20,
          f"换行截断: [{result2[:20]}]")

    # 逗号截断
    t3 = "这是一个长句子，然后继续写很多内容还是没有句号"
    result3 = smart_truncate(t3, 18)
    check("，" in result3, f"逗号截断: [{result3}]")

    # 强制截断（无任何分隔符）
    t4 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop"
    result4 = smart_truncate(t4, 20)
    check(len(result4) <= 20 and result4 == t4[:20], f"强制截断: len={len(result4)}")

    # emoji 不破坏
    t5 = "订单#33 异常分析中包括emoji后续还有很多内容"
    result5 = smart_truncate(t5, 15)
    check(len(result5) <= 15, f"Emoji安全: len={len(result5)}")

    # 中英混合
    t6 = "订单#33 profit=-277.09需要排查原因。后面还有很多内容要截断"
    result6 = smart_truncate(t6, 35)
    check("。" in result6, f"中英混合截断: [{result6}]")

    print(f"  smart_truncate: {PASS} PASS, {FAIL} FAIL")


def test_card_sizes():
    global PASS, FAIL
    print("\n=== 卡片长度校验 ===")
    report_path = "data/output/daily_report_2026-05-20.json"
    if not os.path.exists(report_path):
        print("  跳过 (无报告文件)")
        return

    with open(report_path, encoding="utf-8") as f:
        data = json.load(f)

    if not data.get("reports"):
        print("  跳过 (报告为空)")
        return

    card = build_feishu_card(data["reports"], data["stats"], "2026-05-20")
    card_json = json.dumps(card, ensure_ascii=False)
    card_bytes = len(card_json.encode("utf-8"))
    print(f"  卡片总大小: {card_bytes:,} bytes ({card_bytes/1024:.1f} KB)")
    check(card_bytes < 30 * 1024, "卡片 < 30KB (飞书限制 30KB)")

    for i, el in enumerate(card["card"]["elements"]):
        if el.get("tag") == "div":
            content = el["text"]["content"]
            has_broken = '�' in content or '�' in content
            print(f"  元素 #{i}: {len(content)} 字" + (" [含乱码!]" if has_broken else ""))
            check(len(content) < 4000, f"元素 #{i} < 4000 字 (lark_md 限制)")
            check(not has_broken, f"元素 #{i} 无 Unicode 替换字符")

    print(f"\n  卡片内容: {PASS} PASS, {FAIL} FAIL")


def test_real_truncation():
    global PASS, FAIL
    print("\n=== 真实数据截断 ===")
    report_path = "data/output/daily_report_2026-05-20.json"
    if not os.path.exists(report_path):
        print("  跳过")
        return

    with open(report_path, encoding="utf-8") as f:
        data = json.load(f)

    for i, r in enumerate(data.get("reports", [])[:5]):
        summary = r.get("summary", "")
        s = smart_truncate(summary, 150)
        actions = r.get("recommended_actions", [])
        action_text = actions[0]["action"] if actions else ""
        a = smart_truncate(action_text, 80)

        print(f"  #{i+1}: summary {len(summary)}->{len(s)}字, "
              f"action {len(action_text)}->{len(a)}字")

        # 检查截断点是否在句子边界
        if s != summary:
            last_char = s[-1] if s else ''
            is_boundary = last_char in "。！？\n；，、"
            check(is_boundary or len(s) == 150,
                  f"截断在句子边界/强制截断: ...{s[-5:]}")
        if a != action_text and len(a) > 0:
            check(not any(0xfffd == ord(c) for c in a[-3:]),
                  f"action 截断不产生乱码")

    print(f"  真实数据截断: {PASS} PASS, {FAIL} FAIL")


if __name__ == "__main__":
    test_smart_truncate()
    test_card_sizes()
    test_real_truncation()
    print(f"\n{'='*50}")
    print(f"总计: {PASS} PASS, {FAIL} FAIL")
