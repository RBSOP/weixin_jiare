"""数据查询 - 订单及关联数据扁平化，供展示页使用"""
import json
from typing import Any

from db import get_conn, get_order_create_config, get_order_detail_data, init_db

# 电商加热效果：12 项自选指标 + 9 项数据明细
ECOMMERCE_ALLOWED = {
    f"ecommerce_{k}"
    for k in [
        "总消耗", "直接成交金额", "直接成交订单数", "直接成交ROI",
        "直播间曝光人数", "短视频曝光次数", "直播间观看人数", "直播间商品点击率",
        "直播间平均千次展示费用", "总成交ROI", "间接成交金额", "直播间观看人次",
        "加热主播与创建时间", "名称与编号", "加热开始时间", "加热结束时间", "实际加热时长",
        "直播间进入率（人数）", "GPM", "直播间转化率", "直播间转化成本",
    ]
}

# 十分钟级数据列（不含 sampleTime），按时间升序展示
TEN_MIN_COL_ORDER = [
    "时间",
    "直播间消耗",
    "直播间曝光人数",
    "直播间观看人数",
    "直播间点赞次数",
    "直播间评论次数",
    "直播间新增粉丝数",
]


def _fill_people_feature_block(row: dict, uf: dict, block_name: str, name_key: str, max_cols: int) -> None:
    """按占比降序填充人群分布块，格式：名称 数量 百分比%"""
    raw = uf.get(block_name) or []
    items = sorted(
        [x for x in raw if isinstance(x, dict)],
        key=lambda x: (x.get("percent") or 0),
        reverse=True,
    )
    for i in range(max_cols):
        item = items[i] if i < len(items) else {}
        label = item.get(name_key) or item.get("key") or ""
        value = item.get("value")
        percent = item.get("percent")
        val_str = str(value) if value is not None else ""
        pct_str = f"{percent:.2f}%" if percent is not None else ""
        parts = [p for p in [label, val_str, pct_str] if p]
        row[f"people_feature_{block_name}_{i + 1}"] = " ".join(parts) if parts else ""


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
            flat = _flatten(cfg, "create_config")
            for k, v in flat.items():
                row[k] = v
            # 合并 观众年龄_0~4 为单列 观众年龄，用顿号分隔
            age_parts = [row.pop(f"create_config_人群定向_观众年龄_{i}", None) for i in range(5)]
            age_parts = [a for a in age_parts if a]
            if age_parts:
                row["create_config_人群定向_观众年龄"] = "、".join(str(a) for a in age_parts)

        detail = get_order_detail_data(pid)
        if detail:
            for section, data in detail.items():
                if section == "十分钟级数据":
                    continue
                if isinstance(data, dict):
                    flat = _flatten(data, f"detail_{section}")
                    for k, v in flat.items():
                        row[k] = v
                    if section == "加热信息":
                        age_parts = [row.pop(f"detail_加热信息_人群定向_观众年龄_{i}", None) for i in range(5)]
                        age_parts = [a for a in age_parts if a]
                        if age_parts:
                            row["detail_加热信息_人群定向_观众年龄"] = "、".join(str(a) for a in age_parts)
            # 观众兴趣：优先使用配置页（弹窗采集的可读名称），与配置页保持一致
            cfg_crowd = cfg.get("人群定向") if cfg and isinstance(cfg, dict) else {}
            cfg_interest = cfg_crowd.get("观众兴趣") if isinstance(cfg_crowd, dict) else None
            if cfg_interest and str(cfg_interest).strip():
                row["detail_加热信息_人群定向_观众兴趣"] = cfg_interest

        conn2 = get_conn()
        cur2 = conn2.execute("SELECT merged_json FROM order_ecommerce_statistic WHERE promotion_id = ?", (pid,))
        erow = cur2.fetchone()
        if erow and erow[0]:
            eco = json.loads(erow[0])
            for k, v in _flatten(eco, "ecommerce").items():
                if k in ECOMMERCE_ALLOWED:
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
                _fill_people_feature_block(row, uf, "观众商品偏好", "key", 8)
                _fill_people_feature_block(row, uf, "八类人群占比", "key", 8)
                _fill_people_feature_block(row, uf, "性别分布", "key", 2)
                _fill_people_feature_block(row, uf, "年龄分布", "key", 8)
                _fill_people_feature_block(row, uf, "地域分布", "name", 8)
        conn3.close()

        timeline = []
        if detail and detail.get("十分钟级数据"):
            raw = detail["十分钟级数据"]
            if isinstance(raw, list):
                timeline = sorted(
                    [t for t in raw if isinstance(t, dict)],
                    key=lambda x: (x.get("sampleTime") or 0, x.get("时间") or ""),
                )
        for t in timeline if timeline else [{}]:
            out = dict(row)
            for col in TEN_MIN_COL_ORDER:
                out[f"detail_十分钟级数据_{col}"] = t.get(col)
            result.append(out)

    return result, total
