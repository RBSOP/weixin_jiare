"""查询订单 - 从数据库读取并支持导出 CSV"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import COST_MIN, DB_PATH
from db import count_orders, query_orders


def main():
    if not Path(DB_PATH).exists():
        print("数据库不存在，请先运行 main.py 采集数据")
        return
    n = count_orders(cost_min=COST_MIN)
    print(f"消耗>{COST_MIN}的订单: {n} 条")
    orders = query_orders(cost_min=COST_MIN)
    if not orders:
        return
    out = Path(__file__).parent / "output" / "orders.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=orders[0].keys())
        w.writeheader()
        w.writerows(orders)
    print(f"已导出: {out}")


if __name__ == "__main__":
    main()
