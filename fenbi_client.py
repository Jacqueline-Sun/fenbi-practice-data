"""
粉笔 API 客户端 (v2)
使用 /combine/exercise/ 系列端点，获取三级模块、单题用时等完整数据

API 流程:
  1. POST /api/users/device/sid/create → 生成 DeviceSid (首次需要)
  2. GET /combine/exercise/getExerciseBriefHistory?categoryId=1&deviceId=... → 练习历史
  3. GET /combine/exercise/getSolution?key={exerciseKey}&deviceId=... → 用户作答
  4. GET /combine/exercise/getReport?key={exerciseKey}&deviceId=... → 报告(模块树)
  5. GET {staticUrl} → 静态解析(题目元数据: source, keypoints, correctAnswer)
"""
import base64
import time
import logging
import re
import uuid
import requests
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

logger = logging.getLogger(__name__)

FENBI_PUBLIC_KEY = "ANKi9PWuvDOsagwIVvrPx77mXNV0APmjySsYjB1/GtUTY6cyKNRl2RCTt608m9nYk5VeCG2EAZRQmQNQTyfZkw0Uo+MytAkjj17BXOpY4o6+BToi7rRKfTGl6J60/XBZcGSzN1XVZ80ElSjaGE8Ocg8wbPN18tbmsy761zN5SuIl"
FENBI_RSA_EXPONENT = 65537

LOGIN_URL = "https://login.fenbi.com/api/users/loginV2"
TIKU_BASE = "https://tiku.fenbi.com"

COMMON_PARAMS = {
    "app": "web",
    "kav": "128",
    "av": "128",
    "hav": "128",
    "version": "3.0.0.0",
    "gav": "2",
    "apcId": "0",
}

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.fenbi.com/spa/tiku",
}

EXERCISE_KEY_PATTERN = re.compile(r"^\d+_\d+_[A-Za-z0-9_-]+$")
YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")

PROVINCES = [
    "北京", "上海", "天津", "重庆",
    "江苏", "浙江", "广东", "山东", "四川", "河南", "湖北", "湖南",
    "安徽", "福建", "江西", "辽宁", "吉林", "黑龙江", "河北", "山西",
    "陕西", "甘肃", "青海", "海南", "云南", "贵州", "广西", "内蒙古",
    "新疆", "西藏", "宁夏",
]


def encrypt_password(password: str) -> str:
    modulus_bytes = base64.b64decode(FENBI_PUBLIC_KEY)
    n = int.from_bytes(modulus_bytes, byteorder="big")
    key = RSA.construct((n, FENBI_RSA_EXPONENT))
    cipher = PKCS1_v1_5.new(key)
    encrypted = cipher.encrypt(password.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def _walk(obj, visitor, path=None):
    if path is None:
        path = []
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk(item, visitor, path + [i])
    elif isinstance(obj, dict):
        visitor(obj, path)
        for k, v in obj.items():
            _walk(v, visitor, path + [k])


def _first_value(obj, keys):
    for k in keys:
        v = obj.get(k)
        if v is not None and v != "":
            return v
    return None


def _normalize_correctness(value) -> str:
    if value is True or value == 1 or value == "1":
        return "正确"
    if value is False or value == -1 or value == "-1":
        return "错误"
    if value == 10 or value == "10":
        return "未答"
    return ""


def _format_date(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        millis = value if value > 1e12 else value * 1000
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
        dt_bj = dt + timedelta(hours=8)
        return dt_bj.strftime("%Y-%m-%d")
    s = str(value)
    m = re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", s)
    if m:
        return m.group(0).replace(".", "-").replace("/", "-")
    return s


def _extract_names(value, names=None):
    if names is None:
        names = []
    if isinstance(value, list):
        for item in value:
            _extract_names(item, names)
    elif isinstance(value, dict):
        if isinstance(value.get("name"), str) and value["name"].strip():
            names.append(value["name"].strip())
        for k in ["keyPoint", "keypoint", "keyPoints", "keypoints", "children", "path"]:
            if k in value:
                _extract_names(value[k], names)
    elif isinstance(value, str) and value.strip():
        names.append(value.strip())
    return list(dict.fromkeys(names))


def _module_path(obj) -> list:
    for k in ["keyPoint", "keypoint", "keyPoints", "keypoints", "categoryPath"]:
        if k in obj and obj[k] is not None:
            names = _extract_names(obj[k])
            if names:
                return names
    return []


def _report_module_paths(payload) -> dict:
    paths = {}

    def build_tree(nodes, parents=None):
        if parents is None:
            parents = []
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                continue
            name = node["name"].strip()
            current = parents + [name]
            paths[name] = current
            if "children" in node:
                build_tree(node["children"], current)

    def visitor(obj, _):
        if isinstance(obj.get("details"), list):
            build_tree(obj["details"])

    _walk(payload, visitor)
    return paths


def _collect_by_question_id(payload) -> dict:
    result = {}

    def visitor(obj, _):
        looks_like_question = (
            "correctAnswer" in obj
            or "keyPoint" in obj
            or ("prefix" in obj and any(k in obj for k in ["answer", "status", "time"]))
        )
        if looks_like_question and "id" in obj:
            qid = str(obj["id"])
        else:
            qid = _first_value(obj, ["questionId", "question_id", "quizId", "quiz_id"])
            qid = str(qid) if qid else None
        if not qid:
            return
        existing = result.get(qid, {})
        existing.update(obj)
        result[qid] = existing

    _walk(payload, visitor)
    return result


def _extract_province(source: str) -> str:
    for prov in PROVINCES:
        if prov in source:
            return prov
    if "国家" in source or "国考" in source:
        return "全国"
    return ""


def _extract_source_category(source: str) -> str:
    if "事业单位" in source or "事业编" in source:
        return "事业编"
    if "公务员" in source or "国考" in source or "省考" in source:
        return "公务员"
    if "模考" in source:
        return "模考"
    return ""


class FenbiClient:
    """粉笔 API 客户端 v2"""

    def __init__(self, phone: str, password: str, request_interval: float = 0.25):
        self.phone = phone
        self.password = password
        self.request_interval = request_interval
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._logged_in = False
        self._device_id = ""

    def _wait(self):
        if self.request_interval > 0:
            time.sleep(self.request_interval)

    def login(self) -> bool:
        encrypted_pwd = encrypt_password(self.password)
        data = {
            "password": encrypted_pwd,
            "persistent": "true",
            "app": "web",
            "phone": self.phone,
        }
        params = {"kav": "12", "app": "web"}
        logger.info("正在登录粉笔 (手机号: %s)", self.phone)
        resp = self.session.post(LOGIN_URL, params=params, data=data, timeout=15)
        result = resp.json()
        if result.get("code") == 1:
            self._logged_in = True
            logger.info("粉笔登录成功")
            self._ensure_device_id()
            return True
        logger.error("粉笔登录失败: %s", result.get("msg", "未知错误"))
        return False

    def _ensure_device_id(self):
        """生成 DeviceSid (用于 /combine/exercise/ 端点)"""
        if self._device_id:
            return

        logger.info("正在生成 DeviceSid...")
        fp = {
            "canvas": "python_client_canvas",
            "webgl": "python_client_webgl",
            "screen": "1920x1080x24",
            "language": "zh-CN",
            "platform": "Win32",
            "cores": "8",
            "memory": "8",
            "touchPoints": "0",
        }
        body = {
            "pf": "web",
            "startupId": str(uuid.uuid4()),
            "extras": fp,
        }
        resp = self.session.post(
            f"{TIKU_BASE}/api/users/device/sid/create",
            json=body,
            timeout=15,
            headers={**API_HEADERS, "Content-Type": "application/json"},
        )
        data = resp.json()
        if data.get("code") == 1:
            self._device_id = data["data"]["deviceId"]
            logger.info("DeviceSid 生成成功: %s", self._device_id)
        else:
            logger.warning("DeviceSid 生成失败: %s", data.get("msg", ""))
            logger.warning("将尝试不带 deviceId 继续请求")

    def _ensure_login(self):
        if not self._logged_in:
            if not self.login():
                raise RuntimeError("粉笔登录失败")

    def _get_json(self, url: str, params: dict = None) -> dict:
        """GET 请求返回 JSON"""
        self._ensure_login()
        p = {**COMMON_PARAMS}
        if self._device_id:
            p["deviceId"] = self._device_id
        if params:
            p.update(params)

        resp = self.session.get(url, params=p, timeout=30, headers=API_HEADERS)
        self._wait()
        if resp.status_code != 200:
            logger.warning("GET 失败: HTTP %d %s", resp.status_code, url[:80])
            return {}
        data = resp.json()
        if isinstance(data.get("code"), (int, float)) and data["code"] != 1:
            logger.warning("API 错误 code=%s: %s", data["code"], data.get("msg", ""))
            return {}
        return data

    def get_exercise_history(self, routecs: str = "xingce") -> list:
        """获取练习历史列表 (仅 status=1 已完成)"""
        logger.info("=== 获取练习历史 (%s) ===", routecs)
        url = f"{TIKU_BASE}/combine/exercise/getExerciseBriefHistory"

        all_exercises = []
        cursor = ""
        seen_cursors = set()
        page = 0

        while page < 500:
            page += 1
            params = {"routecs": routecs, "categoryId": "4"}
            if cursor:
                params["cursor"] = cursor

            data = self._get_json(url, params)
            if not data:
                break

            d = data.get("data") or {}
            items = d.get("historyItems") or []
            if not items:
                break

            for item in items:
                key = item.get("exerciseKey", "")
                status = item.get("status", 0)
                if not key or not EXERCISE_KEY_PATTERN.match(key):
                    continue
                if status != 1:
                    continue
                date = _format_date(item.get("updatedTime"))
                all_exercises.append({
                    "exerciseKey": key,
                    "date": date,
                    "status": status,
                    "sheetName": item.get("sheetName", ""),
                })

            cursor = d.get("cursor", "")
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)

            logger.info("  页 %d: 累计 %d 套已完成", page, len(all_exercises))

        logger.info("  共 %d 套已完成练习", len(all_exercises))
        return all_exercises

    def get_solution(self, exercise_key: str, routecs: str = "xingce") -> dict:
        url = f"{TIKU_BASE}/combine/exercise/getSolution"
        return self._get_json(url, {"key": exercise_key, "routecs": routecs, "format": "html"})

    def get_report(self, exercise_key: str, routecs: str = "xingce") -> dict:
        url = f"{TIKU_BASE}/combine/exercise/getReport"
        return self._get_json(url, {"key": exercise_key, "routecs": routecs, "format": "html", "no_toast": ""})

    def get_static_solution(self, static_url: str) -> dict:
        """获取静态解析数据（不加 COMMON_PARAMS，避免破坏签名URL）"""
        if not static_url:
            return {}
        params = {"routecs": "xingce", "type": "1"}
        resp = self.session.get(static_url, params=params, timeout=30, headers=API_HEADERS)
        self._wait()
        if resp.status_code != 200:
            logger.warning("静态解析请求失败: HTTP %d", resp.status_code)
            return {}
        return resp.json()

    def normalize_exercise(self, exercise_key: str, exercise_date: str,
                           solution_payload: dict, report_payload: dict,
                           static_payload: dict, routecs: str = "xingce") -> list:
        """合并 solution + report + static 数据，输出标准化记录"""
        solution_items = _collect_by_question_id(solution_payload)
        report_items = _collect_by_question_id(report_payload)
        static_items = _collect_by_question_id(static_payload)
        report_paths = _report_module_paths(report_payload)

        all_qids = set(solution_items.keys()) | set(report_items.keys()) | set(static_items.keys())

        subject = "行测" if routecs == "xingce" else "申论" if routecs == "shenlun" else routecs
        solution_url = f"https://spa.fenbi.com/ti/exam/solution/{exercise_key}?routecs={routecs}"

        records = []
        for qid in all_qids:
            merged = {}
            merged.update(solution_items.get(qid, {}))
            merged.update(report_items.get(qid, {}))
            merged.update(static_items.get(qid, {}))

            modules = _module_path(merged)
            if modules:
                leaf = modules[-1]
                if leaf in report_paths:
                    modules = report_paths[leaf]

            source = str(_first_value(merged, ["source", "questionSource"]) or "")
            year_match = YEAR_PATTERN.search(source)
            year = year_match.group(0) if year_match else ""
            province = _extract_province(source)
            source_cat = _extract_source_category(source)
            correctness = _normalize_correctness(
                _first_value(merged, ["correct", "isCorrect", "right", "status"])
            )
            duration = _first_value(merged, ["duration", "durationSeconds", "answerTime", "time"])
            practice_date = exercise_date or _format_date(
                _first_value(merged, ["submitTime", "createdTime", "updatedTime"])
            )

            records.append({
                "练习ID": exercise_key,
                "题目ID": qid,
                "科目": subject,
                "一级模块": modules[0] if len(modules) > 0 else "",
                "二级模块": modules[1] if len(modules) > 1 else "",
                "三级模块": modules[2] if len(modules) > 2 else "",
                "题目来源": source,
                "题目年份": year,
                "题目省份": province,
                "题目来源分类": source_cat,
                "是否正确": correctness,
                "单题用时": duration if duration is not None else "",
                "练习日期": practice_date,
                "题目链接": solution_url,
            })

        return records

    def get_all_practice_records(self, routecs_list: list = None) -> list:
        """获取全部练习记录"""
        if routecs_list is None:
            routecs_list = ["xingce"]

        all_records = []

        for routecs in routecs_list:
            exercises = self.get_exercise_history(routecs)

            for i, ex in enumerate(exercises):
                key = ex["exerciseKey"]
                logger.info("  (%d/%d) %s", i + 1, len(exercises), ex.get("sheetName", "")[:40])

                solution_data = self.get_solution(key, routecs)
                if not solution_data:
                    continue

                report_data = self.get_report(key, routecs)

                static_url = ""
                su = solution_data.get("data", {}).get("staticUrl", {})
                if isinstance(su, dict):
                    urls = su.get("urls", [])
                    if urls:
                        static_url = urls[0]

                static_data = self.get_static_solution(static_url) if static_url else {}

                rows = self.normalize_exercise(
                    exercise_key=key,
                    exercise_date=ex["date"],
                    solution_payload=solution_data,
                    report_payload=report_data,
                    static_payload=static_data,
                    routecs=routecs,
                )
                all_records.extend(rows)

                if (i + 1) % 10 == 0:
                    logger.info("  已采集 %d 套，累计 %d 条", i + 1, len(all_records))

            logger.info("=== %s 完成: %d 条记录 ===", routecs, len(all_records))

        return all_records
