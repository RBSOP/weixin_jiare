"""数据库模块 - SQLite 存储订单数据"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DB_PATH


def _migrate_add_account_id(conn: sqlite3.Connection) -> None:
    """为已有 orders 表添加 account_id 列（兼容旧库）"""
    cur = conn.execute("PRAGMA table_info(orders)")
    cols = [r[1] for r in cur.fetchall()]
    if "account_id" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN account_id TEXT")


def get_conn() -> sqlite3.Connection:
    """获取数据库连接"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """初始化数据库表"""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            account_name TEXT,
            video_account TEXT,
            cookie_path TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            promotion_id TEXT PRIMARY KEY,
            account_id TEXT,
            nick_name TEXT,
            create_time TEXT,
            cost INTEGER,
            cost_yuan REAL,
            status TEXT,
            target TEXT,
            order_name TEXT,
            budget INTEGER,
            bid_roi TEXT,
            duration TEXT,
            join_count INTEGER,
            exposure_count INTEGER,
            product_click_count INTEGER,
            created_at TEXT,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_cost ON orders(cost_yuan)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_create_time ON orders(create_time)")
    _migrate_add_account_id(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_create_config (
            promotion_id TEXT PRIMARY KEY,
            config_json TEXT,
            created_at TEXT,
            FOREIGN KEY (promotion_id) REFERENCES orders(promotion_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_detail_data (
            promotion_id TEXT PRIMARY KEY,
            heating_info_json TEXT,
            consumption_progress_json TEXT,
            effect_summary_json TEXT,
            effect_timeline_json TEXT,
            created_at TEXT,
            FOREIGN KEY (promotion_id) REFERENCES orders(promotion_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_ecommerce_statistic (
            promotion_id TEXT PRIMARY KEY,
            merged_json TEXT,
            created_at TEXT,
            FOREIGN KEY (promotion_id) REFERENCES orders(promotion_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_people_statistic (
            promotion_id TEXT PRIMARY KEY,
            funnel_json TEXT,
            user_feature_json TEXT,
            created_at TEXT,
            FOREIGN KEY (promotion_id) REFERENCES orders(promotion_id)
        )
    """)
    conn.commit()
    conn.close()


def save_orders(orders: list[dict], account_id: str | None = None) -> int:
    """
    保存订单到数据库，已存在则更新
    :param account_id: 关联的账号 ID
    :return: 实际插入/更新的条数
    """
    init_db()
    conn = get_conn()
    count = 0
    for o in orders:
        row = _order_to_row(o, account_id)
        if row:
            conn.execute(
                """
                INSERT INTO orders (promotion_id, account_id, nick_name, create_time, cost, cost_yuan,
                    status, target, order_name, budget, bid_roi, duration, join_count,
                    exposure_count, product_click_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(promotion_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    nick_name=excluded.nick_name,
                    create_time=excluded.create_time,
                    cost=excluded.cost,
                    cost_yuan=excluded.cost_yuan,
                    status=excluded.status,
                    target=excluded.target,
                    order_name=excluded.order_name,
                    budget=excluded.budget,
                    bid_roi=excluded.bid_roi,
                    duration=excluded.duration,
                    join_count=excluded.join_count,
                    exposure_count=excluded.exposure_count,
                    product_click_count=excluded.product_click_count,
                    created_at=excluded.created_at
                """,
                row,
            )
            count += 1
    conn.commit()
    conn.close()
    return count


def _map_promotion_target(v) -> str:
    """promotionTarget: 8=直播间成交 10=观看量 11=成交ROI 12=互动量"""
    m = {8: "直播间成交", 10: "观看量", 11: "成交ROI", 12: "互动量"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _order_to_row(o: dict, account_id: str | None = None) -> tuple | None:
    """将订单 dict 转为数据库行（MCP 验证：searchLivePromotionOrderList 结构）"""
    pid = o.get("promotionId")
    if not pid:
        return None
    order_info = o.get("orderInfo") or {}
    acct = o.get("acctInfo") or order_info.get("acctInfo") or {}
    indicator = o.get("indicatorData") or {}
    data_info = indicator.get("dataInfo") or {}
    cost = int(data_info.get("cost") or 0)
    cost_yuan = cost / 10.0
    create_time = o.get("createTime") or ""
    status = o.get("status") or ""
    target = o.get("targetName") or o.get("target") or _map_promotion_target(o.get("promotionTarget") or order_info.get("promotionTarget"))
    order_name = o.get("promotionName") or o.get("orderName") or order_info.get("orderName") or order_info.get("promotionName") or ""
    budget = int(o.get("budget") or order_info.get("costQuota") or 0)
    roi = o.get("bidRoi") or o.get("targetRoi") or order_info.get("bidRoi") or order_info.get("targetRoi")
    if roi is None:
        r100 = (order_info.get("suggest") or {}).get("roiBidX100")
        roi = r100 / 100.0 if r100 is not None else ""
    bid_roi = str(roi) if roi != "" else ""
    duration = o.get("promotionDuration") or order_info.get("duration") or ""
    join_count = int(data_info.get("joinCount") or 0)
    exposure_count = int(data_info.get("exposureCount") or 0)
    product_click_count = int(data_info.get("productClickCount") or 0)
    return (
        pid,
        account_id or "",
        acct.get("nickName") or "",
        create_time,
        cost,
        cost_yuan,
        status,
        target,
        order_name,
        budget,
        bid_roi,
        duration,
        join_count,
        exposure_count,
        product_click_count,
        datetime.now().isoformat(),
    )


def query_orders(cost_min: float | None = None,
                 limit: int | None = None,
                 account_id: str | None = None) -> list[dict]:
    """
    查询订单列表
    :param cost_min: 仅返回消耗 > 此值的订单（元）
    :param limit: 返回条数限制
    :param account_id: 仅返回该账号的订单
    """
    conn = get_conn()
    sql = "SELECT * FROM orders"
    params = []
    conds = []
    if cost_min is not None:
        conds.append("cost_yuan > ?")
        params.append(cost_min)
    if account_id:
        conds.append("account_id = ?")
        params.append(account_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY create_time DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_existing_promotion_ids() -> set[str]:
    """获取数据库中已有的 promotion_id 集合，用于增量采集边界检测"""
    init_db()
    conn = get_conn()
    cur = conn.execute("SELECT promotion_id FROM orders")
    ids = {r[0] for r in cur.fetchall()}
    conn.close()
    return ids


def save_order_create_config(promotion_id: str, config: dict) -> None:
    """保存订单的 create-order 页面配置（结构化，按大标题分组）"""
    init_db()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO order_create_config (promotion_id, config_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(promotion_id) DO UPDATE SET
            config_json=excluded.config_json,
            created_at=excluded.created_at
        """,
        (promotion_id, json.dumps(config, ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def save_order_detail_data(
    promotion_id: str,
    heating_info: dict,
    consumption_progress: dict,
    effect_summary: dict,
    effect_timeline: list,
) -> None:
    """保存订单详情页数据（加热信息、消耗进度、加热效果汇总、十分钟级时间线）"""
    init_db()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO order_detail_data (
            promotion_id, heating_info_json, consumption_progress_json,
            effect_summary_json, effect_timeline_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(promotion_id) DO UPDATE SET
            heating_info_json=excluded.heating_info_json,
            consumption_progress_json=excluded.consumption_progress_json,
            effect_summary_json=excluded.effect_summary_json,
            effect_timeline_json=excluded.effect_timeline_json,
            created_at=excluded.created_at
        """,
        (
            promotion_id,
            json.dumps(heating_info, ensure_ascii=False),
            json.dumps(consumption_progress, ensure_ascii=False),
            json.dumps(effect_summary, ensure_ascii=False),
            json.dumps(effect_timeline, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_order_detail_data(promotion_id: str) -> dict | None:
    """获取订单详情页数据"""
    init_db()
    conn = get_conn()
    cur = conn.execute(
        "SELECT heating_info_json, consumption_progress_json, effect_summary_json, effect_timeline_json "
        "FROM order_detail_data WHERE promotion_id = ?",
        (promotion_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "加热信息": json.loads(row[0]) if row[0] else {},
        "消耗进度": json.loads(row[1]) if row[1] else {},
        "直播间加热效果": json.loads(row[2]) if row[2] else {},
        "十分钟级数据": json.loads(row[3]) if row[3] else [],
    }


def save_order_ecommerce_statistic(promotion_id: str, merged: dict) -> None:
    """保存电商加热效果统计（自选指标+数据明细去重合并）"""
    init_db()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO order_ecommerce_statistic (promotion_id, merged_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(promotion_id) DO UPDATE SET
            merged_json=excluded.merged_json,
            created_at=excluded.created_at
        """,
        (promotion_id, json.dumps(merged, ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def save_order_people_statistic(
    promotion_id: str,
    funnel: dict,
    user_feature: dict,
) -> None:
    """保存人群分析数据（人群漏斗 + 观众商品偏好/八类人群/性别/年龄/地域）"""
    init_db()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO order_people_statistic (promotion_id, funnel_json, user_feature_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(promotion_id) DO UPDATE SET
            funnel_json=excluded.funnel_json,
            user_feature_json=excluded.user_feature_json,
            created_at=excluded.created_at
        """,
        (
            promotion_id,
            json.dumps(funnel, ensure_ascii=False),
            json.dumps(user_feature, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_order_create_config(promotion_id: str) -> dict | None:
    """获取订单的 create-order 页面配置"""
    init_db()
    conn = get_conn()
    cur = conn.execute("SELECT config_json FROM order_create_config WHERE promotion_id = ?", (promotion_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return json.loads(row[0])
    return None


def count_orders(cost_min: float | None = None, account_id: str | None = None) -> int:
    """统计订单数量"""
    conn = get_conn()
    sql = "SELECT COUNT(*) FROM orders"
    params = []
    conds = []
    if cost_min is not None:
        conds.append("cost_yuan > ?")
        params.append(cost_min)
    if account_id:
        conds.append("account_id = ?")
        params.append(account_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    cur = conn.execute(sql, params)
    n = cur.fetchone()[0]
    conn.close()
    return n


def list_accounts() -> list[dict]:
    """列出所有账号"""
    init_db()
    conn = get_conn()
    cur = conn.execute("SELECT id, account_name, video_account, cookie_path, created_at FROM accounts ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_account(account_id: str, account_name: str, video_account: str, cookie_path: str) -> None:
    """保存或更新账号"""
    init_db()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO accounts (id, account_name, video_account, cookie_path, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            account_name=excluded.account_name,
            video_account=excluded.video_account,
            cookie_path=excluded.cookie_path
        """,
        (account_id, account_name, video_account, cookie_path, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_account(account_id: str) -> dict | None:
    """获取账号信息"""
    init_db()
    conn = get_conn()
    cur = conn.execute(
        "SELECT id, account_name, video_account, cookie_path, created_at FROM accounts WHERE id = ?",
        (account_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_account(account_id: str) -> bool:
    """删除账号，同时删除 cookie 文件，订单的 account_id 置空"""
    acc = get_account(account_id)
    if not acc:
        return False
    init_db()
    conn = get_conn()
    conn.execute("UPDATE orders SET account_id = '' WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    cookie_path = Path(acc.get("cookie_path") or "")
    if cookie_path.exists():
        cookie_path.unlink()
    return True
