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
