#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书消息轮询 — 主动拉取群聊中 @机器人 的消息并回复。

用法:
  python feishu_poll.py                    # 轮询间隔默认 10 秒
  python feishu_poll.py --interval 5       # 5 秒轮询一次
  python feishu_poll.py --once             # 只拉一次，不循环

优势:
  - 不需要公网 URL / 隧道 / 事件订阅
  - 复用现有 app_id + app_secret + chat_id
  - 断线自动重连

前置:
  - 飞书应用已开通 im:message.group_msg 权限
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date
from typing import Any, Dict, List, Optional

import requests
import yaml

# ---- 路径 ----
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
sys.path.insert(0, os.path.dirname(os.path.dirname(SKILL_DIR)))

from session_store import handle_message as handle_reply

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("feishu-poll")


# ═══════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.dirname(SKILL_DIR)),
                               "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_lark_cfg():
    return load_config().get("notify", {}).get("lark", {})


# ═══════════════════════════════════════════
# 飞书 API
# ═══════════════════════════════════════════

def get_tenant_token(app_id: str, app_secret: str) -> Optional[str]:
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        data = resp.json()
        return data["tenant_access_token"] if data.get("code") == 0 else None
    except Exception as e:
        logger.error(f"获取 token 失败: {e}")
        return None


def fetch_messages(token: str, chat_id: str,
                   page_token: Optional[str] = None) -> Optional[dict]:
    """拉取群聊消息列表（最新在前）。"""
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "page_size": 10,
        "sort_type": "ByCreateTimeDesc",
    }
    if page_token:
        params["page_token"] = page_token
    try:
        resp = requests.get(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            params=params, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {})
        else:
            logger.warning(f"拉取消息失败: {data}")
            return None
    except Exception as e:
        logger.error(f"拉取消息请求失败: {e}")
        return None


def send_text(token: str, chat_id: str, text: str) -> bool:
    """向群聊发送文本消息。"""
    content = json.dumps({"text": text})
    payload = {"receive_id": chat_id, "msg_type": "text", "content": content}
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=payload, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            logger.info("消息发送成功")
            return True
        else:
            logger.warning(f"消息发送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"消息发送异常: {e}")
        return False


# ═══════════════════════════════════════════
# 消息解析
# ═══════════════════════════════════════════

def _extract_text(content_str: str) -> str:
    try:
        obj = json.loads(content_str)
        if isinstance(obj, dict) and "text" in obj:
            return obj["text"]
    except (json.JSONDecodeError, TypeError):
        pass
    return content_str


def _strip_mentions(text: str) -> str:
    return re.sub(r"@\S+\s*", "", text).strip()


def extract_command(raw_content: str) -> str:
    return _strip_mentions(_extract_text(raw_content))


# ═══════════════════════════════════════════
# 日报加载
# ═══════════════════════════════════════════

def load_latest_report(project_root: str) -> Optional[Dict]:
    output_dir = os.path.join(project_root, "data", "output")
    if not os.path.isdir(output_dir):
        return None
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
# 轮询主逻辑
# ═══════════════════════════════════════════

def poll_once(token: str, chat_id: str, bot_app_id: str,
              last_msg_id: Optional[str],
              project_root: str) -> Optional[str]:
    """拉取最新消息，处理第一条 @机器人 的新命令。返回新的 last_msg_id。"""
    data = fetch_messages(token, chat_id)
    if not data:
        return last_msg_id

    items = data.get("items", [])
    if not items:
        return last_msg_id

    new_last = items[0].get("message_id", last_msg_id)

    # 找第一条未处理且 @机器人 的用户消息
    for msg in items:
        msg_id = msg.get("message_id", "")
        if msg_id == last_msg_id:
            break  # 已处理到此，停止

        sender = msg.get("sender", {})
        # 只处理人类用户消息，忽略机器人自己的消息
        if sender.get("sender_type") != "user":
            continue

        # 群聊中只处理包含 @提及 的消息（用户 @机器人）
        mentions = msg.get("mentions", [])
        if mentions:
            # 检查是否 @了我们的机器人
            is_mentioned = any(
                m.get("id") == bot_app_id or m.get("id_type") == "app_id"
                for m in mentions
            )
            if not is_mentioned:
                continue

        raw_content = msg.get("body", {}).get("content", "")
        command = extract_command(raw_content)

        if not command:
            continue

        logger.info(f"收到命令: '{command}' from {sender.get('id')}")

        # 加载日报
        report_data = load_latest_report(project_root)
        if report_data is None:
            send_text(token, chat_id, "暂无日报数据。请先生成日报。")
            return msg_id  # 标记已处理，避免重复回复

        reports = report_data.get("reports", [])
        stats = report_data.get("stats", {})
        report_date_str = report_data.get("date", date.today().isoformat())

        # 注意：handle_reply 签名中的 chat_id/user_id 用于 session 存储
        # 这里用 sender_id 作为 user_id 来区分不同用户
        reply_text = handle_reply(
            chat_id=chat_id,
            user_id=sender.get("id", "unknown"),
            text=command,
            reports=reports,
            stats=stats,
            report_date=report_date_str,
        )

        if reply_text is None:
            logger.info(f"未识别命令: '{command}'")
            return msg_id

        send_text(token, chat_id, reply_text)
        return msg_id  # 只处理第一条命令

    return new_last


def main():
    parser = argparse.ArgumentParser(description="飞书消息轮询")
    parser.add_argument("--interval", type=int, default=10,
                        help="轮询间隔秒数 (默认 10)")
    parser.add_argument("--once", action="store_true",
                        help="只拉一次，不循环")
    args = parser.parse_args()

    lark = get_lark_cfg()
    app_id = lark.get("app_id", "")
    app_secret = lark.get("app_secret", "")
    chat_id = lark.get("chat_id", "")

    if not app_id or "your-" in app_id:
        logger.error("飞书 app_id 未配置，请检查 config.yaml")
        sys.exit(1)

    project_root = os.path.dirname(os.path.dirname(SKILL_DIR))
    logger.info(f"项目根目录: {project_root}")
    logger.info(f"目标群聊: {chat_id}")

    token = get_tenant_token(app_id, app_secret)
    if not token:
        logger.error("获取 tenant_access_token 失败")
        sys.exit(1)

    logger.info("飞书消息轮询启动")

    # 获取当前最新消息 ID 作为起始标记
    data = fetch_messages(token, chat_id)
    last_msg_id = data.get("items", [{}])[0].get("message_id") if data else None
    logger.info(f"起始消息: {last_msg_id}")

    if args.once:
        last_msg_id = poll_once(token, chat_id, app_id, last_msg_id,
                                project_root)
        return

    # 主循环
    fail_count = 0
    while True:
        try:
            if fail_count > 0 and fail_count % 6 == 0:
                # 每 6 次失败刷新 token
                token = get_tenant_token(app_id, app_secret)
                if token:
                    fail_count = 0
                    logger.info("token 已刷新")

            new_last = poll_once(token, chat_id, app_id, last_msg_id,
                                 project_root)
            if new_last and new_last != last_msg_id:
                last_msg_id = new_last
                fail_count = 0
            else:
                fail_count = 0

        except KeyboardInterrupt:
            logger.info("收到退出信号")
            break
        except Exception as e:
            fail_count += 1
            logger.error(f"轮询异常 (连续 {fail_count} 次): {e}")
            time.sleep(min(fail_count * 2, 30))

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
