"""
粉笔 API 客户端
负责登录认证、获取练习历史、题目元数据等

API 结构（通过逆向粉笔网页版 JS 确认）:
  1. category-exercises (无 categoryId) → 列出全部练习，cursor 分页
     - status=1 表示已完成，status=0 表示未完成
  2. exercises/{id} → 练习详情
     - 已完成练习的 userAnswers 包含用户作答
     - sheet.chapters 包含模块信息（名称 + 题目数量）
     - sheet.questionIds 包含所有题目 ID（顺序与 chapters 对应）
  3. questions?ids=... → 题目元数据
     - correctAnswer.choice 为正确答案 ("1"/"2"/"3"/"4" 对应 ABCD)
     - difficulty 为难度值
     - type 为题型
"""
import base64
import time
import logging
import re
import requests
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

logger = logging.getLogger(__name__)

# 粉笔 RSA 公钥（Base64 编码的模数）
FENBI_PUBLIC_KEY = "ANKi9PWuvDOsagwIVvrPx77mXNV0APmjySsYjB1/GtUTY6cyKNRl2RCTt608m9nYk5VeCG2EAZRQmQNQTyfZkw0Uo+MytAkjj17BXOpY4o6+BToi7rRKfTGl6J60/XBZcGSzN1XVZ80ElSjaGE8Ocg8wbPN18tbmsy761zN5SuIl"
FENBI_RSA_EXPONENT = 65537

BASE_PARAMS = {
    "app": "web",
    "kav": "100",
    "av": "100",
    "hav": "100",
    "version": "3.0.0.0",
}

LOGIN_URL = "https://login.fenbi.com/api/users/loginV2"
TIKU_BASE = "https://tiku.fenbi.com/api"

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# choice 数字到字母的映射
CHOICE_MAP = {"1": "A", "2": "B", "3": "C", "4": "D", "5": "E", "6": "F"}


def encrypt_password(password: str) -> str:
    """RSA + PKCS#1 v1.5 加密密码"""
    modulus_bytes = base64.b64decode(FENBI_PUBLIC_KEY)
    n = int.from_bytes(modulus_bytes, byteorder="big")
    key = RSA.construct((n, FENBI_RSA_EXPONENT))
    cipher = PKCS1_v1_5.new(key)
    encrypted = cipher.encrypt(password.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


class FenbiClient:
    """粉笔 API 客户端"""

    SUBJECT_XINGCE = "xingce"
    SUBJECT_SHENLUN = "shenlun"

    def __init__(self, phone: str, password: str, request_interval: float = 0.3):
        self.phone = phone
        self.password = password
        self.request_interval = request_interval
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._logged_in = False

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
            return True
        else:
            logger.error("粉笔登录失败: %s", result.get("msg", "未知错误"))
            return False

    def _ensure_login(self):
        if not self._logged_in:
            if not self.login():
                raise RuntimeError("粉笔登录失败")

    def _get(self, path: str, params: dict = None) -> dict:
        """GET 请求"""
        self._ensure_login()
        url = f"{TIKU_BASE}/{path}"
        full_params = {**BASE_PARAMS, **(params or {})}
        resp = self.session.get(url, params=full_params, timeout=15)
        self._wait()
        if resp.status_code == 200:
            return resp.json()
        logger.warning("GET %s 失败: HTTP %d", path, resp.status_code)
        return {}

    def get_all_exercises(self, subject: str = SUBJECT_XINGCE) -> list:
        """
        获取用户在指定题库下的全部已完成练习
        使用 category-exercises 端点（无 categoryId），cursor 分页
        只返回 status=1（已完成）的练习
        """
        logger.info("=== 开始采集 %s 数据 ===", subject)

        all_completed = []
        cursor = 0

        while True:
            data = self._get(f"{subject}/category-exercises", {
                "cursor": str(cursor),
                "count": "20",
            })

            if not data:
                break

            datas = data.get("datas", [])
            if not datas:
                break

            for ex in datas:
                if ex.get("status") == 1:
                    all_completed.append(ex)

            new_cursor = data.get("cursor", 0)
            if new_cursor == 0 or new_cursor == cursor:
                break
            cursor = new_cursor

        logger.info("  %s 已完成练习 %d 套", subject, len(all_completed))
        return all_completed

    def get_exercise_detail(self, exercise_id: int, subject: str = SUBJECT_XINGCE) -> dict:
        """获取练习详情（用户作答、模块信息、题目列表）"""
        return self._get(f"{subject}/exercises/{exercise_id}")

    def get_questions(self, question_ids: list, subject: str = SUBJECT_XINGCE) -> list:
        """批量获取题目元数据（正确答案、难度等）"""
        if not question_ids:
            return []

        results = []
        # 分批获取，每批最多 50 个
        for i in range(0, len(question_ids), 50):
            batch = question_ids[i:i + 50]
            ids_str = ",".join(str(qid) for qid in batch)
            data = self._get(f"{subject}/questions", {"ids": ids_str})

            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict) and "list" in data:
                results.extend(data["list"])

        return results

    def get_all_practice_records(self, subjects: list = None) -> list:
        """
        获取所有题库的全部练习记录
        返回标准化的练习记录列表（每道题一条记录）
        """
        if subjects is None:
            subjects = [self.SUBJECT_XINGCE, self.SUBJECT_SHENLUN]

        all_records = []

        for subject in subjects:
            exercises = self.get_all_exercises(subject)

            for i, ex in enumerate(exercises):
                ex_id = ex.get("id")
                ex_name = ex.get("sheet", {}).get("name", "")
                logger.info("  (%d/%d) 获取练习详情: %s", i + 1, len(exercises), ex_name[:40])

                detail = self.get_exercise_detail(ex_id, subject)
                if not detail:
                    continue

                # 提取用户作答
                user_answers = detail.get("userAnswers", {})
                if not user_answers:
                    logger.info("    无用户作答，跳过")
                    continue

                # 构建模块映射（question_index -> module_name）
                module_map = self._build_module_map(detail)

                # 构建题目 ID 列表
                question_ids = detail.get("sheet", {}).get("questionIds", [])

                # 获取题目元数据（正确答案）
                answered_qids = [ans.get("questionId") for ans in user_answers.values() if ans.get("questionId")]
                questions = self.get_questions(answered_qids, subject)
                question_map = {q.get("id"): q for q in questions if q.get("id")}

                # 练习时间
                practice_time = detail.get("createdTime", 0)

                # 试卷信息
                sheet = detail.get("sheet", {})
                paper_name = sheet.get("name", "")
                paper_id = sheet.get("paperId", "")

                # 从试卷名提取年份和来源
                year = self._extract_year(paper_name)
                source = self._extract_source(paper_name)

                # 构建题目链接
                question_link = self._build_question_link(ex_id, subject)

                # 遍历用户作答，构建记录
                for q_index_str, ans_data in user_answers.items():
                    qid = ans_data.get("questionId")
                    q_index = ans_data.get("questionIndex", int(q_index_str) if q_index_str.isdigit() else 0)

                    user_choice_raw = ans_data.get("answer", {}).get("choice", "")
                    user_choice = CHOICE_MAP.get(str(user_choice_raw), str(user_choice_raw))

                    question_meta = question_map.get(qid, {})
                    correct_raw = question_meta.get("correctAnswer", {})
                    if isinstance(correct_raw, dict):
                        correct_choice_raw = correct_raw.get("choice", "")
                    else:
                        correct_choice_raw = str(correct_raw)
                    correct_choice = CHOICE_MAP.get(str(correct_choice_raw), str(correct_choice_raw))

                    is_correct = user_choice == correct_choice if user_choice and correct_choice else False

                    module = module_map.get(q_index, "")

                    # 根据来源判断考试类型
                    if "事业单位" in source:
                        exam_type = "事业单位"
                    elif "国考" in source:
                        exam_type = "国考"
                    elif "省考" in source:
                        exam_type = "省考"
                    elif "模考" in source:
                        exam_type = "模考"
                    else:
                        exam_type = "公务员"

                    record = {
                        "question_id": qid,
                        "exercise_id": ex_id,
                        "question_link": question_link,
                        "practice_name": paper_name,
                        "practice_time": practice_time,
                        "user_answer": user_choice,
                        "correct_answer": correct_choice,
                        "is_correct": is_correct,
                        "module": module,
                        "sub_module": "",
                        "question_type": self._map_question_type(question_meta.get("type", 0)),
                        "year": year,
                        "source": source,
                        "exam_type": exam_type,
                        "difficulty": question_meta.get("difficulty", ""),
                        "paper_name": paper_name,
                        "source_label": source,
                        "subject": subject,
                    }
                    all_records.append(record)

            logger.info("=== %s 采集完成: %d 条记录 ===", subject, len(all_records))

        return all_records

    def _build_module_map(self, exercise_detail: dict) -> dict:
        """
        构建 question_index -> module_name 的映射
        chapters 中的 questionCount 是连续的，按顺序分配
        """
        sheet = exercise_detail.get("sheet", {})
        chapters = sheet.get("chapters", [])

        module_map = {}
        index = 0
        for chapter in chapters:
            name = chapter.get("name", "")
            count = chapter.get("questionCount", 0)
            for i in range(count):
                module_map[index] = name
                index += 1

        return module_map

    def _build_question_link(self, exercise_id, subject: str) -> str:
        """构建粉笔网页版练习报告链接"""
        if not exercise_id:
            return ""
        return f"https://www.fenbi.com/spa/tiku/#/{subject}/report/{exercise_id}"

    def _extract_year(self, paper_name: str) -> str:
        """从试卷名提取年份（支持'2024年'、'2025上半年'、'2023下'等格式）"""
        # 先匹配带"年"的格式，如 "2024年国考"
        match = re.search(r'(20\d{2})年', paper_name)
        if match:
            return match.group(1)
        # 再匹配不带"年"但带上下半年标记的，如 "2025上半年"、"2023下"
        match = re.search(r'(20\d{2})(?:上半年|下半年|上|下)', paper_name)
        if match:
            return match.group(1)
        # 最后兜底：匹配 20xx 格式的4位数字
        match = re.search(r'(20\d{2})', paper_name)
        if match:
            return match.group(1)
        return ""

    def _extract_source(self, paper_name: str) -> str:
        """从试卷名提取来源分类"""
        if "国考" in paper_name or "国家公务员" in paper_name:
            return "国考"
        if "事业单位" in paper_name or "事业编" in paper_name:
            return "事业单位"
        if "模考" in paper_name:
            return "模考"
        if "省考" in paper_name or "省公务员" in paper_name or "省公考" in paper_name:
            # 提取省份名
            for prov in ["江苏", "浙江", "广东", "山东", "北京", "上海", "四川",
                         "河南", "湖北", "湖南", "安徽", "福建", "江西", "辽宁",
                         "吉林", "黑龙江", "河北", "山西", "陕西", "甘肃",
                         "青海", "海南", "云南", "贵州", "广西", "内蒙古",
                         "新疆", "西藏", "宁夏", "重庆", "天津"]:
                if prov in paper_name:
                    return f"{prov}省考"
            return "省考"
        return ""

    def _map_question_type(self, type_code: int) -> str:
        """映射题目类型代码到中文名称"""
        type_map = {
            1: "单选题",
            2: "多选题",
            3: "判断题",
            4: "填空题",
            5: "简答题",
            6: "论述题",
            7: "公文写作",
            8: "材料作文",
        }
        return type_map.get(type_code, f"题型{type_code}")
