"""
============================================================
向量库层（阶段1）：Chroma 本地持久化 + 文档的增/删/列表
============================================================
【为什么用本地持久化】
Chroma 把向量和元数据写到 backend/chroma_db/ 目录，重启服务不丢失，
答辩现场断网也能复用已索引的文档。

【Embedding 选型说明（重要）】
阶段1 默认使用 Chroma 内置的本地离线向量模型（onnxruntime all-MiniLM-L6-v2）：
  · 优点：无需 API 密钥、可离线、开箱即用，让阶段1 不被密钥阻塞
  · 局限：对中文政策长句的相似度一般；阶段2/优化时可换成在线中文
    embedding（如通义 text-embedding-v3），替换点在 get_collection()，
    届时只需把 embedding_function 换成在线封装，上层增删查代码不动。

【元数据类型注意】
Chroma 的 metadata 值只接受 str/int/float/bool，不能存 None 和 list：
  · 页码缺失（Word）→ 存空字符串 ""
  · 覆盖多页 → 页码用逗号拼成字符串
============================================================
"""

import os
import chromadb
from chromadb.config import Settings

import embedding  # 在线中文向量 / 本地占位 可切换适配层

# 持久化目录：放在 backend 下的 chroma_db（git 里通常忽略该目录）
DB_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")

COLLECTION_NAME = "policy_docs"


# ------------------------------------------------------------
# Embedding 实现已统一放到 embedding.py（在线通义 / 本地占位可切换）
# 这里不再重复定义，get_collection() 通过 embedding.get_embedding_function() 获取。
# ------------------------------------------------------------

# 进程内复用同一个 client / collection，避免反复建连接
_client = None
_collection = None
_scheme = None


def get_client():
    """获取（并缓存）Chroma 持久化客户端。"""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=DB_DIR,
            settings=Settings(anonymized_telemetry=False),  # 关闭匿名遥测
        )
    return _client


def get_collection():
    """
    获取政策文档集合（余弦距离）。
    集合名带向量方案后缀（policy_docs_qwen / policy_docs_hash），
    避免不同维度的在线向量与占位向量混存在同一集合。
    """
    global _collection, _scheme
    fn, scheme = embedding.get_embedding_function()
    if _collection is None or _scheme != scheme:
        client = get_client()
        _collection = client.get_or_create_collection(
            name=f"{COLLECTION_NAME}_{scheme}",
            metadata={"hnsw:space": "cosine"},
            embedding_function=fn,
        )
        _scheme = scheme
    return _collection


# ------------------------------------------------------------
# 1. 写入：把一个文档切好的片段全部入库
# ------------------------------------------------------------
def add_chunks(doc_id: str, file_name: str, source_type: str,
               chunks: list, uploaded_at: str) -> int:
    """
    返回写入的片段数。
    chunks 来自 parser.chunk_paragraphs()。
    """
    col = get_collection()
    ids, documents, metadatas = [], [], []

    for chunk in chunks:
        # 片段唯一 id：文档id#片段序号，便于按文档整体删除
        chunk_id = f"{doc_id}#chunk-{chunk['chunk_index']}"
        ids.append(chunk_id)
        documents.append(chunk["text"])

        pages_str = ",".join(str(p) for p in chunk.get("seg_pages", []))
        metadatas.append({
            "doc_id": doc_id,
            "file_name": file_name,
            "source_type": source_type,
            "page": "" if chunk.get("page") is None else str(chunk["page"]),
            "pages": pages_str,
            "chapter": chunk.get("chapter") or "",
            "chunk_index": str(chunk["chunk_index"]),
            "uploaded_at": uploaded_at,
        })

    col.add(ids=ids, documents=documents, metadatas=metadatas)
    return len(chunks)


# ------------------------------------------------------------
# 2. 列表：按文档聚合（同一份文档的多个片段归并成一条）
# ------------------------------------------------------------
def list_documents() -> list:
    """返回已索引文档列表，每项含 doc_id/文件名/类型/页数/片段数/上传时间。"""
    col = get_collection()
    result = col.get(include=["metadatas"])

    grouped = {}  # doc_id -> 聚合信息
    for meta in result["metadatas"]:
        did = meta["doc_id"]
        if did not in grouped:
            grouped[did] = {
                "doc_id": did,
                "file_name": meta["file_name"],
                "source_type": meta["source_type"],
                "_pages": set(),   # 收集该文档出现过的所有页码
                "chunk_count": 0,
                "uploaded_at": meta["uploaded_at"],
            }
        g = grouped[did]
        # 单页片段的 page 字段
        if meta.get("page"):
            try:
                g["_pages"].add(int(meta["page"]))
            except (ValueError, TypeError):
                pass
        # 跨页片段的 pages 逗号串
        if meta.get("pages"):
            for p in meta["pages"].split(","):
                if p:
                    try:
                        g["_pages"].add(int(p))
                    except (ValueError, TypeError):
                        pass
        g["chunk_count"] += 1

    # 组装结果：页数 = 出现过的最大页码
    out = []
    for g in grouped.values():
        pages = g.pop("_pages")
        g["page_count"] = max(pages) if pages else 0
        out.append(g)

    # 按上传时间倒序（最新在前）
    return sorted(out, key=lambda d: d["uploaded_at"], reverse=True)


# ------------------------------------------------------------
# 3. 删除：按 doc_id 删掉该文档的全部片段
# ------------------------------------------------------------
def delete_document(doc_id: str) -> bool:
    """删除成功返回 True；文档不存在返回 False。"""
    col = get_collection()
    found = col.get(where={"doc_id": doc_id})
    if not found["ids"]:
        return False
    col.delete(ids=found["ids"])
    return True
