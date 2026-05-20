#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书事件订阅 Webhook 服务器 — 接收用户回复并路由到三层消息机制。

用法:
  python feishu_webhook.py                  # 默认 0.0.0.0:8080
  python feishu_webhook.py --port 9000      # 自定义端口
  python feishu_webhook.py --host 127.0.0.1 # 仅本地

前置条件:
  1. 飞书开放平台 → 事件订阅 → 配置请求 URL (公网可达, 如 ngrok)
  2. 订阅事件: im.message.receive_v1
  3. 机器人已加入目标群聊
"""

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

import requests
import yaml
from flask import Flask, request, jsonify

# ---- 路径设置 ----
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
sys.path.insert(0, os.path.dirname(os.path.dirname(SKILL_DIR)))

from session_store import handle_message as handle_reply
from message_formatter import format_daily_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("feishu-webhook")

app = Flask(__name__)


# ═══════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════

def load_config():
    """加载 config.yaml。"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(SKILL_DIR)), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_lark_cfg():
    cfg = load_config()
    return cfg.get("notify", {}).get("lark", {})


# ═══════════════════════════════════════════
# 飞书 API 工具
# ═══════════════════════════════════════════

def get_tenant_token(app_id: str, app_secret: str) -> Optional[str]:
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        data = resp.json()
        return data["tenant_access_token"] if data.get("code") == 0 else None
    except Exception as e:
        logger.error(f"获取飞书 token 失败: {e}")
        return None


def send_text_to_chat(app_id: str, app_secret: str, chat_id: str,
                      text: str) -> bool:
    """向指定群聊发送文本消息。"""
    token = get_tenant_token(app_id, app_secret)
    if not token:
        return False
    content = json.dumps({"text": text})
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": content,
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            logger.info(f"文本回复成功 (chat={chat_id})")
            return True
        else:
            logger.warning(f"文本回复失败: {data}")
            return False
    except Exception as e:
        logger.error(f"文本回复请求失败: {e}")
        return False


def upload_file(app_id: str, app_secret: str, file_path: str) -> Optional[str]:
    """上传文件到飞书，返回 file_key。"""
    token = get_tenant_token(app_id, app_secret)
    if not token:
        return None
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (os.path.basename(file_path), f)},
                data={"file_type": "xls"},
                timeout=30,
            )
        data = resp.json()
        if data.get("code") == 0:
            return data["data"]["file_key"]
        else:
            logger.warning(f"文件上传失败: {data}")
            return None
    except Exception as e:
        logger.error(f"文件上传异常: {e}")
        return None


def send_file_to_chat(app_id: str, app_secret: str, chat_id: str,
                      file_path: str) -> bool:
    """上传文件并发送到群聊。"""
    file_key = upload_file(app_id, app_secret, file_path)
    if not file_key:
        return False
    token = get_tenant_token(app_id, app_secret)
    if not token:
        return False
    content = json.dumps({"file_key": file_key})
    payload = {"receive_id": chat_id, "msg_type": "file", "content": content}
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            logger.info(f"文件发送成功: {file_path}")
            return True
        else:
            logger.warning(f"文件发送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"文件发送异常: {e}")
        return False


def generate_excel(project_root: str, severity: str, report_date: str) -> Optional[str]:
    """生成 Excel 导出文件，返回文件路径。

    优先从全部异常 CSV 读取所有记录，再用 LLM 归因数据补充原因/建议列。
    """
    import pandas as pd

    output_dir = os.path.join(project_root, "data", "output")

    # 1) 尝试加载全部异常 CSV
    csv_path = os.path.join(output_dir, f"anomalies_full_{report_date}.csv")
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, encoding="utf-8")
        # 筛选风险等级
        if severity == "high":
            df = df[df["severity"] == "high"]
        elif severity == "medium":
            df = df[df["severity"].isin(["high", "medium"])]
        df = df.copy()
        df.rename(columns={
            "Order Id": "订单号",
            "Category Name": "品类",
            "Product Name": "产品名",
            "metric": "检测指标",
            "value": "异常值",
            "severity": "风险等级",
            "detection_method": "检测方法",
            "Order Region": "区域",
            "Market": "市场",
            "Shipping Mode": "运输方式",
            "Delivery Status": "交付状态",
        }, inplace=True)
        if "风险等级" in df.columns:
            df["风险等级"] = df["风险等级"].str.upper()

        # 2) 加载 LLM 归因数据并合并
        report_data = load_latest_report(project_root)
        if report_data:
            reports = report_data.get("reports", [])
            attr_map = {}
            for r in reports:
                if "error" in r:
                    continue
                ctx = r.get("_meta", {}).get("anomaly_context", r.get("context", {}))
                oid = str(ctx.get("Order Id", ""))
                hyps = r.get("root_cause_hypotheses", [])
                actions = r.get("recommended_actions", [])
                attr_map[oid] = {
                    "AI置信度": f"{r.get('confidence', 0):.0%}",
                    "AI根因": hyps[0].get("cause", "") if hyps else "",
                    "处置建议": actions[0].get("action", "") if actions else "",
                    "负责人": actions[0].get("owner", "") if actions else "",
                }

            # 标准化订单号（去掉 float 的 .0 后缀）
            def norm_oid(v):
                s = str(v).strip()
                if s.endswith(".0"):
                    s = s[:-2]
                return s

            df["_oid_norm"] = df["订单号"].apply(norm_oid)
            df["AI置信度"] = df["_oid_norm"].map(
                lambda o: attr_map.get(o, {}).get("AI置信度", "")
            )
            df["AI根因"] = df["_oid_norm"].map(
                lambda o: attr_map.get(o, {}).get("AI根因", "")
            )
            df["处置建议"] = df["_oid_norm"].map(
                lambda o: attr_map.get(o, {}).get("处置建议", "")
            )
            df["负责人"] = df["_oid_norm"].map(
                lambda o: attr_map.get(o, {}).get("负责人", "")
            )
            df.drop(columns=["_oid_norm"], inplace=True)

    else:
        # 兜底：只用 LLM 归因数据
        report_data = load_latest_report(project_root)
        if not report_data:
            return None
        reports = report_data.get("reports", [])
        rows = []
        for r in reports:
            if "error" in r:
                continue
            risk = r.get("risk_level", "low")
            if severity == "high" and risk != "high":
                continue
            if severity == "medium" and risk not in ("high", "medium"):
                continue
            ctx = r.get("_meta", {}).get("anomaly_context", r.get("context", {}))
            hyps = r.get("root_cause_hypotheses", [])
            actions = r.get("recommended_actions", [])
            rows.append({
                "订单号": ctx.get("Order Id", ""),
                "风险等级": risk.upper(),
                "AI置信度": f"{r.get('confidence', 0):.0%}",
                "异常概述": r.get("summary", ""),
                "AI根因": hyps[0].get("cause", "") if hyps else "",
                "处置建议": actions[0].get("action", "") if actions else "",
                "负责人": actions[0].get("owner", "") if actions else "",
            })
        if not rows:
            return None
        df = pd.DataFrame(rows)

    if df.empty:
        return None

    os.makedirs(output_dir, exist_ok=True)
    filename = f"anomalies_{severity}_{report_date}.xlsx"
    filepath = os.path.join(output_dir, filename)
    df.to_excel(filepath, index=False, engine="openpyxl")
    logger.info(f"Excel 已生成: {filepath} ({len(df):,} 条)")
    return filepath# ═══════════════════════════════════════════
# 消息解析
# ═══════════════════════════════════════════

def _extract_text_from_content(content_str: str) -> str:
    """从飞书消息 content JSON 中提取纯文本。

    飞书文本消息的 content 格式: {"text":"@机器人 全部"}
    也兼容纯文本非 JSON 格式。
    """
    try:
        obj = json.loads(content_str)
        if isinstance(obj, dict) and "text" in obj:
            return obj["text"]
    except (json.JSONDecodeError, TypeError):
        pass
    return content_str


def _strip_mentions(text: str) -> str:
    """移除 @提及，只保留命令文本。

    飞书群聊中 @机器人 的格式可能是:
      - "@机器人名 全部"
      - "@_user_1 全部" (at 标签格式)
      - 直接文本 "全部"
    """
    # 移除 @_xxx 格式的 at 标签
    text = re.sub(r"@\S+\s*", "", text)
    return text.strip()


def extract_command(raw_content: str) -> str:
    """从飞书消息原始内容中提取用户命令。"""
    text = _extract_text_from_content(raw_content)
    return _strip_mentions(text)


# ═══════════════════════════════════════════
# 日报加载
# ═══════════════════════════════════════════

def load_latest_report(project_root: str) -> Optional[Dict]:
    """加载最近一次日报 JSON，找不到返回 None。"""
    output_dir = os.path.join(project_root, "data", "output")
    if not os.path.isdir(output_dir):
        return None

    # 找最新的 daily_report_*.json
    files = sorted(
        [f for f in os.listdir(output_dir)
         if f.startswith("daily_report_") and f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return None

    latest = os.path.join(output_dir, files[0])
    try:
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载日报失败: {e}")
        return None


# ═══════════════════════════════════════════
# Webhook 路由
# ═══════════════════════════════════════════

@app.route("/feishu/event", methods=["POST"])
def feishu_event():
    """飞书事件订阅统一入口。

    处理两类请求:
      1. URL 验证 (type=url_verification) — 返回 challenge
      2. 事件推送 (im.message.receive_v1) — 路由到 handle_message
    """
    body = request.get_json(force=True, silent=True) or {}

    # 1) URL 验证
    if body.get("type") == "url_verification":
        challenge = body.get("challenge", "")
        logger.info(f"URL 验证请求, challenge={challenge[:20]}...")
        return jsonify({"challenge": challenge})

    # 2) 事件推送 — 先回 200，再异步处理（避免飞书 3 秒超时重发）
    header = body.get("header", {})
    event_type = header.get("event_type", "")
    event = body.get("event", {})

    if event_type != "im.message.receive_v1":
        return jsonify({"code": 0})

    message = event.get("message", {})
    if message.get("message_type") != "text":
        return jsonify({"code": 0})

    chat_id = message.get("chat_id", "")
    raw_content = message.get("content", "")
    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", {})
    user_id = sender_id.get("open_id") or sender_id.get("user_id") or "unknown"
    command = extract_command(raw_content)

    if not command:
        return jsonify({"code": 0})

    logger.info(f"收到消息: chat={chat_id} user={user_id} command='{command}'")

    # 同步处理（文件上传可在 3 秒内完成）
    _process_command(chat_id, user_id, command)

    return jsonify({"code": 0})


def _process_command(chat_id: str, user_id: str, command: str):
    """在后台线程中处理命令并回复。"""
    try:
        _process_command_inner(chat_id, user_id, command)
    except Exception as e:
        logger.error(f"处理命令异常: {e}", exc_info=True)
        lark_cfg = get_lark_cfg()
        app_id = lark_cfg.get("app_id", "")
        app_secret = lark_cfg.get("app_secret", "")
        if app_id and app_secret:
            send_text_to_chat(app_id, app_secret, chat_id,
                            f"处理命令失败: {e}")


def _process_command_inner(chat_id: str, user_id: str, command: str):
    """实际处理逻辑。"""
    project_root = os.path.dirname(os.path.dirname(SKILL_DIR))
    report_data = load_latest_report(project_root)

    lark_cfg = get_lark_cfg()
    app_id = lark_cfg.get("app_id", "")
    app_secret = lark_cfg.get("app_secret", "")

    if report_data is None:
        if app_id and app_secret:
            send_text_to_chat(app_id, app_secret, chat_id,
                             "暂无日报数据。请先生成日报。")
        return

    reports = report_data.get("reports", [])
    stats = report_data.get("stats", {})
    report_date_str = report_data.get("date", date.today().isoformat())

    # Excel 命令
    if command in ("全部", "高风险"):
        filepath = generate_excel(project_root, "high", report_date_str)
        if filepath and app_id and app_secret:
            send_file_to_chat(app_id, app_secret, chat_id, filepath)
        elif not filepath:
            send_text_to_chat(app_id, app_secret, chat_id,
                             "暂无高风险异常数据。")
        return

    if command == "中风险":
        filepath = generate_excel(project_root, "medium", report_date_str)
        if filepath and app_id and app_secret:
            send_file_to_chat(app_id, app_secret, chat_id, filepath)
        elif not filepath:
            send_text_to_chat(app_id, app_secret, chat_id,
                             "暂无中风险异常数据。")
        return

    # 文本命令
    reply_text = handle_reply(
        chat_id=chat_id, user_id=user_id, text=command,
        reports=reports, stats=stats, report_date=report_date_str,
    )

    if reply_text and app_id and app_secret:
        send_text_to_chat(app_id, app_secret, chat_id, reply_text)
    elif reply_text is None:
        logger.info(f"未识别命令: '{command}'")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": time.time()})


@app.route("/debug", methods=["GET"])
def debug():
    """诊断端点：检查日报加载状态。"""
    project_root = os.path.dirname(os.path.dirname(SKILL_DIR))
    output_dir = os.path.join(project_root, "data", "output")
    exists = os.path.isdir(output_dir)
    files = []
    if exists:
        files = sorted(
            [f for f in os.listdir(output_dir)
             if f.startswith("daily_report_") and f.endswith(".json")],
            reverse=True,
        )
    report = load_latest_report(project_root)
    return jsonify({
        "project_root": project_root,
        "output_dir": output_dir,
        "output_dir_exists": exists,
        "report_files": files[:5],
        "report_loaded": report is not None,
        "report_date": report.get("date") if report else None,
        "report_count": len(report.get("reports", [])) if report else 0,
    })


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="飞书事件订阅 Webhook 服务器")
    parser.add_argument("--port", type=int, default=8080, help="监听端口 (默认 8080)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    args = parser.parse_args()

    lark_cfg = get_lark_cfg()
    app_id = lark_cfg.get("app_id", "")
    if not app_id or "your-" in app_id:
        logger.warning("飞书 app_id 未配置，请先编辑 config.yaml → notify.lark")

    logger.info(f"飞书 Webhook 服务器启动: http://{args.host}:{args.port}")
    logger.info(f"事件回调 URL: http://<public-host>:{args.port}/feishu/event")
    logger.info("请在飞书开放平台 → 事件订阅中配置此 URL")
    logger.info("提示: 使用 ngrok 暴露本地端口: ngrok http {port}".format(port=args.port))

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
