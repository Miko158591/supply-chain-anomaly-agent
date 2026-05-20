# -*- coding: utf-8 -*-
"""
会话状态存储 — 记住用户上次查看的异常，支持"详情"上下文。

用 SQLite 存储，30 分钟过期。同一 chat_id + user_id 共享一个会话。
"""

import os
import sqlite3
import time
from typing import Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            chat_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            report_date TEXT NOT NULL,
            active_anomaly_index INTEGER,
            report_json TEXT,
            updated_at REAL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    conn.commit()
    return conn


def _now() -> float:
    return time.time()


def save_session(chat_id: str, user_id: str, report_date: str,
                 anomaly_index: int, report_json: str) -> None:
    """保存或更新会话——用户查看了哪个异常。"""
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO sessions (chat_id, user_id, report_date,
            active_anomaly_index, report_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (chat_id, user_id, report_date, anomaly_index, report_json, _now()))
    conn.commit()
    conn.close()


def get_session(chat_id: str, user_id: str, max_age_seconds: int = 1800):
    """获取会话。过期返回 None。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM sessions WHERE chat_id=? AND user_id=?",
        (chat_id, user_id)
    ).fetchone()
    conn.close()

    if row is None:
        return None
    if _now() - row[5] > max_age_seconds:
        _delete_session(chat_id, user_id)
        return None

    return {
        "chat_id": row[0],
        "user_id": row[1],
        "report_date": row[2],
        "active_anomaly_index": row[3],
        "report_json": row[4],
        "updated_at": row[5],
    }


def _delete_session(chat_id: str, user_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit()
    conn.close()


def handle_message(chat_id: str, user_id: str, text: str,
                   reports: List[Dict], stats: Dict,
                   report_date: str) -> Optional[str]:
    """处理用户消息，返回应该推送的文本。

    支持的命令:
      - 数字 (1-20): 查看对应编号的异常摘要（Layer 2）
      - "详情": 查看当前会话中活跃异常的完整报告（Layer 3）
      - "全部": 列出所有异常（Layer 2 的清单版本）
      - "导出": 提示 Excel 导出命令
    """
    from message_formatter import (
        format_anomaly_summary,
        format_anomaly_detail,
    )

    text = text.strip()

    # 数字 → Layer 2
    if text.isdigit():
        idx = int(text) - 1
        llm_reports = [r for r in reports
                       if "error" not in r and r.get("_meta", {}).get("data_sufficient") is not False]
        if idx < 0 or idx >= len(llm_reports):
            return f"编号 {text} 无效，当前共 {len(llm_reports)} 个已归因异常（编号 1-{len(llm_reports)}）"

        report = llm_reports[idx]
        import json
        save_session(chat_id, user_id, report_date, idx,
                     json.dumps(report, ensure_ascii=False, default=str))
        return format_anomaly_summary(report, idx + 1)

    # "详情" → Layer 3
    if text == "详情":
        session = get_session(chat_id, user_id)
        if session is None:
            return "没有活跃的会话。请先回复一个编号（如\"1\"）查看某条异常，再回复\"详情\"。"
        import json
        report = json.loads(session["report_json"])
        return format_anomaly_detail(report, session["active_anomaly_index"] + 1)

    return None  # 无法识别的命令
