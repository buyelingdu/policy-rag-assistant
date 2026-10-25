"""
政策通 · 中小企业政策助手 —— 后端入口
============================================
阶段2：检索 + DeepSeek 流式问答（SSE）+ 引用溯源。

后续阶段在此文件上增量扩展（每阶段保持可独立运行）：
  阶段3  多轮对话（会话上下文）
  阶段4  反馈记录 / 政策匹配表单 / 数据看板

运行方式（在 backend 目录下）：
  pip install -r requirements.txt
  uvicorn main:app --reload --port 8000
  健康检查：http://127.0.0.1:8000/api/health
============================================
"""

import json
import re
import uuid
from datetime import datetime

from dotenv import load_dotenv  # 读取 .env 环境变量（密钥只放在后端，绝不进前端）
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import parser          # 阶段1：PDF/Word 解析 + 切分（带页码/章节元数据）
import vectorstore     # 阶段1：Chroma 本地持久化增/删/列表
import retriever       # 阶段2：Top-K 检索 + 提示词组装
import llm             # 阶段2：DeepSeek 流式/非流式客户端
import stats           # 阶段4：提问计数 / 反馈 / 看板统计（JSONL 本地存储）

# 加载 .env 文件（不存在时不报错，参考 .env.example 创建）
load_dotenv()

app = FastAPI(
    title="政策通 · 中小企业政策助手 API",
    description="县域中小企业政策 RAG 知识助手后端（阶段2：检索 + DeepSeek 流式问答 + 引用溯源）",
    version="0.3.0",
)

# ------------------------------------------------------------
# CORS（跨域）配置
# 为什么需要：前端静态页面与后端端口不同（本地：前端 5500/8000，后端 8000；
# 部署：前端 Vercel、后端 Render），浏览器会拦截跨域请求，必须由后端放行。
# 演示阶段放开所有来源；正式上线建议收敛为前端实际域名。
# ------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    """健康检查接口：前端启动时调用，确认后端在线。"""
    return {
        "status": "ok",
        "service": "policy-rag-backend",
        "version": "0.3.0",
    }


# ============================================================
# 阶段1：文档管理接口（上传 / 列表 / 删除）
# ============================================================

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    上传 PDF / Word：解析 → 切分（带页码/章节元数据）→ 向量化存入 Chroma。
    返回 doc_id、文件名、页数、片段数（前端据此提示"已索引 N 个片段"）。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 1) 解析 + 切分：格式问题由 parser 抛 ValueError，转成 400 友好提示
    try:
        source_type, paragraphs = parser.parse_document(content, file.filename)
        chunks = parser.chunk_paragraphs(paragraphs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文档解析失败：{e}")

    if not chunks:
        raise HTTPException(status_code=400, detail="切分后没有可用片段，请检查文档内容")

    # 2) 生成文档 id 与上传时间，向量化入库
    doc_id = uuid.uuid4().hex[:12]
    uploaded_at = datetime.now().isoformat(timespec="seconds")
    chunk_count = vectorstore.add_chunks(
        doc_id=doc_id,
        file_name=file.filename,
        source_type=source_type,
        chunks=chunks,
        uploaded_at=uploaded_at,
    )

    # PDF 取最大页码作为页数；Word 无页码记 0
    page_count = max((c["page"] for c in chunks if c["page"] is not None), default=0)

    return {
        "doc_id": doc_id,
        "file_name": file.filename,
        "source_type": source_type,
        "page_count": page_count,
        "chunk_count": chunk_count,
        "status": "indexed",
    }


@app.get("/api/documents")
def list_documents():
    """返回已索引文档列表（按上传时间倒序），供文档管理弹窗展示。"""
    return {"documents": vectorstore.list_documents()}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    """按 doc_id 删除该文档的全部片段。"""
    ok = vectorstore.delete_document(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="文档不存在或已删除")
    return {"deleted": doc_id}


# ============================================================
# 阶段2：流式问答（SSE）+ 引用溯源
# ============================================================

# ------------------------------------------------------------
# 闲聊 / 无关问题识别：命中则不检索、不挂引用卡片，直接专业引导
# （避免日常问题硬召回无关政策片段、出现不相干引用卡片）
# ------------------------------------------------------------
# 政策关键词：问题里出现这些词，一律视为政策相关，不判为闲聊（防止误杀正常提问）
POLICY_WORDS = [
    "政策", "补贴", "贷款", "担保", "税收", "减免", "融资", "创业", "就业",
    "社保", "保险", "奖励", "扶持", "申请", "企业", "小微", "规模", "年限",
    "行业", "地区", "账款", "负担", "挂牌", "上市", "贴息", "费用", "条件",
    "材料", "标准", "个体工商户", "农户", "用工", "人才", "场地", "税收优惠",
]

# 明确的闲聊 / 非政策意图（正则，忽略大小写）
CHITCHAT_PATTERNS = [
    r"你是谁", r"你叫什么", r"你的名字", r"介绍一下你自己", r"你是哪位",
    r"吃饭", r"吃了吗", r"睡了吗", r"睡觉", r"休息",
    r"天气", r"下雨", r"气温", r"几度", r"天气预报",
    r"笑话", r"讲个", r"唱歌", r"歌词", r"写诗", r"写一首", r"讲个故事", r"故事",
    r"你多大", r"你几岁", r"年龄", r"性别", r"结婚", r"女朋友", r"男朋友",
    r"^\s*(hi|hello|hey|你好)\s*$",
]

# 闲聊时返回的专业引导文案（不调用大模型，直接给出）
OFFTOPIC_REPLY = (
    "抱歉，我是县域中小企业政策助手，只依据已上传的政策文件解答补贴、税收、"
    "融资担保、用工就业等问题，不回答日常闲聊。请提出具体政策问题，例如："
    "“小微企业申请创业担保贷款需要什么条件？”"
)


def is_offtopic(question: str) -> bool:
    """判断是否为闲聊/无关问题：含政策关键词则放行，否则匹配闲聊规则。"""
    if any(w in question for w in POLICY_WORDS):
        return False
    return any(re.search(p, question, re.IGNORECASE) for p in CHITCHAT_PATTERNS)


# ------------------------------------------------------------
# 「资料中没有答案」的拒答识别：模型按系统提示会用这些措辞说明资料未涉及。
# 命中时说明召回的片段其实不支撑答案，前端应撤掉引用卡片，避免展示无关出处。
# ------------------------------------------------------------
NO_EVIDENCE_PATTERNS = [
    r"资料中未涉及", r"资料未涉及", r"未涉及",
    r"资料中没有", r"资料未收录", r"资料中暂未",
    r"未找到相关", r"暂未找到", r"暂无相关", r"没有相关政策",
    r"未收录", r"无法提供.{0,8}相关",
]


def is_no_evidence(text: str) -> bool:
    """判断完整答案是否为「资料未涉及/未找到」式拒答；空答案不算拒答。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(re.search(p, t) for p in NO_EVIDENCE_PATTERNS)


class ChatRequest(BaseModel):
    question: str
    # 最近若干轮对话 [{"role":"user"/"assistant","content":"..."}]，用于多轮指代理解
    history: list = []


def sse_packet(payload: dict) -> str:
    """打包一条 SSE 事件帧：data: {json}\\n\\n"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """
    SSE 流式问答：检索 Top-K → 推送引用卡片 → 逐字推送回答。
    事件类型：status / citations / delta / done / error。
    流式失败自动降级为非流式一次性返回；未配置密钥或无文档时友好报错，不崩溃。
    """
    question = (req.question or "").strip()

    def event_gen():
        # 0) 空问题兜底（前端已 trim，这里做二次防御）
        if not question:
            yield sse_packet({"type": "error", "message": "问题不能为空，请输入您想咨询的政策问题。"})
            return

        # 0.5) 闲聊/无关问题：不检索、不调大模型、不挂引用卡片，直接专业引导
        if is_offtopic(question):
            yield sse_packet({"type": "delta", "text": OFFTOPIC_REPLY})
            yield sse_packet({"type": "done"})
            return

        # 1) 未配置密钥：直接返回友好提示（前端展示，不白屏）
        if not llm.is_configured():
            yield sse_packet({
                "type": "error",
                "message": "尚未配置 DeepSeek 密钥：请在 backend/.env 填入 DEEPSEEK_API_KEY 后重启后端",
            })
            return

        # 2) 检索知识库
        yield sse_packet({"type": "status", "text": "正在检索知识库…"})
        citations = retriever.search(question)
        if not citations:
            yield sse_packet({
                "type": "error",
                "message": "知识库还没有文档，请先在「文档管理」上传政策文件",
            })
            return

        # 3) 记录一次有效提问（供看板统计）；引用卡片不在此先发，
        #    等答案生成完、确认有依据后再发，避免「资料未涉及」时卡片先闪现
        stats.record_question(question)

        # 4) 流式生成回答（边收边累积完整答案，用于结尾判断是否为「资料未涉及」拒答）
        yield sse_packet({"type": "status", "text": "正在生成回答…"})
        messages = retriever.build_messages(question, citations, req.history)
        full_text = ""
        try:
            for delta in llm.stream_chat(messages):
                if delta:
                    full_text += delta
                    yield sse_packet({"type": "delta", "text": delta})
        except Exception as e:
            # 降级：流式中断时尝试非流式一次性返回完整答案
            try:
                full = llm.chat_complete(messages)
                full_text = full
                yield sse_packet({"type": "delta", "text": full})
            except Exception:
                yield sse_packet({"type": "error", "message": f"回答生成失败：{e}"})
                return

        # 5) 发引用卡片：只要模型没有明确说“资料未涉及/未找到”式拒答，
        #    就说明它是依据召回片段给出了实质答案，应展示出处卡片供溯源。
        #    不再用固定距离阈值卡发卡（0.30 阈值会误杀“答案实质但距离偏高”的问题，
        #    如口语化/复合提问），拒答时全程不发卡，不闪现、不误挂无关出处。
        if not is_no_evidence(full_text):
            yield sse_packet({"type": "citations", "items": citations})

        yield sse_packet({"type": "done"})

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)


# ============================================================
# 阶段4：政策匹配表单（企业画像 → 推荐政策清单 + 匹配理由，SSE 流式）
# ============================================================

class MatchRequest(BaseModel):
    industry: str = ""   # 行业
    size: str = ""       # 企业规模
    region: str = ""     # 地区
    years: str = ""      # 成立年限
    need: str = ""       # 主要诉求


@app.post("/api/match")
def match_policies(req: MatchRequest):
    """
    根据企业画像检索资料，流式输出推荐政策清单与匹配理由。
    SSE 事件协议与 /api/chat/stream 一致：status / citations / delta / done / error。
    """
    profile = req.model_dump()
    # 把画像拼成一句检索语，用于 Top-K 召回相关政策片段
    query = (
        f"{req.region} {req.industry} {req.size}企业，成立{req.years}，"
        f"主要诉求{req.need}，有哪些适用的扶持政策"
    )

    def event_gen():
        if not llm.is_configured():
            yield sse_packet({
                "type": "error",
                "message": "尚未配置 DeepSeek 密钥：请在 backend/.env 填入 DEEPSEEK_API_KEY 后重启后端",
            })
            return

        yield sse_packet({"type": "status", "text": "正在根据企业画像匹配政策…"})
        citations = retriever.search(query)
        if not citations:
            yield sse_packet({
                "type": "error",
                "message": "知识库还没有文档，请先在「文档管理」上传政策文件",
            })
            return

        # 引用卡片等推荐清单生成完、确认有匹配项后再发，避免无匹配时先闪现
        yield sse_packet({"type": "status", "text": "正在生成推荐清单…"})
        messages = retriever.build_match_messages(profile, citations)

        full_text = ""
        try:
            for delta in llm.stream_chat(messages):
                if delta:
                    full_text += delta
                    yield sse_packet({"type": "delta", "text": delta})
        except Exception as e:
            # 降级：流式中断时非流式一次性返回
            try:
                full = llm.chat_complete(messages)
                full_text = full
                yield sse_packet({"type": "delta", "text": full})
            except Exception:
                yield sse_packet({"type": "error", "message": f"推荐生成失败：{e}"})
                return

        # 模型未明确说“暂未找到匹配”就发卡片：说明它给出了实质推荐，
        # 卡片即推荐所依据的资料片段，供核对溯源；无匹配项（拒答措辞）不发无关出处
        if not is_no_evidence(full_text):
            yield sse_packet({"type": "citations", "items": citations})

        yield sse_packet({"type": "done"})

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)

class FeedbackRequest(BaseModel):
    question: str
    answer: str = ""
    useful: bool
    citations_count: int = 0


@app.post("/api/feedback")
def submit_feedback(req: FeedbackRequest):
    """记录一条「有用 / 无用」反馈（写入本地 JSONL）。"""
    stats.record_feedback(req.question, req.answer, req.useful, req.citations_count)
    return {"ok": True}


@app.get("/api/stats")
def get_stats():
    """看板统计：累计提问数、高频问题Top5、反馈数、文档数。"""
    data = stats.get_stats()
    data["doc_count"] = len(vectorstore.list_documents())
    return data
