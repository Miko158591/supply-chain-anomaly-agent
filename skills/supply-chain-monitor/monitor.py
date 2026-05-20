#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
供应链异常监控主入口 — OpenClaw Skill 执行脚本。

用法:
  python monitor.py                    # 日报模式（默认）
  python monitor.py --mode full        # 全量检测 + 归因
  python monitor.py --mode quick       # 只检测，不归因，不推送
  python monitor.py --lookback 3       # 回溯最近 3 天
  python monitor.py --max 10           # 飞书展示 10 条异常
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

# 确定项目根目录：优先读 config.json 中的 project_path，兜底按 skill 位置推算
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

# ── 日志 ──────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════
# 1. 数据加载
# ══════════════════════════════════════════════════════════════

def load_data(raw_dir: str) -> pd.DataFrame:
    """加载 DataCo 数据集并做基础预处理。"""
    csv_path = os.path.join(raw_dir, "DataCoSupplyChainDataset.csv")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"数据文件不存在: {csv_path}")

    df = pd.read_csv(csv_path, encoding="latin-1", low_memory=False)
    df["shipping_delay_days"] = df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    logger.info(f"数据加载完成: {len(df):,} 行")
    return df


# ══════════════════════════════════════════════════════════════
# 2. 异常检测 + 归因
# ══════════════════════════════════════════════════════════════

def run_pipeline(df: pd.DataFrame, config: dict, mode: str,
                 lookback_days: int, max_anomalies: int) -> Dict[str, Any]:
    """执行完整的检测→归因流水线。

    Returns
    -------
    dict — {"anomalies_df": DataFrame, "reports": list, "stats": dict}
    """
    cfg = config.get("anomaly", {})

    # ── 2a. 异常检测 ──
    logger.info("运行异常检测...")
    detector = AnomalyDetector(config_path=os.path.join(PROJECT_ROOT, "config.yaml"))
    anomalies_df = detector.detect_all(df)
    total_anomalies = len(anomalies_df)
    severity_counts = anomalies_df["severity"].value_counts().to_dict()
    logger.info(f"检出 {total_anomalies:,} 条异常 "
                f"(high={severity_counts.get('high', 0):,}, "
                f"medium={severity_counts.get('medium', 0):,}, "
                f"low={severity_counts.get('low', 0):,})")

    # ── 2b. 归因分析 ──
    reports: List[Dict[str, Any]] = []
    stats = {
        "total_anomalies": total_anomalies,
        "severity_counts": severity_counts,
        "llm_calls": 0,
        "degraded": 0,
        "errors": 0,
    }

    if mode == "quick":
        logger.info("quick 模式，跳过归因分析")
        return {"anomalies_df": anomalies_df, "reports": reports, "stats": stats}

    logger.info("运行归因分析...")
    try:
        agent = AttributionAgent(config_path=os.path.join(PROJECT_ROOT, "config.yaml"))
        reports = agent.analyze_batch(
            anomalies_df, df,
            lookback_days=lookback_days,
            max_samples=max_anomalies,
            verbose=False,
        )

        for r in reports:
            meta = r.get("_meta", {})
            if "error" in r:
                stats["errors"] += 1
            elif meta.get("data_sufficient") is False:
                stats["degraded"] += 1
            else:
                stats["llm_calls"] += 1

        logger.info(f"归因完成: {stats['llm_calls']} 次 LLM, "
                    f"{stats['degraded']} 次降级, {stats['errors']} 次失败")
    except Exception as e:
        logger.warning(f"归因分析失败: {e}（可能未配置 API Key）")
        stats["errors"] = max_anomalies

    return {"anomalies_df": anomalies_df, "reports": reports, "stats": stats}


# ══════════════════════════════════════════════════════════════
# 3. 飞书推送
# ══════════════════════════════════════════════════════════════

def _severity_color(sev: str) -> str:
    return {"high": "red", "medium": "yellow", "low": "green"}.get(sev, "default")


def _truncate(text: str, max_len: int = 80) -> str:
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


def build_feishu_card(reports: List[Dict], stats: Dict, report_date: str) -> Dict[str, Any]:
    """构建飞书卡片消息 JSON。

    格式：标题 + 概览统计 + 每条异常一个折叠区域 + 页脚。
    """
    llm_reports = [r for r in reports
                   if "error" not in r and r.get("_meta", {}).get("data_sufficient") is not False]
    degraded_reports = [r for r in reports
                        if "error" not in r and r.get("_meta", {}).get("data_sufficient") is False]

    n_total = stats["total_anomalies"]
    n_llm = len(llm_reports)
    n_degraded = len(degraded_reports)
    n_high = stats["severity_counts"].get("high", 0)

    # ── 构建 card elements ──
    elements: List[Dict] = []

    # 概览统计
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**共检出 {n_total:,} 个异常**，"
                f"其中 🔴 高风险 {n_high:,} 个\\n"
                f"AI 归因 {n_llm} 个 | 数据不足降级 {n_degraded} 个"
            ),
        },
    })
    elements.append({"tag": "hr"})

    # 每个归因结果
    for i, report in enumerate(llm_reports[:5]):
        meta = report.get("_meta", {})
        risk = report.get("risk_level", "medium")
        color = _severity_color(risk)
        summary = _truncate(report.get("summary", ""), 100)

        # 取第一条处置建议
        actions = report.get("recommended_actions", [])
        top_action = actions[0]["action"] if actions else "人工排查"

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**{i+1}. [{risk.upper()}] {summary}**\\n"
                    f"置信度: {report.get('confidence', 0):.0%} | "
                    f"建议: {_truncate(top_action, 60)}\\n"
                    f"——{report.get('risk_rationale', '')[:80]}"
                ),
            },
        })
        elements.append({"tag": "hr"})

    # 降级异常（数据不足）
    if degraded_reports:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"⚠️ **{len(degraded_reports)} 个异常因数据不足跳过 AI 归因**，建议人工排查",
            },
        })
        elements.append({"tag": "hr"})

    # 页脚
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"由 AutoClaw · Supply Chain Monitor 自动生成 | {report_date}",
        },
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📦 供应链异常日报 | {report_date}",
                },
                "template": _severity_color("high") if n_high > n_total * 0.3 else "blue",
            },
            "elements": elements,
        },
    }


def _get_tenant_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取飞书企业自建应用的 tenant_access_token。"""
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return data["tenant_access_token"]
        else:
            logger.error(f"获取飞书 token 失败: {data}")
            return None
    except Exception as e:
        logger.error(f"获取飞书 token 请求失败: {e}")
        return None


def _send_via_app(app_id: str, app_secret: str, chat_id: str,
                  card: Dict, report_date: str) -> bool:
    """通过飞书应用 API 发送卡片消息。"""
    token = _get_tenant_token(app_id, app_secret)
    if not token:
        return False

    # 如果有 chat_id 就用它，否则不指定（可能往 webhook 对应的群发）
    payload: Dict[str, Any] = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card["card"]),
    }

    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            logger.info(f"飞书应用推送成功 (chat={chat_id or 'default'})")
            return True
        else:
            logger.warning(f"飞书应用推送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"飞书应用推送请求失败: {e}")
        return False


def notify_feishu(lark_cfg: Dict, reports: List[Dict], stats: Dict,
                  report_date: str) -> bool:
    """推送飞书卡片消息。优先用企业应用 API，兜底用 webhook。"""
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

    # 方式 A：企业自建应用
    if app_id and app_secret and "your-" not in app_id:
        logger.info("尝试飞书应用 API 推送...")
        if _send_via_app(app_id, app_secret, chat_id, card, report_date):
            return True
        logger.info("应用 API 推送失败，尝试 webhook 兜底...")

    # 方式 B：Webhook 兜底
    if webhook_url and "your-webhook" not in webhook_url:
        logger.info("尝试飞书 Webhook 推送...")
        try:
            resp = requests.post(webhook_url, json=card, timeout=15)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                logger.info("飞书 Webhook 推送成功")
                return True
            else:
                logger.warning(f"飞书 Webhook 推送失败: {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"飞书 Webhook 请求失败: {e}")
            return False

    logger.warning("飞书推送失败：无可用方式 (app_id/webhook 均未正确配置)")
    return False


# ══════════════════════════════════════════════════════════════
# 4. 报告保存
# ══════════════════════════════════════════════════════════════

def save_report(reports: List[Dict], stats: Dict, output_dir: str) -> str:
    """保存完整 JSON 报告到 data/output/。"""
    today = date.today().isoformat()
    filename = f"daily_report_{today}.json"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    # 序列化报告：datetime 等特殊类型转字符串
    payload = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "stats": stats,
        "reports": reports,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"报告已保存: {filepath}")
    return filepath


# ══════════════════════════════════════════════════════════════
# 5. 主入口
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="供应链异常监控 Skill")
    parser.add_argument("--mode", choices=["daily", "full", "quick"], default="daily",
                        help="运行模式: daily(日报) / full(全量) / quick(只检测)")
    parser.add_argument("--lookback", type=int, default=7,
                        help="回溯天数 (默认 7)")
    parser.add_argument("--max", dest="max_anomalies", type=int, default=5,
                        help="归因/飞书展示的异常数量上限")
    parser.add_argument("--no-notify", action="store_true",
                        help="跳过飞书推送")
    parser.add_argument("--date", type=str, default=None,
                        help="指定报告日期 (YYYY-MM-DD)，默认今天")
    args = parser.parse_args()

    # ── 加载配置 ──
    config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("config.yaml 未找到，使用空配置")
        config = {}

    lark_cfg = config.get("notify", {}).get("lark", {})
    if args.no_notify:
        lark_cfg = {**lark_cfg, "enable_push": False}

    report_date = args.date or date.today().isoformat()

    # ── 执行流水线 ──
    start_time = time.time()
    logger.info(f"===== 供应链异常监控启动 | mode={args.mode} | date={report_date} =====")

    try:
        df = load_data(os.path.join(PROJECT_ROOT, "data", "raw"))
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    result = run_pipeline(
        df, config,
        mode=args.mode,
        lookback_days=args.lookback,
        max_anomalies=args.max_anomalies,
    )

    # ── 保存报告 ──
    report_path = save_report(
        result["reports"], result["stats"],
        os.path.join(PROJECT_ROOT, "data", "output"),
    )

    # ── 飞书推送 ──
    feishu_ok = notify_feishu(
        lark_cfg, result["reports"], result["stats"],
        report_date,
    )

    # ── 汇总 ──
    elapsed = time.time() - start_time
    logger.info(f"===== 完成 | 耗时 {elapsed:.1f}s | "
                f"异常 {result['stats']['total_anomalies']:,} | "
                f"LLM {result['stats']['llm_calls']} | "
                f"降级 {result['stats']['degraded']} | "
                f"推送 {'OK' if feishu_ok else 'SKIP'} =====")

    # 返回 JSON 摘要供 OpenClaw 读取
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
