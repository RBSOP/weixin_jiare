"""Web 服务 - 数据展示页"""
import io
import json
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

# 关闭 Werkzeug 的 HTTP 请求日志，仅保留核心业务日志
logging.getLogger("werkzeug").disabled = True

from config import LOG_FILE, UI_CONFIG_FILE
from db import delete_account, list_accounts
from db_query import query_orders_with_relations
from pending_action import write as write_pending_action

app = Flask(__name__, template_folder="templates")
app.config["JSON_AS_ASCII"] = False

DEFAULT_UI_CONFIG = {"mode": "prod", "resolution": "1280x800"}


@app.route("/")
def index():
    """数据展示页"""
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    """分页查询数据"""
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))
    cost_min = request.args.get("cost_min", type=float)
    promotion_id = request.args.get("promotion_id", "").strip()
    nick_name = request.args.get("nick_name", "").strip()
    order_name = request.args.get("order_name", "").strip()
    sort_by = request.args.get("sort_by", "create_time")
    sort_order = request.args.get("sort_order", "desc")

    rows, total = query_orders_with_relations(
        cost_min=cost_min,
        promotion_id=promotion_id or None,
        nick_name=nick_name or None,
        order_name=order_name or None,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return jsonify({"rows": rows, "total": total, "page": page, "page_size": page_size})


@app.route("/api/export")
def api_export():
    """导出 xlsx（按当前筛选条件）"""
    cost_min = request.args.get("cost_min", type=float)
    promotion_id = request.args.get("promotion_id", "").strip()
    nick_name = request.args.get("nick_name", "").strip()
    order_name = request.args.get("order_name", "").strip()
    sort_by = request.args.get("sort_by", "create_time")
    sort_order = request.args.get("sort_order", "desc")

    rows, _ = query_orders_with_relations(
        cost_min=cost_min,
        promotion_id=promotion_id or None,
        nick_name=nick_name or None,
        order_name=order_name or None,
        page=1,
        page_size=10000,
        sort_by=sort_by.replace("orders_", ""),
        sort_order=sort_order,
    )
    if not rows:
        return jsonify({"error": "无数据可导出"}), 400

    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify({"error": "未安装 openpyxl"}), 500

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "采集数据"

    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    cols = sorted(all_keys)
    for c, k in enumerate(cols, 1):
        ws.cell(1, c, k)
    for ri, r in enumerate(rows, 2):
        for ci, k in enumerate(cols, 1):
            v = r.get(k)
            if isinstance(v, (dict, list)):
                v = str(v)
            ws.cell(ri, ci, v)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fn = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fn, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/accounts")
def api_accounts():
    """获取账号列表"""
    return jsonify(list_accounts())


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def api_delete_account(account_id: str):
    """删除账号"""
    if delete_account(account_id):
        return jsonify({"ok": True})
    return jsonify({"error": "账号不存在"}), 404


@app.route("/api/request_add_account", methods=["POST"])
def api_request_add_account():
    """请求添加账号（写入 pending action，由 main 轮询执行）"""
    write_pending_action("add_account")
    return jsonify({"ok": True})


@app.route("/api/request_collect", methods=["POST"])
def api_request_collect():
    """请求采集（写入 pending action）"""
    data = request.get_json() or {}
    write_pending_action(
        "collect",
        account_id=data.get("account_id", ""),
        start_time=data.get("start_time") or "",
        end_time=data.get("end_time") or "",
    )
    return jsonify({"ok": True})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """获取/保存 UI 配置（模式、分辨率）"""
    path = Path(UI_CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if request.method == "POST":
        data = request.get_json() or {}
        cfg = {**DEFAULT_UI_CONFIG, **data}
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        return jsonify(cfg)
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        return jsonify({**DEFAULT_UI_CONFIG, **cfg})
    return jsonify(DEFAULT_UI_CONFIG)


@app.route("/api/logs")
def api_logs():
    """获取最近日志（开发模式用）"""
    lines = int(request.args.get("lines", 100))
    if not Path(LOG_FILE).exists():
        return jsonify({"lines": []})
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return jsonify({"lines": all_lines[-lines:]})


def run_app(host: str = "0.0.0.0", port: int = 5000):
    Path("templates").mkdir(exist_ok=True)
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_app()
