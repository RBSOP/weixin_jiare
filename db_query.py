"""数据查询 - 订单及关联数据扁平化，供展示页使用"""
import json
from typing import Any

from db import get_conn, get_order_create_config, get_order_detail_data, init_db


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """递归扁平化 dict/list，键为 prefix_key"""
    out = {}
    if obj is None:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}_{k}" if prefix else k
            if isinstance(v, (dict, list)) and not (isinstance(v, dict) and not v) and not (isinstance(v, list) and not v):
                sub = _flatten(v, key)
                out.update(sub)
            else:
                out[key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}_{i}" if prefix else str(i)
            if isinstance(v, (dict, list)):
                sub = _flatten(v, key)
                out.update(sub)
            else:
                out[key] = v
    return out


def query_orders_with_relations(
    cost_min: float | None = None,
    promotion_id: str | None = None,
    nick_name: str | None = None,
    order_name: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "create_time",
    sort_order: str = "desc",
) -> tuple[list[dict], int]:
    """
    查询订单及关联数据，扁平化后每订单一行
    :return: (rows, total_count)
    """
    init_db()
    conn = get_conn()

    where_parts = []
    params = []
    if cost_min is not None:
        where_parts.append("cost_yuan > ?")
        params.append(cost_min)
    if promotion_id:
        where_parts.append("promotion_id LIKE ?")
        params.append(f"%{promotion_id}%")
    if nick_name:
        where_parts.append("nick_name LIKE ?")
        params.append(f"%{nick_name}%")
    if order_name:
        where_parts.append("order_name LIKE ?")
        params.append(f"%{order_name}%")
    where_sql = " AND ".join(where_parts) if where_parts else "1=1"

    allowed_sort = {"promotion_id", "nick_name", "create_time", "cost_yuan", "status", "target", "order_name", "budget", "duration"}
    sort_col = sort_by.replace("orders_", "") if sort_by.replace("orders_", "") in allowed_sort else "create_time"
    order = "DESC" if sort_order.lower() == "desc" else "ASC"

    cur = conn.execute(f"SELECT COUNT(*) FROM orders WHERE {where_sql}", params)
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur = conn.execute(
        f"SELECT * FROM orders WHERE {where_sql} ORDER BY {sort_col} {order} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    result = []
    for r in rows:
        pid = r.get("promotion_id")
        row = {f"orders_{k}": v for k, v in r.items()}
        row["promotion_id"] = pid

        cfg = get_order_create_config(pid)
        if cfg:
            for k, v in _flatten(cfg, "create_config").items():
                row[k] = v

        detail = get_order_detail_data(pid)
        if detail:
            for section, data in detail.items():
                if isinstance(data, dict):
                    for k, v in _flatten(data, f"detail_{section}").items():
                        row[k] = v
                elif isinstance(data, list) and data:
                    for i, item in enumerate(data[:5]):
                        for k, v in _flatten(item, f"detail_{section}_{i}").items():
                            row[k] = v

        conn2 = get_conn()
        cur2 = conn2.execute("SELECT merged_json FROM order_ecommerce_statistic WHERE promotion_id = ?", (pid,))
        erow = cur2.fetchone()
        if erow and erow[0]:
            eco = json.loads(erow[0])
            for k, v in _flatten(eco, "ecommerce").items():
                row[k] = v
        conn2.close()

        conn3 = get_conn()
        cur3 = conn3.execute(
            "SELECT funnel_json, user_feature_json FROM order_people_statistic WHERE promotion_id = ?",
            (pid,),
        )
        prow = cur3.fetchone()
        if prow:
            if prow[0]:
                funnel = json.loads(prow[0])
                for k, v in _flatten(funnel, "people_funnel").items():
                    row[k] = v
            if prow[1]:
                uf = json.loads(prow[1])
                for k, v in _flatten(uf, "people_feature").items():
                    row[k] = v
        conn3.close()

        result.append(row)

    return result, total
