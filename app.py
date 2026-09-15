# -*- coding: utf-8 -*-
"""饭团摊营业额统计小工具：Flask + SQLite + ECharts 折线图 + DeepSeek AI 分析"""
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data.db")

# 从 .env 加载密钥（.env 已被 .gitignore 忽略，不会提交到仓库）
load_dotenv(os.path.join(BASE_DIR, ".env"))
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

app = Flask(__name__)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """首次运行自动建表；老库自动迁移，支持休息日记录（金额可为空）"""
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    date       TEXT    UNIQUE NOT NULL,   -- 日期，格式 YYYY-MM-DD
                    amount     REAL,                       -- 当天营业额（元）；休息日固定为 0
                    is_rest    INTEGER NOT NULL DEFAULT 0, -- 1=休息日（未出摊）
                    created_at TEXT    DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # 兼容历史数据：早期休息日金额可能为空，统一记为 0
            conn.execute("UPDATE records SET amount = 0 WHERE is_rest = 1 AND amount IS NULL")
        # 老库迁移：没有 is_rest 列时重建表（金额列需允许为空）
        cols = [c[1] for c in conn.execute("PRAGMA table_info(records)")]
        if "is_rest" not in cols:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE records_migrate (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        date       TEXT    UNIQUE NOT NULL,
                        amount     REAL,
                        is_rest    INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT    DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO records_migrate (date, amount, is_rest, created_at) "
                    "SELECT date, amount, 0, created_at FROM records"
                )
                conn.execute("DROP TABLE records")
                conn.execute("ALTER TABLE records_migrate RENAME TO records")
    finally:
        conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.after_request
def no_cache_api(resp):
    """API 响应禁用浏览器缓存，保证每次拿到最新数据"""
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/records", methods=["GET"])
def list_records():
    """全部记录，按日期升序返回"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, date, amount, is_rest FROM records ORDER BY date ASC"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"records": [dict(r) for r in rows]})


@app.route("/api/records", methods=["POST"])
def save_record():
    """保存一条记录；同一天重复保存时直接覆盖更新。休息日不记金额"""
    data = request.get_json(silent=True) or {}
    date = str(data.get("date", "")).strip()
    is_rest = 1 if data.get("is_rest") else 0

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "msg": "日期格式不正确"}), 400

    if is_rest:
        amount = 0  # 休息日按 0 元记录，折线图保持连贯
    else:
        amount = data.get("amount")
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
                "INSERT OR REPLACE INTO records (date, amount, is_rest) VALUES (?, ?, ?)",
                (date, amount, is_rest),
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


def weekday_cn(date_str):
    """日期字符串转中文星期，0=周一"""
    return WEEK_CN[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


@app.route("/api/ai-analysis", methods=["POST"])
def ai_analysis():
    """调用 DeepSeek 分析营业额与日期、星期之间的关系"""
    if not DEEPSEEK_API_KEY:
        return jsonify({"ok": False, "msg": "未配置 DEEPSEEK_API_KEY，请在 .env 文件中填写"}), 500

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT date, amount, is_rest FROM records ORDER BY date ASC"
        ).fetchall()
    finally:
        conn.close()

    stall_days = [r for r in rows if not r["is_rest"]]
    if len(stall_days) < 3:
        return jsonify({"ok": False, "msg": "出摊数据还太少，至少录入 3 天营业额再试试"}), 400

    # 每日明细（休息日单独标注）
    daily_text = "\n".join(
        f"{r['date']} {weekday_cn(r['date'])} "
        + ("休息" if r["is_rest"] else f"{r['amount']:.1f} 元")
        for r in rows
    )

    # 按星期汇总（平均只算出摊日）
    summary_lines = []
    for name in WEEK_CN:
        amounts = [r["amount"] for r in stall_days if weekday_cn(r["date"]) == name]
        rest_cnt = sum(1 for r in rows if r["is_rest"] and weekday_cn(r["date"]) == name)
        if not amounts:
            summary_lines.append(f"{name}：未出摊" + (f"（休息 {rest_cnt} 天）" if rest_cnt else ""))
            continue
        avg = sum(amounts) / len(amounts)
        line = (
            f"{name}：出摊 {len(amounts)} 天，平均 {avg:.1f} 元，"
            f"最高 {max(amounts):.1f} 元，最低 {min(amounts):.1f} 元"
        )
        if rest_cnt:
            line += f"（另有休息 {rest_cnt} 天）"
        summary_lines.append(line)

    total = sum(r["amount"] for r in stall_days)

    system_prompt = (
        "你是一位接地气的数据分析助手，服务对象是一位每天出摊卖饭团的摊主。"
        "请根据提供的营业额数据分析："
        "1. 营业额整体走势；"
        "2. 星期几与营业额的关系（哪天最旺、哪天最淡；注意区分出摊日与休息日，平均只按出摊日算）；"
        "3. 日期上有没有周期规律（如周末效应），出勤安排是否合理；"
        "4. 给出 2-3 条实用建议（备货量、出摊安排等）。"
        "要求：中文回答，语气口语化、摊主能看懂；用 markdown 分点输出，"
        "金额保留 1 位小数；总长度 350 字以内；数据不足以得出结论时要明说，不要编造。"
    )
    user_prompt = (
        f"统计周期：{rows[0]['date']} 至 {rows[-1]['date']}，共 {len(rows)} 天记录"
        f"（出摊 {len(stall_days)} 天、休息 {len(rows) - len(stall_days)} 天），"
        f"累计营业额 {total:.1f} 元。\n\n"
        f"按星期汇总：\n" + "\n".join(summary_lines) + f"\n\n每日明细：\n{daily_text}"
    )

    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.5,
                "max_tokens": 1024,
            },
            timeout=90,
        )
    except requests.RequestException:
        return jsonify({"ok": False, "msg": "请求 DeepSeek 接口失败，请检查网络"}), 502

    if resp.status_code != 200:
        return jsonify(
            {"ok": False, "msg": f"DeepSeek 接口返回错误（{resp.status_code}），请检查 API Key 是否有效"}
        ), 502

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError):
        return jsonify({"ok": False, "msg": "解析 DeepSeek 返回内容失败"}), 502

    return jsonify({"ok": True, "content": content})


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    # 启动后自动打开浏览器；服务器环境设置 NO_BROWSER=1 关闭
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host=host, port=port, debug=False)
