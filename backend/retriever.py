"""
============================================================
检索模块（阶段2）：从 Chroma 取 Top-K 相关片段，并组装提示词
============================================================
【说明】
  · 当前向量为本地占位哈希（阶段1），检索为粗略匹配，用于先跑通流程；
    后续接入在线中文 embedding 后，只需替换 vectorstore 的
    embedding_function，本模块检索代码不用改。
  · 检索结果同时承担「引用溯源」：每条都带文件名 / 页码 / 章节 / 原文，
    前端据此渲染引用卡片并在侧边栏高亮。
============================================================
"""

import re

import vectorstore

DEFAULT_TOP_K = 3  # 每次取回的相关片段数

# 无依据距离阈值（余弦距离，越小越相关）：
# top1 距离大于该值，说明库里没有足够相关的片段（如问了外省政策）。
# 注意：发卡判断已不再依赖该阈值（改为“模型未拒答即发卡”，避免固定阈值
# 误杀“答案实质但距离偏高”的问题）；本函数保留用于诊断与后续调优。
NO_EVIDENCE_DIST = 0.30

# 泛词停用表：只剔除最泛、句句都有的词；其余词都参与打分，取命中最多的一句
_STOP = set(
    "企业 贷款 政策 支持 融资 小微 小型 微型 公司 项目 服务 发展 经济 资金 "
    "补贴 申请 条件 需要 什么 可以 如何 怎么 多少 进行 通过 给予 予以 以及 "
    "有关 相关 地方 国家 银行 借款 利息 措施 内容 方面 规定 办法 机制 体系"
    .split()
)


def _select_blocks(question: str, text: str) -> list:
    """
    从召回片段里挑出最相关的几块，供前端标黄。
    做法：按句号/叹号/问号/分号切块（换行是排版折行，不作边界）；
    跳过政策依据/联系方式/责任单位/文号等元信息；用问题两字词（剔泛词）打分，
    只保留分数接近最高的块（相对阈值），避免泛词碎片全部达标导致铺满。
    """
    # 问题里的中文两字词，剔除泛词，作为打分信号
    grams = set()
    for seg in re.findall(r"[\u4e00-\u9fa5]+", question or ""):
        for i in range(len(seg) - 1):
            g = seg[i:i + 2]
            if g not in _STOP:
                grams.add(g)
    if not grams:
        return []

    # 只按句末标点/分号切块；换行只是 PDF 排版折行，不作为边界
    blocks = [b.strip() for b in re.split(r"[。！？；;]+", text or "") if b.strip()]
    # 元信息块：政策依据/联系方式/责任单位/文号等，不是答案，跳过
    meta_marks = ("咨询电话", "扫码查看", "申请材料", "政策依据", "财金", "〔",
                  "申请途径", "按要求提供", "原件", "详见", "文号", "0951",
                  "财政厅", "市场监管厅", "扫码", "原文", "银保监局", "证监局",
                  "人民银行", "统计局", "农业农村厅", "工业和信息化厅", "负责）", "负责)")

    scored = []
    for b in blocks:
        if any(m in b for m in meta_marks):
            continue
        compact = re.sub(r"\s+", "", b)
        # 短块且含数字编号（目录/文号残留，如“148 民间投资专项担保计划”）跳过
        if len(compact) < 15 and re.search(r"\d{2,}", compact):
            continue
        score = sum(1 for g in grams if g in compact)
        if score > 0:
            scored.append((b.strip(), score))

    if not scored:
        return []
    max_score = max(s for _, s in scored)
    # 相对阈值：保留达到最高分 60% 的块，且至少命中 2 个信号词，淘汰泛命中碎片
    threshold = max(2, round(max_score * 0.6))
    return [b for b, s in scored if s >= threshold]


def search(question: str, top_k: int = DEFAULT_TOP_K) -> list:
    """
    检索与问题最相关的 top_k 个片段。
    返回引用项列表：[{doc_id, file_name, page, chapter, snippet, text}]
    """
    col = vectorstore.get_collection()
    # 空库直接返回，避免空集合 query 的边界问题
    if col.count() == 0:
        return []

    results = col.query(
        query_texts=[question],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    citations = []
    # query 结果按「列表的列表」返回，取第 0 组
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    for idx, (doc_text, meta) in enumerate(zip(docs, metas)):
        page = meta.get("page") or ""
        chapter = meta.get("chapter") or ""
        # 卡片摘要：取原文前 80 字，避免卡片过长
        snippet = doc_text[:80].replace("\n", " ").strip() + ("…" if len(doc_text) > 80 else "")
        # 后端挑出的所有相关块：highlight 取首块兼容旧逻辑，highlights 供前端多块一起标黄
        blocks = _select_blocks(question, doc_text)
        # 该片段与问题的余弦距离（越小越相关），供后端判断「资料中是否有依据」
        distance = dists[idx] if idx < len(dists) else 1.0
        citations.append({
            "doc_id": meta.get("doc_id", ""),
            "file_name": meta.get("file_name", ""),
            "page": page,
            "chapter": chapter,
            "snippet": snippet,
            "text": doc_text,
            "distance": distance,
            "highlight": blocks[0] if blocks else "",
            "highlights": blocks,
        })
    return citations


def has_evidence(citations: list) -> bool:
    """
    判断检索结果是否真的有依据：top1 片段距离低于阈值才算相关。
    用于「问了库里没有的地区/政策」时不展示引用卡片，避免无关出处。
    """
    if not citations:
        return False
    return citations[0].get("distance", 1.0) < NO_EVIDENCE_DIST


def build_context(citations: list) -> str:
    """把检索片段拼成给 LLM 的参考资料文本，编号并标注出处。"""
    parts = []
    for i, c in enumerate(citations, start=1):
        location = f"第 {c['page']} 页" if c["page"] else (c["chapter"] or "全文")
        parts.append(f"【资料{i}】来源：{c['file_name']}（{location}）\n{c['text']}")
    return "\n\n".join(parts)


# 系统提示词：约束模型只依据资料回答，不编造政策；并控制表达不啰嗦
SYSTEM_PROMPT = (
    "你是县域中小企业政策顾问。请严格只依据下面【参考资料】回答，"
    "不要编造资料以外的政策、金额或条件。表达要求："
    "（1）资料里有答案时，直接给出答案本身，不要写“根据资料/现有文档/说明如下”这类开场铺垫，"
    "也不要逐条罗列【资料1】【资料2】分别讲了什么；"
    "（2）资料里确实没有该问题的内容时，只用一两句简洁说明“资料中未涉及××”，"
    "并建议向当地相关主管部门咨询；不要重复声明“未找到”，不要清点每份资料为何没有，"
    "也不要提及现有资料属于哪个地区、不要解释资料里另有什么别的内容；"
    "（3）回答使用简洁通顺的中文，适合分点时再分点列出。"
)


def build_messages(question: str, citations: list, history: list = None) -> list:
    """
    组装发给 LLM 的 messages：系统提示 + 历史对话 + 参考资料 + 当前问题。
    history: [{"role":"user"/"assistant","content":"..."}]，由前端传入最近若干轮，
    用于理解追问里的指代（如"它""这项政策"）；检索仍只按当前问题。
    """
    context = build_context(citations)
    user_content = (
        f"【参考资料】\n{context}\n\n"
        f"【用户问题】{question}\n"
        f"请依据以上参考资料回答。"
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 插入历史对话（只保留合法角色与非空内容，防止脏数据报错）
    for h in (history or []):
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_content})
    return messages


# ------------------------------------------------------------
# 政策匹配专用：根据企业画像推荐适用政策清单 + 匹配理由
# ------------------------------------------------------------
MATCH_PROMPT = (
    "你是县域中小企业政策匹配顾问。请严格只依据下面【参考资料】中的政策，"
    "结合给出的企业画像，挑出真正适用的政策逐条推荐；不要编造资料里没有的政策、金额或条件。"
    "每条推荐用如下格式：\n"
    "【政策名称】\n· 匹配理由：为什么适合这家企业（结合行业/规模/地区/年限/诉求）\n"
    "· 扶持内容：资料里写明的补贴、贷款、税收等具体措施\n"
    "若资料中没有匹配项，只用一句话说明资料中暂未找到与该画像匹配的政策即可，不要清点每份资料。"
)


def build_match_messages(profile: dict, citations: list) -> list:
    """
    政策匹配：系统提示 + 参考资料 + 企业画像，让模型输出推荐清单与理由。
    profile: {industry, size, region, years, need}
    """
    context = build_context(citations)
    user_content = (
        "【参考资料】\n" + context + "\n\n"
        "【企业画像】\n"
        f"行业：{profile.get('industry', '未填')}\n"
        f"规模：{profile.get('size', '未填')}\n"
        f"地区：{profile.get('region', '未填')}\n"
        f"成立年限：{profile.get('years', '未填')}\n"
        f"主要诉求：{profile.get('need', '未填')}\n\n"
        "请依据参考资料，为这家企业推荐适用政策并给出匹配理由。"
    )
    return [
        {"role": "system", "content": MATCH_PROMPT},
        {"role": "user", "content": user_content},
    ]
