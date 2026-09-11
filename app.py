# -*- coding: utf-8 -*-
"""饭团摊营业额统计小工具：Flask + SQLite + ECharts 折线图"""
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data.db")

app = Flask(__name__)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """首次运行时自动建表"""
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    date       TEXT    UNIQUE NOT NULL,   -- 日期，格式 YYYY-MM-DD
                    amount     REAL    NOT NULL,          -- 当天营业额（元）
                    created_at TEXT    DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
    finally:
        conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/records", methods=["GET"])
def list_records():
    """全部记录，按日期升序返回"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, date, amount FROM records ORDER BY date ASC"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"records": [dict(r) for r in rows]})


@app.route("/api/records", methods=["POST"])
def save_record():
    """保存一条营业额记录；同一天重复保存时直接覆盖更新"""
    data = request.get_json(silent=True) or {}
    date = str(data.get("date", "")).strip()
    amount = data.get("amount")

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "msg": "日期格式不正确"}), 400

    try:
        amount = round(float(amount), 2)
        if amount < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "金额必须是不小于 0 的数字"}), 400

    conn = get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO records (date, amount) VALUES (?, ?)",
                (date, amount),
            )
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/records/<int:record_id>", methods=["DELETE"])
def delete_record(record_id):
    """删除一条记录"""
    conn = get_conn()
    try:
        with conn:
            conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    finally:
        conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    url = f"http://{host}:{port}"
    # 启动后自动打开浏览器；服务器环境设置 NO_BROWSER=1 关闭
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False)
