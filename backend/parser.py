"""
============================================================
文档解析与切分模块（阶段1）
============================================================
职责：把上传的 PDF / Word 文件解析成「带位置信息的段落」，
      再按长度切成可供检索的片段（chunk）。

【溯源元数据为什么重要】（本项目核心卖点"答案可溯源"的地基）
每个片段必须记住它来自：
  · file_name  文件名（引用卡片显示）
  · page       页码（PDF 有效；Word 无页码，置 None）
  · chapter    章节标题（Word 从标题样式识别；PDF 暂为 None）
  · chunk_index 在本文档内的片段序号
这样阶段2 检索命中后，才能准确标注"第X页 / 第X章"并定位原文。

【切分策略说明】
  · 目标片段长度 TARGET_LEN = 500 字左右（政策条文不宜过长，便于精确检索）
  · 相邻片段保留 OVERLAP = 50 字重叠（避免一句话被切成两半、两边都检索不到）
  · PDF：按「整页文本」为单位进入切分器，页码随段落记录
  · Word：按「段落」为单位，遇到标题样式时更新当前章节
============================================================
"""

import io
import re

# 切分参数（可调：答辩前可按实际文档密度微调）
TARGET_LEN = 500   # 每个片段目标字数
OVERLAP = 50       # 相邻片段重叠字数，防止句子被腰斩


# ------------------------------------------------------------
# 目录页识别：手册前几页的"目录"没有检索价值，跳过不入库
# 判定依据：一页里出现多条「标题 + 引导点线 + 页码」的目录行
# ------------------------------------------------------------
def _is_toc_page(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    toc_lines = 0
    for ln in lines:
        # 目录行：含连续引导点（.... / …… / ···），行尾是页码数字
        if re.search(r"[.．·…]{3,}", ln) and re.search(r"\d+\s*$", ln):
            toc_lines += 1
    # 密集目录行（≥5）直接判为目录页
    if toc_lines >= 5:
        return True
    # 页首写着"目录"且目录行≥3，也判为目录页
    head = lines[0] if lines else ""
    if ("目录" in head) and toc_lines >= 3:
        return True
    return False


# ------------------------------------------------------------
# 1. PDF 解析（pypdf，逐页提取，记录页码，页码从 1 开始）
# ------------------------------------------------------------
def parse_pdf(file_bytes: bytes) -> list:
    """把 PDF 字节流解析为段落列表：[{text, page, chapter}]"""
    from pypdf import PdfReader  # 延迟导入：仅在需要解析 PDF 时加载

    reader = PdfReader(io.BytesIO(file_bytes))
    paragraphs = []

    for page_idx, page in enumerate(reader.pages, start=1):
        # extract_text() 取出本页全部文字；空白页返回空串
        text = (page.extract_text() or "").strip()
        # 跳过目录页（无检索价值，避免检索误命中、侧边栏铺满目录页码）
        if text and not _is_toc_page(text):
            # PDF 无法稳定识别章节，chapter 先置 None
            paragraphs.append({"text": text, "page": page_idx, "chapter": None})

    if not paragraphs:
        raise ValueError("PDF 中未提取到任何文字（可能是扫描件图片，需要 OCR，本阶段不支持）")
    return paragraphs


# ------------------------------------------------------------
# 2. Word 解析（python-docx，按段落读取，识别标题作为章节）
# ------------------------------------------------------------
def parse_docx(file_bytes: bytes) -> list:
    """把 Word 字节流解析为段落列表：[{text, page, chapter}]；Word 无页码，page=None"""
    import docx  # 延迟导入

    document = docx.Document(io.BytesIO(file_bytes))
    paragraphs = []
    current_chapter = None  # 当前所处章节，遇到新标题时更新

    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue  # 跳过空行

        # 识别标题样式：英文 Heading 1/2… 或中文"标题 1/2…"
        style_name = para.style.name if para.style is not None else ""
        if style_name.startswith("Heading") or "标题" in style_name:
            current_chapter = text

        paragraphs.append({"text": text, "page": None, "chapter": current_chapter})

    if not paragraphs:
        raise ValueError("Word 中未提取到任何文字")
    return paragraphs


# ------------------------------------------------------------
# 3. 统一入口：按扩展名选择解析器
# ------------------------------------------------------------
def parse_document(file_bytes: bytes, file_name: str) -> tuple:
    """
    返回 (source_type, paragraphs)
    source_type: "pdf" / "docx"
    不支持的扩展名抛出 ValueError，由接口层转成 400 错误。
    """
    lower_name = file_name.lower()
    if lower_name.endswith(".pdf"):
        return "pdf", parse_pdf(file_bytes)
    elif lower_name.endswith(".docx") or lower_name.endswith(".doc"):
        # .doc（老格式）python-docx 不支持，单独给出更清晰的提示
        if lower_name.endswith(".doc"):
            raise ValueError("暂不支持旧版 .doc 格式，请另存为 .docx 后上传")
        return "docx", parse_docx(file_bytes)
    else:
        raise ValueError("仅支持 PDF（.pdf）和 Word（.docx）文件")


# ------------------------------------------------------------
# 4. 切分：把段落列表切成带元数据的片段
# ------------------------------------------------------------
def chunk_paragraphs(paragraphs: list, target_len: int = TARGET_LEN, overlap: int = OVERLAP) -> list:
    """
    滑动累积切分：
    返回 [{text, page, chapter, chunk_index, seg_pages}]
      · page       该片段主体所在页（PDF）/ None（Word）
      · chapter    该片段所在章节
      · seg_pages  片段覆盖到的所有页码（列表，供溯源展示）
    """
    chunks = []
    buffer_text = ""          # 当前累积片段的文字
    buffer_pages = []         # 当前片段覆盖的页码
    buffer_chapters = []      # 当前片段经过的章节

    for para in paragraphs:
        text = para["text"].strip()
        if not text:
            continue

        # 缓冲非空，且加上本段会超过目标长度 → 先把当前缓冲落盘为一个片段
        if buffer_text and len(buffer_text) + len(text) > target_len:
            chunks.append(_make_chunk(buffer_text, buffer_pages, buffer_chapters))
            # 重叠：保留缓冲末尾 overlap 字，避免上下文断裂
            buffer_text = buffer_text[-overlap:] + "\n"
            buffer_pages = [buffer_pages[-1]] if buffer_pages else []
            buffer_chapters = [buffer_chapters[-1]] if buffer_chapters else []

        buffer_text += text + "\n"
        if para["page"] is not None:
            buffer_pages.append(para["page"])
        if para["chapter"]:
            buffer_chapters.append(para["chapter"])

    # 收尾：剩余缓冲作为最后一个片段
    if buffer_text.strip():
        chunks.append(_make_chunk(buffer_text, buffer_pages, buffer_chapters))

    # 写入片段序号
    for idx, chunk in enumerate(chunks):
        chunk["chunk_index"] = idx

    return chunks


def _make_chunk(buffer_text: str, buffer_pages: list, buffer_chapters: list) -> dict:
    """把缓冲打包成一个片段字典（内部工具）。"""
    pages = [p for p in buffer_pages if p is not None]
    return {
        "text": buffer_text.strip(),
        # 主体页码取片段最后出现的页；Word 为 None
        "page": pages[-1] if pages else None,
        # 章节取最后出现的章节
        "chapter": buffer_chapters[-1] if buffer_chapters else None,
        "seg_pages": pages,
    }
