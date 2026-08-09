"""
数据提取、转换和 CSV 输出
14 列标准化格式，与参考 CSV 对齐
"""
import os
import logging

logger = logging.getLogger(__name__)

HEADERS = [
    "练习ID",
    "题目ID",
    "科目",
    "一级模块",
    "二级模块",
    "三级模块",
    "题目来源",
    "题目年份",
    "题目省份",
    "题目来源分类",
    "是否正确",
    "单题用时",
    "练习日期",
    "题目难度",
    "题目链接",
]


def deduplicate_records(records: list) -> list:
    """去重：同一练习ID+题目ID只保留一条"""
    seen = set()
    unique = []
    for r in records:
        key = (r.get("练习ID", ""), r.get("题目ID", ""))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def sort_records(records: list) -> list:
    """排序：练习日期 DESC, 练习ID ASC, 题目ID ASC"""
    return sorted(records, key=lambda r: (
        r.get("练习日期", ""),
        r.get("练习ID", ""),
        str(r.get("题目ID", "")),
    ), reverse=False)


def save_to_csv(records: list, csv_path: str):
    """保存为 CSV (UTF-8 BOM)"""
    import csv

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)

    logger.info("CSV 已保存: %s (%d 条记录)", csv_path, len(records))
