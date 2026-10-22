# -*- coding: utf-8 -*-
"""OpenAI 兼容的 chat 调用（DeepSeek 官方、中转站、本地 vLLM / Ollama 都走这套）。

只做一件事：发 messages、拿回文本。错误翻译成人话，前端能直接弹给用户看，
不用让人对着一串 401/404 猜是 key 错了还是模型名错了。
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"


class AgentError(RuntimeError):
    """模型调用失败。`hint` 是给人看的下一步动作。"""

    def __init__(self, message: str, *, hint: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.hint = hint
        self.status = status


def endpoint_of(base_url: str) -> str:
    """把用户填的 base_url 归一成 chat/completions 地址。

    允许三种写法：
      https://api.deepseek.com
      https://api.deepseek.com/v1
      https://xxx/v1/chat/completions（原样用）
    """
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _explain(status: int, body: str) -> AgentError:
    text = (body or "").strip()
    if len(text) > 300:
        text = text[:300] + "…"
    if status == 401:
        return AgentError(
            "密钥无效或已过期（401）",
            hint="到设置 → 模型 重新填 API Key。注意别把前后空格一起粘进去。",
            status=status,
        )
    if status == 402:
        return AgentError("账户余额不足（402）", hint="到服务商后台充值后再试。", status=status)
    if status == 404:
        return AgentError(
            f"接口或模型不存在（404）：{text}",
            hint="多半是模型名不对。DeepSeek 官方常用 deepseek-chat / deepseek-reasoner；"
            "换了中转站要先确认它支持的模型 id。",
            status=status,
        )
    if status == 429:
        return AgentError("被限流了（429）", hint="等几十秒再试，或降低并发。", status=status)
    if status >= 500:
        return AgentError(f"服务端错误（{status}）：{text}", hint="稍后重试；持续报错就换个 base_url。", status=status)
    return AgentError(f"请求被拒绝（{status}）：{text}", hint="检查 base_url 与模型名。", status=status)


def _text_of(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # 少数网关返回分段内容
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


async def chat(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 120.0,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    if not api_key:
        raise AgentError("还没填 API Key", hint="到设置 → 模型 填一把，保存后再点测试连接。")

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens

    url = endpoint_of(base_url)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.ConnectError as exc:
        raise AgentError(
            f"连不上 {url}",
            hint="检查 base_url 是否写对（要带 http:// 或 https://），以及本机网络/代理。",
        ) from exc
    except httpx.TimeoutException as exc:
        raise AgentError(
            f"等了 {timeout:.0f} 秒没有响应",
            hint="模型出词慢或网络抖动；可以到设置里把超时调大，或缩短指令。",
        ) from exc
    except httpx.HTTPError as exc:  # 其它传输层错误
        raise AgentError(f"请求失败：{exc}") from exc

    if resp.status_code != 200:
        raise _explain(resp.status_code, resp.text)

    try:
        payload = resp.json()
    except ValueError as exc:
        raise AgentError("返回的不是 JSON，可能被代理拦截了", hint="检查 base_url 是否指向了网页而不是 API。") from exc

    text = _text_of(payload)
    if not text.strip():
        raise AgentError("模型返回了空内容", hint="换个指令再试；若持续为空，检查模型名是否为对话模型。")

    usage = payload.get("usage") or {}
    return {
        "text": text,
        "model": payload.get("model") or model,
        "usage": {
            "prompt": usage.get("prompt_tokens"),
            "completion": usage.get("completion_tokens"),
        },
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


async def ping(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """连通性测试：一句最小对话，能回字就说明 key / 模型 / 地址三者都对。"""
    result = await chat(
        [
            {"role": "system", "content": "只回答一个字。"},
            {"role": "user", "content": "在吗"},
        ],
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        temperature=0.0,
        max_tokens=8,
    )
    return {
        "ok": True,
        "model": result["model"],
        "latency_ms": result["latency_ms"],
        "sample": result["text"].strip()[:20],
    }
