"""
============================================================
LLM 客户端封装（阶段2）：DeepSeek（OpenAI 兼容接口）
============================================================
【密钥安全】密钥只从后端环境变量 / .env 读取，前端永远拿不到。
【配置项（写在 backend/.env）】
  DEEPSEEK_API_KEY=sk-xxxx            你的 DeepSeek 密钥（必填）
  DEEPSEEK_BASE_URL=https://api.deepseek.com   一般不用改
  DEEPSEEK_MODEL=deepseek-flash       模型名（flash 快且便宜；可选 deepseek-v4-pro）

【设计】
  · stream_chat()  流式生成器：逐字 yield 回答片段（供 SSE 推送）
  · chat_complete() 非流式：一次性返回完整回答（用于流式失败降级）
  · 未配置密钥 / 调用失败时抛出带明确信息的异常，由路由层转成 error 事件，
    保证前端不会白屏或崩溃。
============================================================
"""

import os
from openai import OpenAI

# 模块级缓存客户端，避免每次请求重复初始化
_client = None


def _get_client() -> OpenAI:
    """懒加载 DeepSeek 客户端；未配置密钥时抛出明确错误。"""
    global _client
    if _client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "未配置 DEEPSEEK_API_KEY：请在 backend/.env 填入密钥后重启后端"
            )
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def _model() -> str:
    """读取模型名，默认 deepseek-flash（快、便宜，适合演示；可选 deepseek-v4-pro）。"""
    return os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash"


def is_configured() -> bool:
    """供接口判断是否已配置密钥（未配置时前端走友好提示，不发起无效调用）。"""
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


def stream_chat(messages: list):
    """
    流式对话生成器：逐段 yield 回答文本。
    messages: [{"role":"system"/"user","content":"..."}]
    调用方（SSE 路由）逐段转发给前端。
    """
    client = _get_client()
    stream = client.chat.completions.create(
        model=_model(),
        messages=messages,
        stream=True,
        temperature=0.3,          # 偏低，让政策回答更稳、少发挥
    )
    for chunk in stream:
        # 每个 chunk 的 choices[0].delta.content 可能为 None（结束标记）
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def chat_complete(messages: list) -> str:
    """非流式一次性回答：流式失败时的降级兜底。"""
    client = _get_client()
    resp = client.chat.completions.create(
        model=_model(),
        messages=messages,
        temperature=0.3,
        stream=False,
    )
    return resp.choices[0].message.content or ""
