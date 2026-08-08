"""
粉笔 API 客户端
负责登录认证、获取练习历史、题目元数据等
"""
import base64
import time
import logging
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


def encrypt_password(password: str) -> str:
    """
    使用 RSA + PKCS#1 v1.5 填充加密密码
    与粉笔前端 JavaScript 的加密逻辑完全一致
    """
    modulus_bytes = base64.b64decode(FENBI_PUBLIC_KEY)
    n = int.from_bytes(modulus_bytes, byteorder="big")
    key = RSA.construct((n, FENBI_RSA_EXPONENT))
    cipher = PKCS1_v1_5.new(key)
    encrypted = cipher.encrypt(password.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


class FenbiClient:
    """粉笔 API 客户端"""

    # 支持的题库类型
    SUBJECT_XINGCE = "xingce"    # 行测
    SUBJECT_SHENLUN = "shenlun"  # 申论

    def __init__(self, phone: str, password: str, request_interval: float = 0.5):
        self.phone = phone
        self.password = password
        self.request_interval = request_interval
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._logged_in = False

    def _wait(self):
        """请求间隔，避免被限流"""
        if self.request_interval > 0:
            time.sleep(self.request_interval)

    def login(self) -> bool:
        """登录粉笔，获取会话 Cookie"""
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
                raise RuntimeError("粉笔登录失败，无法继续操作")

    def get_sub_labels(self, subject: str = SUBJECT_XINGCE) -> list:
        """
        获取题库分类列表（省份/考试类型）
        返回: [{"name": "国考", "labelMeta": {"id": 123, "paperCount": 50}}, ...]
        """
        self._ensure_login()
        url = f"{TIKU_BASE}/{subject}/subLabels"
        logger.info("获取 %s 题库分类...", subject)
        resp = self.session.get(url, params=BASE_PARAMS, timeout=15)
        self._wait()
        return resp.json()

    def get_papers(self, label_id: int, subject: str = SUBJECT_XINGCE,
                   page: int = 0, page_size: int = 200) -> dict:
        """
        获取某分类下的试卷列表
        返回: {"list": [{"id": 1, "name": "...", "exercise": {"id": 123}}, ...]}
        exercise 字段不为 null 表示用户已做过该试卷
        """
        self._ensure_login()
        url = f"{TIKU_BASE}/{subject}/papers/"
        params = {
            **BASE_PARAMS,
            "toPage": str(page),
            "pageSize": str(page_size),
            "labelId": str(label_id),
        }
        resp = self.session.get(url, params=params, timeout=15)
        self._wait()
        return resp.json()

    def get_all_papers(self, subject: str = SUBJECT_XINGCE) -> list:
        """
        获取所有分类下的全部试卷
        返回带有 exercise 信息（已做/未做）的试卷列表
        """
        all_papers = []
        sub_labels = self.get_sub_labels(subject)

        for label in sub_labels:
            name = label.get("name", "")
            label_meta = label.get("labelMeta", {})
            label_id = label_meta.get("id")
            paper_count = label_meta.get("paperCount", 0)

            if not label_id or paper_count == 0:
                continue

            logger.info("  获取 [%s] 试卷列表 (%d 套)...", name, paper_count)
            data = self.get_papers(label_id, subject, page_size=paper_count)
            papers = data.get("list", [])

            for paper in papers:
                paper["_source_label"] = name
                paper["_subject"] = subject
                all_papers.append(paper)

        logger.info("  %s 共获取 %d 套试卷", subject, len(all_papers))
        return all_papers

    def get_exercise_detail(self, exercise_id: int, subject: str = SUBJECT_XINGCE) -> dict:
        """
        获取单次练习的详情（用户作答记录）
        包含: 题目ID列表、用户答案、练习时间等
        """
        self._ensure_login()
        url = f"{TIKU_BASE}/{subject}/exercises/{exercise_id}"
        params = {"app": "web", "kav": "12", "version": "3.0.0.0"}
        resp = self.session.get(url, params=params, timeout=15)
        self._wait()
        if resp.status_code == 200:
            return resp.json()
        logger.warning("获取练习详情失败 (exercise_id=%s): HTTP %d", exercise_id, resp.status_code)
        return {}

    def get_questions(self, question_ids: list, subject: str = SUBJECT_XINGCE) -> list:
        """
        批量获取题目数据（正确答案、元数据等）
        question_ids: 题目ID列表
        返回题目详情列表
        """
        if not question_ids:
            return []

        self._ensure_login()
        url = f"{TIKU_BASE}/{subject}/questions"
        # 粉笔 API 接受逗号分隔的 ID 列表
        ids_str = ",".join(str(qid) for qid in question_ids)
        params = {**BASE_PARAMS, "ids": ids_str}

        resp = self.session.get(url, params=params, timeout=30)
        self._wait()

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "list" in data:
                return data["list"]
            return [data] if data else []
        logger.warning("获取题目数据失败: HTTP %d", resp.status_code)
        return []

    def get_practiced_exercises(self, subject: str = SUBJECT_XINGCE) -> list:
        """
        获取用户已做过的所有练习（exercise 不为 null 的试卷）
        返回: [{"paper_id", "paper_name", "source_label", "subject", "exercise_id"}, ...]
        """
        all_papers = self.get_all_papers(subject)
        practiced = []

        for paper in all_papers:
            exercise = paper.get("exercise")
            if exercise and exercise.get("id"):
                practiced.append({
                    "paper_id": paper.get("id"),
                    "paper_name": paper.get("name", ""),
                    "source_label": paper.get("_source_label", ""),
                    "subject": paper.get("_subject", subject),
                    "exercise_id": exercise.get("id"),
                })

        logger.info("  %s 已做练习 %d 套", subject, len(practiced))
        return practiced

    def get_all_practice_records(self, subjects: list = None) -> list:
        """
        获取所有题库类型的全部练习记录
        默认获取行测 + 申论
        返回完整的练习记录列表
        """
        if subjects is None:
            subjects = [self.SUBJECT_XINGCE, self.SUBJECT_SHENLUN]

        all_records = []

        for subject in subjects:
            logger.info("=== 开始采集 %s 数据 ===", subject)
            practiced = self.get_practiced_exercises(subject)

            for i, item in enumerate(practiced):
                logger.info("  (%d/%d) 获取练习: %s",
                           i + 1, len(practiced), item["paper_name"])

                # 获取练习详情（用户作答）
                exercise_data = self.get_exercise_detail(
                    item["exercise_id"], subject
                )

                if not exercise_data:
                    continue

                # 提取题目ID列表
                question_ids = self._extract_question_ids(exercise_data)

                # 批量获取题目元数据
                questions = []
                if question_ids:
                    # 分批获取，每批最多 50 个
                    for j in range(0, len(question_ids), 50):
                        batch = question_ids[j:j + 50]
                        questions.extend(self.get_questions(batch, subject))

                # 合并练习详情和题目数据
                records = self._merge_exercise_questions(
                    exercise_data, questions, item
                )
                all_records.extend(records)

            logger.info("=== %s 采集完成: %d 条记录 ===", subject, len(all_records))

        return all_records

    def _extract_question_ids(self, exercise_data: dict) -> list:
        """从练习详情中提取题目ID列表"""
        question_ids = []

        # 练习详情中的题目列表
        questions = exercise_data.get("questions", [])
        for q in questions:
            qid = q.get("questionId") or q.get("id")
            if qid:
                question_ids.append(qid)

        # 也从用户作答记录中提取
        answers = exercise_data.get("answers", [])
        for ans in answers:
            qid = ans.get("questionId") or ans.get("id")
            if qid and qid not in question_ids:
                question_ids.append(qid)

        return question_ids

    def _merge_exercise_questions(self, exercise_data: dict,
                                   questions: list, paper_info: dict) -> list:
        """
        合并练习详情（用户作答）和题目元数据（正确答案等）
        返回标准化的练习记录列表
        """
        # 构建题目元数据查找表
        question_map = {}
        for q in questions:
            qid = q.get("id") or q.get("questionId")
            if qid:
                question_map[qid] = q

        # 构建用户作答查找表
        answer_map = {}
        answers = exercise_data.get("answers", [])
        for ans in answers:
            qid = ans.get("questionId") or ans.get("id")
            if qid:
                answer_map[qid] = ans

        # 如果 answers 为空，尝试从 questions 字段中提取用户答案
        exercise_questions = exercise_data.get("questions", [])
        for eq in exercise_questions:
            qid = eq.get("questionId") or eq.get("id")
            if qid and qid not in answer_map:
                answer_map[qid] = eq

        records = []
        practice_name = exercise_data.get("name", paper_info.get("paper_name", ""))
        practice_time = exercise_data.get("createTime") or exercise_data.get("startTime") or ""

        for qid, user_answer in answer_map.items():
            question_meta = question_map.get(qid, {})

            # 用户选择的答案
            user_choice = self._extract_user_choice(user_answer)
            # 正确答案
            correct_choice = self._extract_correct_choice(question_meta)
            # 判断对错
            is_correct = self._check_correct(user_choice, correct_choice)

            # 构建题目链接：指向粉笔网页版的练习报告页
            # 用户登录网页版后点击即可查看本次练习的对错详情
            exercise_id = paper_info.get("exercise_id", "")
            subject_val = paper_info.get("subject", subject)
            question_link = self._build_question_link(exercise_id, subject_val, qid)

            record = {
                "question_id": qid,
                "exercise_id": exercise_id,
                "question_link": question_link,
                "practice_name": practice_name,
                "practice_time": practice_time,
                "user_answer": user_choice,
                "correct_answer": correct_choice,
                "is_correct": is_correct,
                # 题目元数据
                "module": question_meta.get("categoryName") or question_meta.get("module", ""),
                "sub_module": question_meta.get("typeName") or question_meta.get("subModule", ""),
                "question_type": question_meta.get("typeName", ""),
                "year": question_meta.get("year", ""),
                "source": question_meta.get("provinceName") or question_meta.get("source", ""),
                "exam_type": question_meta.get("examType", ""),
                "difficulty": question_meta.get("difficulty", ""),
                # 试卷信息
                "paper_name": paper_info.get("paper_name", ""),
                "source_label": paper_info.get("source_label", ""),
                "subject": paper_info.get("subject", ""),
            }
            records.append(record)

        # 也处理有元数据但没有用户作答的题目（可能未作答）
        for qid, question_meta in question_map.items():
            if qid not in answer_map:
                correct_choice = self._extract_correct_choice(question_meta)
                exercise_id = paper_info.get("exercise_id", "")
                subject_val = paper_info.get("subject", subject)
                question_link = self._build_question_link(exercise_id, subject_val, qid)

                record = {
                    "question_id": qid,
                    "exercise_id": exercise_id,
                    "question_link": question_link,
                    "practice_name": practice_name,
                    "practice_time": practice_time,
                    "user_answer": "",
                    "correct_answer": correct_choice,
                    "is_correct": False,
                    "module": question_meta.get("categoryName") or question_meta.get("module", ""),
                    "sub_module": question_meta.get("typeName") or question_meta.get("subModule", ""),
                    "question_type": question_meta.get("typeName", ""),
                    "year": question_meta.get("year", ""),
                    "source": question_meta.get("provinceName") or question_meta.get("source", ""),
                    "exam_type": question_meta.get("examType", ""),
                    "difficulty": question_meta.get("difficulty", ""),
                    "paper_name": paper_info.get("paper_name", ""),
                    "source_label": paper_info.get("source_label", ""),
                    "subject": paper_info.get("subject", ""),
                }
                records.append(record)

        return records

    def _extract_user_choice(self, answer_data: dict) -> str:
        """从用户作答数据中提取选择的答案"""
        answer = answer_data.get("answer", {})
        if isinstance(answer, dict):
            choice = answer.get("choice", "")
            if choice:
                return choice
        if isinstance(answer, str):
            return answer
        return str(answer) if answer else ""

    def _extract_correct_choice(self, question_meta: dict) -> str:
        """从题目元数据中提取正确答案"""
        correct = question_meta.get("correctAnswer", {})
        if isinstance(correct, dict):
            choice = correct.get("choice", "")
            if choice:
                return choice
        if isinstance(correct, str):
            return correct
        return str(correct) if correct else ""

    def _check_correct(self, user_choice: str, correct_choice: str) -> bool:
        """判断用户答案是否正确"""
        if not user_choice or not correct_choice:
            return False
        return user_choice.strip().lower() == correct_choice.strip().lower()

    def _build_question_link(self, exercise_id, subject: str, question_id) -> str:
        """
        构建粉笔网页版的练习报告链接
        点击后可查看本次练习中该题目的对错情况

        URL格式: https://www.fenbi.com/spa/tiku/#/{subject}/xingce/{exercise_id}
        其中 subject 为题库类型（xingce/shenlun）

        注意: 此URL格式基于逆向分析，粉笔可能会更新路由。
        如链接无法打开，请在浏览器中打开一次练习报告页，
        将实际URL格式告知开发者以更新此函数。
        """
        if not exercise_id:
            return ""
        # 粉笔网页版 SPA 路由
        return f"https://www.fenbi.com/spa/tiku/#/{subject}/xingce/{exercise_id}"
