"""
粉笔练习数据自动采集 → CSV 文件

从粉笔网获取个人练习历史，保存为 CSV 文件供 Power BI 读取
设计为在 GitHub Actions 中运行，无需本地电脑开机

架构:
    GitHub Actions (每日定时)
        ↓
    Python 脚本 (采集粉笔数据)
        ↓
    CSV 文件 (提交到仓库)
        ↓
    Power BI Web 连接器 (读取 raw URL)
        ↓
    Power BI 报表 (计划刷新，无需网关)

配置来源（优先级从高到低）:
    1. 环境变量（GitHub Secrets）— 云端运行时使用
    2. config.ini — 本地测试时使用
"""
import os
import sys
import logging
import configparser
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fenbi_client import FenbiClient
from data_extractor import transform_records, deduplicate_records, save_to_csv


def setup_logging(debug: bool = False):
    """配置日志输出到 stdout（GitHub Actions 可见）"""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def load_config() -> dict:
    """
    加载配置
    优先读取环境变量（GitHub Secrets），其次读取 config.ini（本地测试）
    """
    phone = os.environ.get("FENBI_PHONE", "")
    password = os.environ.get("FENBI_PASSWORD", "")

    request_interval = float(os.environ.get("REQUEST_INTERVAL", "0.5"))
    debug = os.environ.get("DEBUG", "true").lower() == "true"

    # 如果环境变量中没有配置，尝试从 config.ini 读取（本地测试模式）
    if not phone or not password:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, "config.ini")
        if os.path.exists(config_path):
            config = configparser.ConfigParser()
            config.read(config_path, encoding="utf-8")
            if not config.sections():
                config.read(config_path, encoding="gbk")

            phone = phone or config.get("FENBI", "phone", fallback="")
            password = password or config.get("FENBI", "password", fallback="")

            request_interval = config.getfloat("AUTOMATION", "request_interval", fallback=request_interval)
            debug = config.getboolean("AUTOMATION", "debug", fallback=debug)

    return {
        "phone": phone,
        "password": password,
        "request_interval": request_interval,
        "debug": debug,
    }


def main():
    """主入口函数"""
    setup_logging()
    logger = logging.getLogger("main")

    logger.info("=" * 60)
    logger.info("粉笔练习数据采集 → CSV - 开始")
    logger.info("时间: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    # 加载配置
    cfg = load_config()

    # 验证粉笔账号
    if not cfg["phone"] or not cfg["password"]:
        logger.error("未配置粉笔账号密码")
        logger.error("云端运行: 请在 GitHub Secrets 中设置 FENBI_PHONE 和 FENBI_PASSWORD")
        logger.error("本地运行: 请在 config.ini 中填写账号密码")
        return False

    logger.info("配置验证通过")
    logger.info("  粉笔账号: %s", cfg["phone"])

    try:
        # 1. 登录粉笔并采集数据
        client = FenbiClient(
            phone=cfg["phone"],
            password=cfg["password"],
            request_interval=cfg["request_interval"],
        )

        if not client.login():
            logger.error("粉笔登录失败，请检查账号密码是否正确")
            return False

        logger.info("开始采集全部练习数据...")
        raw_records = client.get_all_practice_records()

        if not raw_records:
            logger.warning("未获取到任何练习记录")
            logger.info("可能原因: 1)账号没有练习历史 2)API结构变化 3)网络问题")
            return False

        logger.info("共获取原始记录 %d 条", len(raw_records))

        # 2. 转换数据格式
        transformed = transform_records(raw_records)
        transformed = deduplicate_records(transformed)
        logger.info("数据转换 + 去重完成: %d 条", len(transformed))

        if not transformed:
            logger.warning("转换后无有效数据")
            return False

        # 3. 保存为 CSV 文件
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "practice_history.csv")
        save_to_csv(transformed, csv_path)

        logger.info("=" * 60)
        logger.info("粉笔练习数据采集 → CSV - 完成")
        logger.info("记录数: %d 条", len(transformed))
        logger.info("CSV 路径: %s", csv_path)
        logger.info("时间: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("=" * 60)
        return True

    except Exception as e:
        logger.error("运行过程中发生错误: %s", e, exc_info=True)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
