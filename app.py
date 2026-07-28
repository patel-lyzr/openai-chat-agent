"""A real OpenAI-backed agent, packaged for the Lyzr control plane.

Speaks the `chat_completion` protocol: the platform gateway POSTs an
OpenAI-shaped Chat Completions body to the service root; we forward the
conversation to OpenAI (with our own system prompt and model choice) and
return the OpenAI-shaped response as-is.

Config (all env):
    OPENAI_API_KEY   — required. Direct mode: a real OpenAI key. Platform
                       mode: a controlplane credential (cpk_… service-account
                       key) — attach it as an environment_variable secret.
    OPENAI_BASE_URL  — optional. Point it at the platform's LLM gateway
                       (https://<console-host>/llm/v1) and every completion
                       routes through controlplane -> Rekori: metered,
                       budgeted, provider keys stay in the platform vault.
                       Unset = talk to api.openai.com directly.
    OPENAI_MODEL     — default gpt-4o-mini. In platform mode use the
                       gateway's namespaced form, e.g. openai-main/gpt-4o-mini.
    SYSTEM_PROMPT    — persona; default below
    PORT             — default 8000 (the platform's runtime envelope sets it)
"""

import ast
import json
import operator
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a concise, helpful assistant for the Lyzr platform. "
    "Answer directly; say so plainly when you don't know.",
)

app = FastAPI(title="openai-chat-agent")
_client: AsyncOpenAI | None = None

# ---------------------------------------------------------------------------
# Local tools — executed IN this agent, no network. Each round-trip through
# the platform's LLM gateway is its own traced request, so a tool-using
# conversation shows up in Request traces as: call 1 (model asks for tools),
# then call 2 with the tool results in the input and the final answer out.
# ---------------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def _calc(node):
    """Arithmetic only — an AST walk, never eval()."""
    if isinstance(node, ast.Expression):
        return _calc(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc(node.left), _calc(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc(node.operand))
    raise ValueError("only arithmetic is supported")


def tool_calculator(expression: str) -> str:
    try:
        return str(_calc(ast.parse(expression, mode="eval")))
    except Exception as exc:
        return f"error: {exc}"


def tool_current_time(timezone_name: str = "UTC") -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def tool_string_stats(text: str) -> str:
    words = text.split()
    return json.dumps({"characters": len(text), "words": len(words),
                       "longest_word": max(words, key=len) if words else ""})


TOOL_IMPLS = {
    "calculator": tool_calculator,
    "current_time": tool_current_time,
    "string_stats": tool_string_stats,
}

TOOLS = [
    {"type": "function", "function": {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression exactly (+ - * / ** %).",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string", "description": "e.g. 23*47"}},
            "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "current_time",
        "description": "The current date and time in UTC.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "string_stats",
        "description": "Character/word counts and the longest word of a text.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
]

MAX_TOOL_ROUNDS = 4


async def run_tool_loop(messages: list[dict], user: str | None):
    """The standard function-calling loop: let the model request local tools,
    execute them here, feed results back, until it answers in prose."""
    kwargs = {"model": MODEL, "tools": TOOLS,
              **({"user": user} if user else {})}
    resp = None
    for _ in range(MAX_TOOL_ROUNDS):
        resp = await client().chat.completions.create(messages=messages, **kwargs)
        choice = resp.choices[0].message
        if not choice.tool_calls:
            return resp
        messages.append({"role": "assistant", "content": choice.content,
                         "tool_calls": [tc.model_dump() for tc in choice.tool_calls]})
        for tc in choice.tool_calls:
            impl = TOOL_IMPLS.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            result = impl(**args) if impl else f"unknown tool: {tc.function.name}"
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "name": tc.function.name, "content": str(result)})
    return resp


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
        # base_url unset -> api.openai.com; set -> the platform's LLM gateway
        _client = AsyncOpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
    return _client


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    """The subset of the Chat Completions request we care about."""

    messages: list[Turn] = Field(min_length=1)
    model: str | None = None  # the platform sends the agent slug; we use OURS
    user: str | None = None
    stream: bool = False


@app.get("/healthz")
async def healthz() -> dict:
    # liveness only — no upstream call, so probes never burn tokens
    return {"ok": True, "model": MODEL,
            "upstream": os.getenv("OPENAI_BASE_URL") or "api.openai.com",
            "tools": sorted(TOOL_IMPLS)}


@app.post("/")
@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        {"role": t.role, "content": t.content} for t in req.messages if t.role != "system"
    ]
    if req.stream:
        # Tools + streaming don't compose simply: the loop must finish before
        # the final text exists. Run the loop, then emit the answer as SSE so
        # streaming clients (the platform playground included) keep working.
        resp = await run_tool_loop(messages, req.user)

        async def sse():
            chunk = {"id": resp.id, "object": "chat.completion.chunk",
                     "created": resp.created, "model": resp.model,
                     "choices": [{"index": 0, "delta": {
                         "role": "assistant",
                         "content": resp.choices[0].message.content or ""},
                         "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            done = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")
    resp = await run_tool_loop(messages, req.user)
    return resp.model_dump()
