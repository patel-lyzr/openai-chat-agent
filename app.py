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
async def chat(req: ChatRequest) -> dict:
    if req.stream:
        raise HTTPException(status_code=400, detail="streaming is not supported")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        {"role": t.role, "content": t.content} for t in req.messages if t.role != "system"
    ]
    resp = await client().chat.completions.create(
        model=MODEL,
        messages=messages,
        **({"user": req.user} if req.user else {}),
    )
    return resp.model_dump()
