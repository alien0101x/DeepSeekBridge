"""
DeepSeekBridge - OpenAI-compatible bridge to chat.deepseek.com
Uses headed persistent browser profile. No route interception.
"""

import asyncio
import json
import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from playwright.async_api import async_playwright
import uvicorn

BASE_URL = "https://chat.deepseek.com"
PORT = int(os.getenv("DEEPSEEK_BRIDGE_PORT", "8084"))
PROFILE = os.path.join(os.path.dirname(__file__), "browser_profile")

MODEL_MAP = {
    "deepseek-chat": "DeepSeek-V3",
    "deepseek-v3": "DeepSeek-V3",
    "deepseek-reasoner": "DeepSeek-R1",
    "deepseek-r1": "DeepSeek-R1",
}


class DeepSeekDriver:
    def __init__(self):
        self.pw = None
        self.ctx = None
        self.page = None
        self.ready = False
        self.chat_active = False

    async def start(self):
        self.pw = await async_playwright().__aenter__()
        self.ctx = await self.pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

    async def boot(self):
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        await self.page.goto(BASE_URL, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(8000)

        if "/sign_in" in self.page.url:
            try:
                await self.page.wait_for_selector("textarea", timeout=300000)
            except Exception:
                raise RuntimeError("Login timeout")

        await self.page.wait_for_selector("textarea", timeout=30000)
        self.ready = True
        print("BOOT: success", flush=True)

    async def new_chat(self):
        await self.page.evaluate("""() => {
            const els = [...document.querySelectorAll('a, div, span, button')];
            const nc = els.find(e => (e.textContent||'').trim() === 'New chat');
            if (nc) nc.click();
        }""")
        await self.page.wait_for_timeout(2000)
        if "/sign_in" in self.page.url:
            raise RuntimeError("Session expired - signed out")
        await self.page.wait_for_selector("textarea", timeout=15000)
        await self.page.wait_for_timeout(500)
        self.chat_active = True

    async def list_chats(self):
        chats = await self.page.evaluate("""() => {
            const sidebar = document.querySelector('[class*="sidebar"]') || document.querySelector('nav');
            if (!sidebar) return [];
            const items = [...sidebar.querySelectorAll('a, div, span')];
            return items
                .filter(e => {
                    const t = (e.textContent || '').trim();
                    return t.length > 3 && t.length < 100 && !t.includes('New chat') && !t.includes('Settings');
                })
                .map((e, i) => ({
                    id: i,
                    title: (e.textContent || '').trim().substring(0, 60)
                }))
                .slice(0, 20);
        }""")
        return chats

    async def switch_chat(self, chat_index):
        result = await self.page.evaluate("""(idx) => {
            const sidebar = document.querySelector('[class*="sidebar"]') || document.querySelector('nav');
            if (!sidebar) return false;
            const items = [...sidebar.querySelectorAll('a, div, span')];
            const clickable = items.filter(e => {
                const t = (e.textContent || '').trim();
                return t.length > 3 && t.length < 100 && !t.includes('New chat') && !t.includes('Settings');
            });
            if (idx < clickable.length) {
                clickable[idx].click();
                return true;
            }
            return false;
        }""", chat_index)
        if result:
            await self.page.wait_for_timeout(2000)
            await self.page.wait_for_selector("textarea", timeout=15000)
            self.chat_active = True
        return result

    async def select_model(self, label):
        if not label:
            return
        await self.page.evaluate(
            """(label) => {
                const els=[...document.querySelectorAll('button,div,span')];
                const trig=els.find(e=>(e.textContent||'').includes('DeepSeek'));
                if(trig) trig.click();
            }""",
            label,
        )
        await self.page.wait_for_timeout(500)
        await self.page.evaluate(
            """(label) => {
                const els=[...document.querySelectorAll('button,div,span')];
                const opt=els.find(e=>(e.textContent||'').trim()===label);
                if(opt) opt.click();
            }""",
            label,
        )
        await self.page.wait_for_timeout(500)

    async def send_and_capture(self, text):
        await self.page.wait_for_selector("textarea", timeout=60000)

        resp_future = asyncio.get_event_loop().create_future()

        def on_response_capture(response):
            if response.request.method == "POST" and "chat/completion" in response.url:
                asyncio.ensure_future(capture(response))

        async def capture(response):
            try:
                body = await response.body()
                if resp_future and not resp_future.done():
                    resp_future.set_result(body)
            except Exception:
                pass

        self.page.on("response", on_response_capture)

        await self.page.fill("textarea", text)
        await self.page.wait_for_timeout(300)
        await self.page.press("textarea", "Enter")

        try:
            raw = await asyncio.wait_for(resp_future, timeout=120000)
        except asyncio.TimeoutError:
            raw = None
        finally:
            self.page.remove_listener("response", on_response_capture)

        return raw.decode("utf-8", "replace") if raw else ""

    async def close(self):
        try:
            if self.ctx:
                await self.ctx.close()
        except Exception:
            pass
        try:
            if self.pw:
                await self.pw.stop()
        except Exception:
            pass


driver = None
request_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global driver
    driver = DeepSeekDriver()
    await driver.start()
    try:
        await driver.boot()
    except RuntimeError as e:
        print("FATAL: " + str(e), flush=True)
    yield
    await driver.close()


app = FastAPI(lifespan=lifespan)


def build_openai_text(raw_sse):
    events = []
    for line in raw_sse.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload in ("", "[DONE]"):
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        v = obj.get("v")
        if v is None:
            continue
        if isinstance(v, str):
            if "p" not in obj:
                events.append(v)
            continue
        if isinstance(v, dict):
            resp = v.get("response", {})
            frags = resp.get("fragments") or []
            for frag in frags:
                c = frag.get("content", "")
                if c:
                    events.append(c)
    return "".join(events)


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "deepseek"}
            for m in MODEL_MAP
        ],
    }


@app.post("/v1/reset")
async def reset_chat():
    driver.chat_active = False
    return {"status": "ok"}


@app.get("/v1/chats")
async def get_chats():
    async with request_lock:
        chats = await driver.list_chats()
    return {"chats": chats}


@app.post("/v1/chats/switch")
async def switch_chat(request: Request):
    body = await request.json()
    chat_index = body.get("chat_index", 0)
    async with request_lock:
        result = await driver.switch_chat(chat_index)
    return {"success": result}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        return Response(content=json.dumps({"error": "invalid json"}), status_code=400)

    messages = data.get("messages", [])
    model = data.get("model", "deepseek-chat")
    stream = bool(data.get("stream", False))

    # Get last user message only (fast, no lag)
    prompt = ""
    for m in reversed(messages):
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        if m.get("role") == "user" and c:
            prompt = c
            break
    if not prompt:
        prompt = messages[-1].get("content", "") if messages else ""

    label = MODEL_MAP.get(model, MODEL_MAP["deepseek-chat"])

    async with request_lock:
        try:
            if not driver.ready:
                raise RuntimeError("Server not booted")
            if not driver.chat_active:
                await driver.new_chat()
                await driver.select_model(label)
            raw = await driver.send_and_capture(prompt)
        except Exception as e:
            traceback.print_exc()
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=500,
            )

    text = build_openai_text(raw)
    created = int(time.time())
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]

    if not stream:
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    async def event_stream():
        yield "data: " + json.dumps({
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }) + "\n\n"
        yield "data: " + json.dumps({
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }) + "\n\n"
        yield "data: " + json.dumps({
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
