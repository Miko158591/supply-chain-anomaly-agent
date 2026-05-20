#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
供应链异常监控主入口 — OpenClaw Skill 执行脚本。

用法:
  python monitor.py                    # 日报模式（默认）
  python monitor.py --mode full        # 全量检测 + 归因
  python monitor.py --mode quick       # 只检测，不归因，不推送
  python monitor.py --lookback 7       # 回溯最近 7 天
  python monitor.py --max 5            # 飞书展示 5 条异常
  python monitor.py --export high      # 导出高风险异常到 Excel
  python monitor.py --no-notify        # 不推送飞书
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import yaml

# Skill 和项目根目录
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_JSON = os.path.join(SKILL_DIR, "config.json")
try:
    with open(CONFIG_JSON, "r", encoding="utf-8") as f:
        skill_cfg = json.load(f)
    PROJECT_ROOT = skill_cfg.get("project_path", "")
    if not PROJECT_ROOT or not os.path.isdir(PROJECT_ROOT):
        raise ValueError("project_path 无效")
except Exception:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(SKILL_DIR))
sys.path.insert(0, PROJECT_ROOT)

from analysis.anomaly_detector import AnomalyDetector
from analysis.attribution_agent import AttributionAgent

# Skill 内部模块（SKILL_DIR 已在上面定义）
sys.path.insert(0, SKILL_DIR)
from message_formatter import format_daily_summary
from session_store import handle_message as handle_reply

os.makedirs(os.path.join(PROJECT_ROOT, "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, "logs", "monitor.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("supply-chain-monitor")


# ================================================================
# 1. 数据加载
# ================================================================

def load_data(raw_dir: str) -> pd.DataFrame:
    csv_path = os.path.join(raw_dir, "DataCoSupplyChainDataset.csv")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"数据文件不存在: {csv_path}")
    df = pd.read_csv(csv_path, encoding="latin-1", low_memory=False)
    df["shipping_delay_days"] = df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    logger.info(f"数据加载完成: {len(df):,} 行")
    return df


# ================================================================
# 2. 异常检测 + 归因
# ================================================================

def run_pipeline(df: pd.DataFrame, config: dict, mode: str,
                 lookback_days: int, max_anomalies: int) -> Dict[str, Any]:
    stats = {"total_anomalies": 0, "severity_counts": {}, "llm_calls": 0,
             "degraded": 0, "errors": 0}
    reports: List[Dict[str, Any]] = []

    logger.info("运行异常检测...")
    detector = AnomalyDetector(config_path=os.path.join(PROJECT_ROOT, "config.yaml"))
    anomalies_df = detector.detect_all(df)
    stats["total_anomalies"] = len(anomalies_df)
    stats["severity_counts"] = anomalies_df["severity"].value_counts().to_dict()
    logger.info(
        f"检出 {stats['total_anomalies']:,} 条异常 "
        f"(high={stats['severity_counts'].get('high', 0):,}, "
        f"medium={stats['severity_counts'].get('medium', 0):,}, "
        f"low={stats['severity_counts'].get('low', 0):,})"
    )

    if mode == "quick":
        logger.info("quick 模式，跳过归因分析")
        return {"anomalies_df": anomalies_df, "reports": reports, "stats": stats}

    logger.info("运行归因分析...")
    try:
        agent = AttributionAgent(config_path=os.path.join(PROJECT_ROOT, "config.yaml"))
        reports = agent.analyze_batch(anomalies_df, df, lookback_days=lookback_days,
                                      max_samples=max_anomalies, verbose=False)
        for r in reports:
            meta = r.get("_meta", {})
            if "error" in r:
                stats["errors"] += 1
            elif meta.get("data_sufficient") is False:
                stats["degraded"] += 1
            else:
                stats["llm_calls"] += 1
        logger.info(f"归因完成: {stats['llm_calls']} LLM, {stats['degraded']} 降级, {stats['errors']} 失败")
    except Exception as e:
        logger.warning(f"归因分析失败: {e}（可能未配置 API Key）")
        stats["errors"] = max_anomalies

    return {"anomalies_df": anomalies_df, "reports": reports, "stats": stats}


# ================================================================
# 3. 飞书推送（智能截断 + 摘要卡片）
# ================================================================

def smart_truncate(text: str, max_len: int = 200) -> str:
    """在句子边界处截断，不破坏中文字符或 emoji。

    优先级：句号 > 感叹号 > 问号 > 换行 > 分号 > 逗号 > 空格。
    """
    if len(text) <= max_len:
        return text
    # 从 70% 位置开始找分隔符（比 80% 更宽，更容易找到句子边界）
    search_start = int(max_len * 0.7)
    chunk = text[search_start:max_len]
    for sep in ["。", "！", "？", "\n", "；", "，", "、", " ", ". "]:
        pos = chunk.rfind(sep)
        if pos >= 0:
            return text[:search_start + pos + len(sep)]
    # 强制截断：退回到最后一个 ASCII 单词边界
    hard_cut = text[:max_len]
    # 如果最后几个字符是英文单词的一部分，回退到前一个空格
    last_space = hard_cut.rfind(" ")
    if last_space > max_len * 0.85:
        return hard_cut[:last_space]
    return hard_cut


def build_feishu_card(reports: List[Dict], stats: Dict, report_date: str) -> Dict[str, Any]:
    """构建飞书日报卡片 — Layer 1 汇总格式，不超过 500 字。"""
    content = format_daily_summary(stats, reports, report_date)

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text",
                          "content": f"供应链异常日报 | {report_date}"},
                "template": "blue",
            },
            "elements": [{
                "tag": "div",
                "text": {"tag": "lark_md", "content": content},
            }],
        },
    }


def _get_tenant_token(app_id: str, app_secret: str) -> Optional[str]:
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        data = resp.json()
        return data["tenant_access_token"] if data.get("code") == 0 else None
    except Exception as e:
        logger.error(f"获取飞书 token 失败: {e}")
        return None


def _send_via_app(app_id: str, app_secret: str, chat_id: str,
                  card: Dict, report_date: str) -> bool:
    token = _get_tenant_token(app_id, app_secret)
    if not token:
        return False
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card["card"]),
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            logger.info(f"飞书推送成功 (chat={chat_id or 'default'})")
            return True
        else:
            logger.warning(f"飞书推送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"飞书推送请求失败: {e}")
        return False


def notify_feishu(lark_cfg: Dict, reports: List[Dict], stats: Dict,
                  report_date: str) -> bool:
    if not reports:
        logger.info("无异常报告，跳过推送")
        return False
    enable = lark_cfg.get("enable_push", False)
    if not enable:
        logger.info("飞书推送未启用 (enable_push=false)")
        return False

    card = build_feishu_card(reports, stats, report_date)

    app_id = lark_cfg.get("app_id", "")
    app_secret = lark_cfg.get("app_secret", "")
    chat_id = lark_cfg.get("chat_id", "")
    webhook_url = lark_cfg.get("webhook_url", "")

    # 方式 A：企业应用
    if app_id and app_secret and "your-" not in app_id:
        logger.info("尝试飞书应用 API 推送...")
        if _send_via_app(app_id, app_secret, chat_id, card, report_date):
            return True

    # 方式 B：Webhook 兜底
    if webhook_url and "your-webhook" not in webhook_url:
        logger.info("尝试飞书 Webhook...")
        try:
            resp = requests.post(webhook_url, json=card, timeout=15)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                logger.info("飞书 Webhook 推送成功")
                return True
        except Exception as e:
            logger.error(f"飞书 Webhook 请求失败: {e}")

    logger.warning("飞书推送失败：无可用方式")
    return False


# ================================================================
# 4. 报告保存
# ================================================================

def save_report(reports: List[Dict], stats: Dict, output_dir: str,
                anomalies_df: Optional[pd.DataFrame] = None) -> str:
    today = date.today().isoformat()
    filepath = os.path.join(output_dir, f"daily_report_{today}.json")
    os.makedirs(output_dir, exist_ok=True)
    payload = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "stats": stats,
        "reports": reports,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"报告已保存: {filepath}")

    # 保存全部异常数据为 CSV（供 Excel 导出用）
    if anomalies_df is not None and not anomalies_df.empty:
        csv_path = os.path.join(output_dir, f"anomalies_full_{today}.csv")
        export_df = anomalies_df.copy()
        # 展开 context 字典中的订单号等关键字段
        ctx_keys = ["Order Id", "Category Name", "Product Name",
                    "Order Region", "Order City", "Market",
                    "Shipping Mode", "Delivery Status"]
        for k in ctx_keys:
            export_df[k] = export_df["context"].apply(
                lambda c, key=k: c.get(key, "") if isinstance(c, dict) else ""
            )
        # 保留核心列
        key_cols = [
            "Order Id", "Category Name", "Product Name",
            "metric", "value", "severity", "detection_method",
            "Order Region", "Market", "Shipping Mode", "Delivery Status",
        ]
        available = [c for c in key_cols if c in export_df.columns]
        export_df[available].to_csv(csv_path, index=False, encoding="utf-8")
        logger.info(f"全部异常数据已保存: {csv_path} ({len(export_df):,} 条)")
        return filepath, csv_path

    return filepath


# ================================================================
# 5. 主入口
# ================================================================

def main():
    parser = argparse.ArgumentParser(description="供应商异常监控 Skill")
    parser.add_argument("--mode", choices=["daily", "full", "quick"], default="daily")
    parser.add_argument("--lookback", type=int, default=7)
    parser.add_argument("--max", dest="max_anomalies", type=int, default=5)
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--export", type=str, default=None,
                        choices=["high", "medium", "all"])
    parser.add_argument("--reply", type=str, default=None,
                        help="模拟飞书回复: 数字(1-5)/详情/全部/导出")
    args = parser.parse_args()

    config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("config.yaml 未找到")
        config = {}

    lark_cfg = config.get("notify", {}).get("lark", {})
    if args.no_notify:
        lark_cfg = {**lark_cfg, "enable_push": False}

    report_date = args.date or date.today().isoformat()

    start_time = time.time()
    logger.info(f"===== 供应链异常监控启动 | mode={args.mode} | date={report_date} =====")

    try:
        df = load_data(os.path.join(PROJECT_ROOT, "data", "raw"))
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    result = run_pipeline(df, config, mode=args.mode,
                          lookback_days=args.lookback,
                          max_anomalies=args.max_anomalies)

    # Excel 导出
    excel_path = None
    if args.export and not result["anomalies_df"].empty:
        anomalies_df = result["anomalies_df"]
        if args.export == "high":
            to_export = anomalies_df[anomalies_df["severity"] == "high"]
        elif args.export == "medium":
            to_export = anomalies_df[anomalies_df["severity"].isin(["high", "medium"])]
        else:
            to_export = anomalies_df

        excel_dir = os.path.join(PROJECT_ROOT, "data", "output")
        excel_path = os.path.join(excel_dir, f"anomalies_{args.export}_{report_date}.xlsx")
        os.makedirs(excel_dir, exist_ok=True)

        export_df = to_export.copy()
        export_df["context_str"] = export_df["context"].apply(
            lambda c: json.dumps(c, ensure_ascii=False, default=str) if isinstance(c, dict) else str(c))
        export_df = export_df.drop(columns=["context"], errors="ignore")
        export_df.to_excel(excel_path, index=False, engine="openpyxl")
        logger.info(f"导出 {len(to_export):,} 条异常 -> {excel_path}")

    # 保存 JSON 报告
    report_path = save_report(
        result["reports"], result["stats"],
        os.path.join(PROJECT_ROOT, "data", "output"),
        anomalies_df=result.get("anomalies_df"))

    # ── 模拟飞书回复 ──
    if args.reply:
        # 从最近一次日报 JSON 读取已归因的报告
        saved_path = os.path.join(PROJECT_ROOT, "data", "output",
                                  f"daily_report_{report_date}.json")
        if not os.path.exists(saved_path):
            print(f"错误: 日报文件不存在 {saved_path}，请先运行 --mode daily")
            sys.exit(1)

        with open(saved_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        saved_reports = saved_data.get("reports", [])
        saved_stats = saved_data.get("stats", {})

        reply_text = handle_reply(
            chat_id="mock-chat",
            user_id="mock-user",
            text=args.reply,
            reports=saved_reports,
            stats=saved_stats,
            report_date=report_date,
        )
        if reply_text:
            print("\n" + "=" * 50)
            print(reply_text)
            print("=" * 50)
        else:
            print(f"未识别命令: {args.reply}")

    # ── 飞书推送 ──
    feishu_ok = notify_feishu(lark_cfg, result["reports"], result["stats"], report_date)

    elapsed = time.time() - start_time
    logger.info(
        f"===== 完成 | 耗时 {elapsed:.1f}s | "
        f"异常 {result['stats']['total_anomalies']:,} | "
        f"LLM {result['stats']['llm_calls']} | "
        f"降级 {result['stats']['degraded']} | "
        f"推送 {'OK' if feishu_ok else 'SKIP'} =====")

    summary = {
        "date": report_date,
        "mode": args.mode,
        "elapsed_seconds": round(elapsed, 1),
        "total_anomalies": result["stats"]["total_anomalies"],
        "llm_attributions": result["stats"]["llm_calls"],
        "degraded": result["stats"]["degraded"],
        "feishu_sent": feishu_ok,
        "report_path": report_path,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    main()
