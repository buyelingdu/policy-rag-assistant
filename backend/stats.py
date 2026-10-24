"""
stats.py —— 轻量本地统计存储（阶段4：反馈 + 数据看板）
=====================================================
不引入数据库，用 JSONL（每行一条 JSON）追加写入 backend/data/ 目录，
重启不丢、便于答辩演示。后续要换数据库只改本文件，接口不变。

记录两类数据：
  questions.jsonl  每次提问（用于累计提问数、高频问题 Top5）
  feedback.jsonl   每条回答的有用 / 无用反馈
"""

import json
import os
import re
from collections import Counter
from datetime import datetime

# 数据目录：与本文件同级的 data/
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
QUESTION_FILE = os.path.join(DATA_DIR, "questions.jsonl")
FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback.jsonl")

# 纯开场问候 / 自我介绍 / 预告提问的句子：不构成政策问题，不计入看板高频问题
_GREETING_PATTERN = re.compile(
    r"^\s*(你好|您好|hi|hello|hey)[!,，。\s]*$"          # 单独一句“你好”
    r"|自我介绍|我是.{0,15}(负责人|老板|创业者|企业主)"   # 自我介绍
    r"|接下来.{0,15}(一系列|几个|一些)?(问题|提问|咨询)"  # 预告“接下来要问问题”
)


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _append(path: str, record: dict):
    """以 UTF-8 追加写一行 JSON（损坏行读取时跳过，不影响整体）。"""
    _ensure_dir()
    record["ts"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_all(path: str) -> list:
    """读回全部记录，跳过损坏行。"""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ------------------------------------------------------------
# 1) 提问记录
# ------------------------------------------------------------
def record_question(question: str):
    """记录一次提问。纯开场问候/自我介绍不属于政策问题，不进看板统计。"""
    q = (question or "").strip()
    if not q:
        return
    # 只过滤“你好开场 / 自我介绍 / 预告要提问”这类无实质政策内容的句子
    if _GREETING_PATTERN.search(q):
        return
    _append(QUESTION_FILE, {"type": "question", "question": q})


# ------------------------------------------------------------
# 2) 反馈记录（useful: True 有用 / False 无用）
# ------------------------------------------------------------
def record_feedback(question: str, answer: str, useful: bool, citations_count: int = 0):
    _append(FEEDBACK_FILE, {
        "type": "feedback",
        "question": (question or "").strip(),
        "answer": (answer or "").strip(),
        "useful": bool(useful),
        "citations_count": citations_count,
    })


# ------------------------------------------------------------
# 3) 汇总统计（供看板接口）
# ------------------------------------------------------------
def _normalize(q: str) -> str:
    """问题归一化：去掉所有空白后再计数，避免空格差异把同一问题算成两条。"""
    return "".join((q or "").split()).lower()


def get_stats() -> dict:
    questions = _read_all(QUESTION_FILE)
    feedbacks = _read_all(FEEDBACK_FILE)

    # 高频问题 Top5：按归一化问题计数，展示时保留首次出现的原文
    counter = Counter(_normalize(r.get("question", "")) for r in questions if r.get("question"))
    first_text = {}
    for r in questions:
        key = _normalize(r.get("question", ""))
        if key and key not in first_text:
            first_text[key] = r["question"]

    top = [
        {"question": first_text[key], "count": cnt}
        for key, cnt in counter.most_common(5)
    ]

    useful = sum(1 for r in feedbacks if r.get("useful") is True)
    useless = sum(1 for r in feedbacks if r.get("useful") is False)

    return {
        "total_questions": len(questions),
        "top_questions": top,
        "feedback": {
            "useful": useful,
            "useless": useless,
            "total": len(feedbacks),
        },
    }
