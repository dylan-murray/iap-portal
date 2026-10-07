"""Example FastAPI app with streaming — validates SSE / token-by-token responses."""

import asyncio

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from iap_portal.auth import current_user
from iap_portal.auth.fastapi import protect

app = FastAPI(title="fastapi-sse-example")
protect(app, public_paths=["/healthz"])


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    user = current_user()
    return {"hello": user.email, "groups": user.groups}


@app.get("/stream")
async def stream():
    """Simulates an LLM token stream. Validates that proxies don't buffer SSE."""
    user = current_user()

    async def gen():
        for i in range(20):
            yield f"data: token-{i} for {user.email}\n\n"
            await asyncio.sleep(0.2)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
