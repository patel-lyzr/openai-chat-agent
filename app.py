"""A real OpenAI-backed agent, packaged for the Lyzr control plane.

Speaks the `chat_completion` protocol: the platform gateway POSTs an
OpenAI-shaped Chat Completions body to the service root; we forward the
conversation to OpenAI (with our own system prompt and model choice) and
return the OpenAI-shaped response as-is.

Config (all env):
    OPENAI_API_KEY  — required; on the platform, attach it as an
                      environment_variable secret or a secret:// env ref
    OPENAI_MODEL    — default gpt-4o-mini
    SYSTEM_PROMPT   — persona; default below
    PORT            — default 8000 (the platform's runtime envelope sets it)
"""

import os

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


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
        _client = AsyncOpenAI()
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
    return {"ok": True, "model": MODEL}


@app.post("/")
@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        {"role": t.role, "content": t.content} for t in req.messages if t.role != "system"
    ]
    kwargs = {"model": MODEL, "messages": messages,
              **({"user": req.user} if req.user else {})}
    if req.stream:
        # relay OpenAI's own SSE chunks — the platform gateway passes them
        # through to the caller untouched
        upstream = await client().chat.completions.create(stream=True, **kwargs)

        async def sse():
            async for chunk in upstream:
                yield f"data: {chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")
    resp = await client().chat.completions.create(**kwargs)
    return resp.model_dump()
