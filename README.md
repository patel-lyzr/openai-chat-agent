# openai-chat-agent

A real OpenAI-backed agent packaged for the Lyzr control plane. FastAPI,
one endpoint, speaks the `chat_completion` protocol: POST an OpenAI-shaped
Chat Completions body to `/` (or `/v1/chat/completions`), get an
OpenAI-shaped response. `GET /healthz` for probes.

The platform never builds this — CI here produces the image (GHCR), and
the CD step calls the control plane's deploy hook with the image URI and
commit. The platform mints a version and deploys it through the
environment flow.

## Run locally

```bash
export OPENAI_API_KEY=sk-...
pip install -r requirements.txt
uvicorn app:app --port 8000

curl -s localhost:8000/ -H 'content-type: application/json' \
  -d '{"messages": [{"role": "user", "content": "hello"}]}'
```

Or the container:

```bash
docker build -t openai-chat-agent .
docker run --rm -p 8000:8000 -e OPENAI_API_KEY=$OPENAI_API_KEY openai-chat-agent
```

## Deploy on the control plane

1. **Save the OpenAI key once** — Secrets → new secret `openai-key`, kind
   `environment_variable`, env name `OPENAI_API_KEY`.
2. **Register the agent** — Register agent → *Docker image*:
   - Name: `openai-chat`
   - Git repository: this repo's URL (provenance)
   - Image URI: leave empty — releases come from CI
   - Protocol: `chat_completion` · Port: `8000` · Health path: `/healthz`
3. **Arm CI** — repo secrets `CONTROL_PLANE_URL` (the API base) and
   `CP_API_KEY` (a `cpk_…` service-account key), plus a repo variable
   `AGENT=openai-chat`. Every push to main then builds, pushes to GHCR,
   and releases to dev; eval-gated auto-promotion and the prod approval
   gate take it from there.
4. **Deploy manually instead** — skip step 3 and call the hook yourself:

```bash
curl -X POST $CONTROL_PLANE_URL/agents/openai-chat/deploy \
  -H "Authorization: Bearer cpk_..." -H 'content-type: application/json' \
  -d '{"stage": "dev", "image_uri": "ghcr.io/<owner>/openai-chat-agent:<sha>",
       "git_sha": "<sha>", "confirm": true}'
```

When attaching the secret to the deployment (or using a `secret://` env
ref), the platform resolves the value server-side into the pod — the key
never appears in this repo, the registry, or the deployment spec.

> GHCR note: make the package public (or add a pull secret on the
> cluster) so Kubernetes can pull the image.


## Routing through the platform's LLM gateway

Instead of holding a raw OpenAI key, point the agent at the control plane's
LLM gateway — completions are then metered, budgeted, and traced by the
platform, and the real provider key never leaves its vault:

| Env | Value |
|---|---|
| `OPENAI_BASE_URL` | `https://controlplane.test.studio.lyzr.ai/llm/v1` |
| `OPENAI_API_KEY` | a `cpk_…` service-account key (Settings → Service accounts) |
| `OPENAI_MODEL` | `openai-main/gpt-4o-mini` (the gateway's `provider/model` form) |

When the agent is deployed BY the platform, set these in the runtime env
(key via an `environment_variable` secret) and use the in-cluster base URL
`http://control-plane.control-plane.svc.cluster.local:8081/llm/v1` to skip
the public hop.


## Local tools

The agent carries three local tools — `calculator` (AST-walked arithmetic,
never eval), `current_time`, and `string_stats` — behind the standard
function-calling loop (max 4 rounds). Ask "what is 23*47?" or "what time is
it?" and the model calls the tool, gets the result fed back, and answers.

Routed through the platform's LLM gateway, every round-trip is its own
traced request: Request traces shows call 1 (the model asking for the tool)
and call 2 (tool results in, final answer out), tokens and cost per hop.
