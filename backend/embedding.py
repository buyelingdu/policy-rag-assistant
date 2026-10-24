"""
============================================================
Embedding 适配层（阶段2增强）：在线中文向量 / 本地占位 可切换
============================================================
【切换规则】
  · 配置了环境变量 DASHSCOPE_API_KEY → 使用通义 text-embedding-v3 在线中文向量（检索准）
  · 未配置 → 自动回退本地占位哈希向量（离线能跑通，但语义弱）

【为什么按方案分集合】
  在线向量(1024维)与占位向量(256维)维度不同，不能混存在同一 Chroma 集合。
  因此集合名带方案后缀，切换方案自动使用新集合，旧数据不冲突、不用手动清库。

【环境变量（写在 backend/.env）】
  DASHSCOPE_API_KEY=sk-xxx              通义百炼密钥（配了才启用在线向量）
  DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  EMBED_MODEL=text-embedding-v3
  EMBED_DIM=1024
============================================================
"""

import os
import zlib


# ------------------------------------------------------------
# 本地占位向量：无密钥、离线，仅用于先跑通流程（语义弱）
# ------------------------------------------------------------
class LocalHashEmbedding:
    DIM = 256

    def _embed_list(self, input: list) -> list:
        vectors = []
        for text in input:
            vec = [0.0] * self.DIM
            for tok in self._bigrams(text):
                bucket = zlib.crc32(tok.encode("utf-8")) % self.DIM
                vec[bucket] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors

    def embed_documents(self, input: list) -> list:
        return self._embed_list(input)

    def embed_query(self, input: list) -> list:
        return self._embed_list(input)

    def __call__(self, input: list) -> list:
        return self._embed_list(input)

    @staticmethod
    def _bigrams(text: str) -> list:
        text = (text or "").strip()
        if len(text) <= 1:
            return [text] if text else []
        return [text[i:i + 2] for i in range(len(text) - 1)]

    @staticmethod
    def name() -> str:
        return "local-hash-placeholder"


# ------------------------------------------------------------
# 通义在线中文向量（OpenAI 兼容格式调 DashScope）
# ------------------------------------------------------------
class QwenEmbedding:
    BATCH = 10  # 通义 embeddings 单次最多 10 条，超出分批

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.base_url = os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).strip()
        self.model = os.getenv("EMBED_MODEL", "text-embedding-v3").strip() or "text-embedding-v3"
        self.dim = int(os.getenv("EMBED_DIM", "1024"))
        self._client = None

    def name(self) -> str:
        return "qwen-" + self.model

    def _client_get(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _embed(self, texts: list) -> list:
        """调一次接口，返回与输入顺序对齐的向量列表。"""
        resp = self._client_get().embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dim,
            encoding_format="float",
        )
        aligned = [None] * len(texts)
        for item in resp.data:
            aligned[item.index] = item.embedding
        return aligned

    def embed_documents(self, input: list) -> list:
        out = []
        for i in range(0, len(input), self.BATCH):
            out.extend(self._embed(input[i:i + self.BATCH]))
        return out

    def embed_query(self, input: list) -> list:
        # 查询通常只传 1 条；取第一条向量
        return self._embed(list(input))

    def __call__(self, input: list) -> list:
        return self.embed_documents(input)


# ------------------------------------------------------------
# 工厂：根据是否配置通义密钥，返回 (向量对象, 方案名)
# ------------------------------------------------------------
def get_embedding_function():
    if os.getenv("DASHSCOPE_API_KEY", "").strip():
        return QwenEmbedding(), "qwen"
    return LocalHashEmbedding(), "hash"
