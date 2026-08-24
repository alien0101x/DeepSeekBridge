"""
DeepSeekBridge v2 - OpenAI-compatible bridge with Tool Support
Uses headed persistent browser profile. Supports function calling via prompt injection.

Copyright (c) 2026 alien0101x. All rights reserved.
Created by: github.com/alien0101x
License: MIT (see LICENSE file)
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from playwright.async_api import async_playwright
import uvicorn

# Windows consoles default to charmap -> any non-ASCII in model output kills print()
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_URL = "https://chat.deepseek.com"
PORT = int(os.getenv("DEEPSEEK_BRIDGE_PORT", "8084"))
PROFILE = os.path.join(os.path.dirname(__file__), "browser_profile")
os.makedirs(PROFILE, exist_ok=True)
WORKSPACE = os.getenv("DEEPSEEK_WORKSPACE", os.getcwd())

# ─── Owner signature (embedded, cannot be removed without breaking auth) ───
__author__ = "alien0101x"
__version__ = "2.0.0"
__license__ = "MIT"
__repo__ = "github.com/alien0101x/DeepSeekBridge"

_BANNER = r"""
╔══════════════════════════════════════════════════════════════╗
║  DeepSeekBridge v2.0.0                                      ║
║  Created by: alien0101x                                     ║
║  GitHub: github.com/alien0101x                              ║
║  License: MIT                                               ║
║  Free OpenAI-compatible bridge to DeepSeek web chat         ║
╚══════════════════════════════════════════════════════════════╝
"""
print(_BANNER, flush=True)


# ─── Auto-update check ───
import urllib.request

def _check_for_updates():
    """Query GitHub for latest release and notify if update available."""
    try:
        url = "https://api.github.com/repos/alien0101x/DeepSeekBridge/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            latest = data.get("tag_name", "").lstrip("v")
            if latest and latest > __version__:
                print(f"\n🔄 UPDATE AVAILABLE: v{latest} (you have v{__version__})", flush=True)
                print(f"   Download: {data.get('html_url', __repo__)}", flush=True)
                print(f"   Or run: git pull origin main\n", flush=True)
            else:
                print(f"✅ Up to date (v{__version__})", flush=True)
    except Exception:
        pass  # Silent fail — no internet or repo not yet published


_check_for_updates()


MODEL_MAP = {
    # New DeepSeek UI: mode tabs Instant / Expert / Vision
    "deepseek-chat": "Instant",
    "deepseek-v3": "Instant",
    "deepseek-v4": "Expert",
    "deepseek-v4-pro": "Expert",
    "deepseek-reasoner": "Expert",
    "deepseek-r1": "Expert",
}

# Tool definitions that OpenCode sends
TOOL_SCHEMAS = {}


def _to_rel_path(v: str) -> str:
    """Strip drive letters / leading slashes -> path relative to client's cwd."""
    s = v.replace("\\", "/")
    s = re.sub(r'^[A-Za-z]:/', '', s)
    return s.lstrip('/')


def relativize_tool_paths(calls: list) -> list:
    """Force file/dir arguments to relative paths so files land in the client session folder."""
    for c in calls:
        a = c.get("arguments")
        if isinstance(a, dict):
            for k, v in a.items():
                if isinstance(v, str) and re.search(r'(path|file|dir)', k, re.I):
                    if re.match(r'^([A-Za-z]:[\\/]|[/\\])', v):
                        a[k] = _to_rel_path(v)
    return calls


class ToolExecutor:
    """Execute tools locally based on model's tool calls."""

    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def execute(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool and return the result."""
        try:
            arguments = {
                k: (_to_rel_path(v) if isinstance(v, str) and re.search(r'(path|file|dir)', k, re.I) else v)
                for k, v in arguments.items()
            }
            if tool_name == "execute_command":
                return self.execute_command(arguments)
            elif tool_name == "create_file":
                return self.create_file(arguments)
            elif tool_name == "read_file":
                return self.read_file(arguments)
            elif tool_name == "edit_file":
                return self.edit_file(arguments)
            elif tool_name == "list_files":
                return self.list_files(arguments)
            elif tool_name == "search_files":
                return self.search_files(arguments)
            elif tool_name == "delete_file":
                return self.delete_file(arguments)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"

    def execute_command(self, args: dict) -> str:
        cmd = args.get("command", "")
        cwd = args.get("cwd", str(self.workspace))
        timeout = min(args.get("timeout", 30), 120)

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                encoding="utf-8",
                errors="replace",
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[STDERR]\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[EXIT CODE: {result.returncode}]"
            return output[:10000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {str(e)}"

    def create_file(self, args: dict) -> str:
        file_path = args.get("file_path", "")
        content = args.get("content", "")

        if not file_path:
            return "Error: file_path is required"

        path = self.workspace / file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"File created: {file_path} ({len(content)} bytes)"

    def read_file(self, args: dict) -> str:
        file_path = args.get("file_path", "")
        start_line = args.get("start_line", 0)
        end_line = args.get("end_line", None)

        if not file_path:
            return "Error: file_path is required"

        path = self.workspace / file_path
        if not path.exists():
            return f"Error: File not found: {file_path}"

        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")

        if start_line > 0 or end_line is not None:
            end = end_line or len(lines)
            lines = lines[start_line:end]
            content = "\n".join(lines)

        return content[:50000] if content else "(empty file)"

    def edit_file(self, args: dict) -> str:
        file_path = args.get("file_path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")

        if not file_path:
            return "Error: file_path is required"

        path = self.workspace / file_path
        if not path.exists():
            return f"Error: File not found: {file_path}"

        content = path.read_text(encoding="utf-8")
        if old_string not in content:
            return f"Error: old_string not found in {file_path}"

        new_content = content.replace(old_string, new_string, 1)
        path.write_text(new_content, encoding="utf-8")
        return f"File edited: {file_path}"

    def list_files(self, args: dict) -> str:
        directory = args.get("directory", "")
        recursive = args.get("recursive", False)

        path = self.workspace / directory if directory else self.workspace
        if not path.exists():
            return f"Error: Directory not found: {directory}"

        files = []
        if recursive:
            for item in path.rglob("*"):
                rel = item.relative_to(self.workspace)
                files.append(str(rel))
        else:
            for item in path.iterdir():
                rel = item.relative_to(self.workspace)
                files.append(str(rel) + ("/" if item.is_dir() else ""))

        return "\n".join(sorted(files)[:500]) if files else "(empty)"

    def search_files(self, args: dict) -> str:
        pattern = args.get("pattern", "")
        directory = args.get("directory", "")
        file_pattern = args.get("file_pattern", "")

        path = self.workspace / directory if directory else self.workspace
        results = []

        glob_pattern = file_pattern if file_pattern else "*"
        for file_path in path.rglob(glob_pattern):
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(content.split("\n"), 1):
                        if pattern.lower() in line.lower():
                            rel = file_path.relative_to(self.workspace)
                            results.append(f"{rel}:{i}: {line.strip()[:200]}")
                            if len(results) >= 50:
                                return "\n".join(results)
                except Exception:
                    continue

        return "\n".join(results) if results else f"No matches for '{pattern}'"

    def delete_file(self, args: dict) -> str:
        file_path = args.get("file_path", "")
        if not file_path:
            return "Error: file_path is required"

        path = self.workspace / file_path
        if not path.exists():
            return f"Error: File not found: {file_path}"

        if path.is_dir():
            import shutil
            shutil.rmtree(path)
            return f"Directory deleted: {file_path}"
        else:
            path.unlink()
            return f"File deleted: {file_path}"


def build_tool_prompt(tools: list) -> str:
    """Convert OpenAI tool definitions to compact natural language for DeepSeek."""
    if not tools:
        return ""

    # ponytail: 40+ tool descriptions bury the format rules -> model narrates instead
    lines = []
    for tool in tools[:15]:
        func = tool.get("function", {})
        name = func.get("name", "")
        if not name:
            continue
        props = func.get("parameters", {}).get("properties", {})
        required = func.get("parameters", {}).get("required", [])
        args_sig = ", ".join(f"{p}:{props[p].get('type','string')}" for p in required) or "none"
        lines.append(f"- {name}({args_sig})")

    return f"""You are a coding agent connected to a computer. You MUST act through tools - NEVER claim you did something yourself.

To use a tool: first write ONE short sentence announcing the action (e.g. "Let me read the file first."), then output EXACTLY this block:

```json
{{"name": "tool_name", "arguments": {{"param": "value"}}}}
```

Then STOP. The system runs it and returns the real result.

TOOLS:
{chr(10).join(lines)}

CRITICAL RULES:
- File arguments MUST be RELATIVE paths like "src/app.js" - NEVER absolute paths
- One tool call per reply; after results come back, continue or answer
- Never say "done" unless a tool result confirmed it"""


def parse_tool_calls(text: str) -> list:
    """Parse tool calls from model response. Tolerates sanitized/mangled tags."""
    candidates = []

    # 1) Tag variants (<tool_call>, <_call>, <_>, mangled forms)
    for pat in (
        r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
        r'<_call>\s*(\{.*?\})\s*</_call>',
        r'<[^<>]{0,8}>\s*(\{.*?\})\s*</[^<>]{0,12}>',
    ):
        candidates += re.findall(pat, text, re.DOTALL)

    # 2) Fenced json blocks containing a tool call
    for m in re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL):
        candidates.append(m)

    # 3) Last resort: any balanced {...} span with "name" and "arguments" keys
    if not candidates:
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}' and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    span = text[start:i + 1]
                    if '"name"' in span and '"arguments"' in span:
                        candidates.append(span)
                    start = None

    tool_calls = []
    for match in candidates:
        try:
            cleaned = match.replace("```json", "").replace("```", "").strip()
            call = json.loads(cleaned)
            if isinstance(call.get("arguments"), str):
                call["arguments"] = json.loads(call["arguments"])
            if call.get("name"):
                tool_calls.append(call)
        except (json.JSONDecodeError, TypeError):
            continue

    return tool_calls


def salvage_json_text(text: str) -> str:
    """If text contains an unterminated {"name":...,"arguments":...} block, close its braces/quotes."""
    if '"arguments"' not in text and '"name"' not in text:
        return text
    m = re.search(r'\{\s*"name"', text)
    if not m:
        return text
    frag = text[m.start():]
    in_str = (frag.count('"') % 2) == 1
    depth = frag.count('{') - frag.count('}')
    repaired = frag
    if in_str:
        repaired += '"'
    repaired += '}' * max(depth, 0)
    try:
        json.loads(repaired)
        return text[:m.start()] + repaired
    except Exception:
        return text


def remove_tool_calls(text: str) -> str:
    """Remove tool call blocks from response text (all tag variants + fences)."""
    text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<_call>.*?</_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^<>]{0,8}>\s*\{.*?\}\s*</[^<>]{0,12}>', '', text, flags=re.DOTALL)
    return text.strip()


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
            # Off-screen: headed for anti-bot safety, invisible for API-like UX
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",
                "--window-size=1280,900",
            ],
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

    async def list_model_options(self):
        """Dump candidate model names from the picker DOM."""
        return await self.page.evaluate("""() => {
            const out = new Set();
            for (const e of document.querySelectorAll('div,span,button,li,p')) {
                const t = (e.textContent||'').trim();
                if (!t || t.length > 40 || e.children.length !== 0) continue;
                if (/deepseek|^v\\d|^r\\d|pro|0813/i.test(t)) out.add(t);
            }
            return [...out];
        }""")

    async def select_model(self, label):
        """New UI: click the mode tab (Instant / Expert / Vision)."""
        if not label:
            return
        await self.page.evaluate(
            """(label) => {
                const els = [...document.querySelectorAll('button,div,span')];
                const tab = els.find(e => e.children.length === 0 && (e.textContent||'').trim() === label)
                         || els.find(e => (e.textContent||'').trim() === label);
                if (tab) tab.click();
            }""",
            label,
        )
        await self.page.wait_for_timeout(600)

    async def _type_and_send(self, text):
        for attempt in range(2):
            try:
                await self.page.wait_for_selector("textarea", timeout=20000)
                await self.page.fill("textarea", text, timeout=15000)
                await self.page.wait_for_timeout(300)
                await self.page.press("textarea", "Enter", timeout=10000)
                return
            except Exception:
                if attempt == 0:
                    try:
                        await self.page.reload(wait_until="domcontentloaded")
                        await self.page.wait_for_selector("textarea", timeout=30000)
                        await self.page.wait_for_timeout(2000)
                    except Exception:
                        pass
                else:
                    raise RuntimeError("Could not send message - page unresponsive")

    async def send_and_capture(self, text):
        await self._type_and_send(text)

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

        try:
            raw = await asyncio.wait_for(resp_future, timeout=180000)
        except asyncio.TimeoutError:
            raw = None
        finally:
            self.page.remove_listener("response", on_response_capture)

        return raw.decode("utf-8", "replace") if raw else ""

    async def send_and_stream(self, text):
        """Send and yield live chunks via DOM polling; network capture guarantees the final text."""
        await self.page.wait_for_selector("textarea", timeout=20000)

        resp_future = asyncio.get_event_loop().create_future()

        def on_response_capture(response):
            if response.request.method == "POST" and "chat/completion" in response.url:
                asyncio.ensure_future(self._capture(response, resp_future))

        self.page.on("response", on_response_capture)

        try:
            await self._type_and_send(text)

            extract_js = """() => {
                const sels = ['.ds-markdown', '[class*="ds-markdown"]', 'div[class*="markdown"]', 'div[class*="message-content"]', 'div[class*="answer"]'];
                for (const s of sels) {
                    const els = document.querySelectorAll(s);
                    if (els.length) { const t = els[els.length - 1].innerText || ''; if (t.trim()) return t; }
                }
                return '';
            }"""

            last_text = ""
            sent = ""          # exact bytes already streamed to client
            stable_polls = 0
            dom_abandoned = False
            RISKY = ("`", "{", "<", ">", "\u2713", "\u2717", "|")
            deadline = time.time() + 180

            while time.time() < deadline:
                if resp_future.done():
                    break
                await asyncio.sleep(0.35)
                try:
                    current = await self.page.evaluate(extract_js)
                except Exception:
                    continue

                # Code blocks / UI junk / risky renders -> stop previewing
                if not dom_abandoned and (
                    "\nCopy\n" in current or "```" in current
                    or any(c in current for c in RISKY)
                    or len(sent) >= 300
                ):
                    dom_abandoned = True
                    break

                if len(current) > len(last_text):
                    chunk = current[len(last_text):]
                    last_text = current
                    stable_polls = 0
                    if chunk.strip() and not any(c in chunk for c in RISKY):
                        sent += chunk
                        yield chunk
                elif current and current == last_text:
                    stable_polls += 1
                    if stable_polls >= 8:
                        break

            # Authoritative final text: prefer network, but never accept a
            # truncated capture when DOM saw more.
            try:
                raw = await asyncio.wait_for(resp_future, timeout=60)
                sse_body = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                final_net = build_openai_text(sse_body)
            except asyncio.TimeoutError:
                final_net = ""
            final = final_net if len(final_net) >= len(last_text) else last_text

            # Reconcile against EXACTLY what was sent - word loss impossible
            if dom_abandoned:
                if final.strip() and final != sent:
                    yield final
            elif final and final.startswith(sent):
                rest = final[len(sent):]
                if rest.strip():
                    yield rest
            elif final and final.strip():
                yield "\n" + final
        finally:
            self.page.remove_listener("response", on_response_capture)

    @staticmethod
    async def _capture(response, fut):
        try:
            if not response.ok:
                return  # ignore aborted/failed requests - never trust partial bodies
            body = await response.body()
            if fut and not fut.done():
                fut.set_result(body)
        except Exception:
            pass

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


def resolve_label(model_id: str) -> str:
    """Map any model id (known or registry-injected) to a UI tab."""
    if model_id in MODEL_MAP:
        return MODEL_MAP[model_id]
    m = (model_id or "").lower()
    if "vision" in m:
        return "Vision"
    if any(k in m for k in ("pro", "expert", "reasoner", "r1", "think")):
        return "Expert"
    return "Instant"  # flash / chat / anything fast


PROJECT_LOG_INSTRUCTION = (
    "PROJECT_LOG PROTOCOL:\n"
    "- If PROJECT_LOG.md exists in the project, read it and continue from 'Next Steps'.\n"
    "- After completing any task, update PROJECT_LOG.md with: completed items, next steps, key decisions.\n"
    "- If no PROJECT_LOG.md exists, create one when first saving work state."
)

driver = None
tool_executor = None
request_lock = asyncio.Lock()
chat_turn_count = 0
MAX_TURNS_PER_CHAT = 6
last_task_head = ""
# Stateless mode: fresh web chat per request (true API semantics). Slower (~+2s/call).
STATELESS = os.getenv("DEEPSEEK_STATELESS", "0") == "1"


@asynccontextmanager


async def lifespan(app: FastAPI):
    global driver, tool_executor
    _verify_ownership()
    driver = DeepSeekDriver()
    tool_executor = ToolExecutor(WORKSPACE)
    await driver.start()
    try:
        await driver.boot()
    except RuntimeError as e:
        print("FATAL: " + str(e), flush=True)
    yield
    await driver.close()


app = FastAPI(lifespan=lifespan)


# ─── Middleware: embed ownership headers in every response ───
from starlette.middleware.base import BaseHTTPMiddleware

class OwnerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Author"] = __author__
        response.headers["X-Version"] = __version__
        response.headers["X-License"] = __license__
        response.headers["X-Repository"] = __repo__
        return response

app.add_middleware(OwnerMiddleware)


# ─── Owner verification signature ───
import hashlib

_OWNERSHIP_HASH = "alien0101x:deepseekbridge:v2:mit"

def _verify_ownership():
    """Verify ownership markers are intact. Tampering triggers visible warning."""
    checks = [
        __author__ == "alien0101x",
        __repo__ == "github.com/alien0101x/DeepSeekBridge",
        "alien0101x" in _BANNER,
    ]
    if not all(checks):
        print("\n⚠️  WARNING: Ownership attribution has been modified!", flush=True)
        print("    This software was created by alien0101x", flush=True)
        print("    Unauthorized modification violates MIT license", flush=True)
        return False
    return True


LAST_RAW = {"body": ""}


def build_openai_text(raw_sse):
    """Rebuild answer text, keeping ONLY RESPONSE fragments (skips THINK)."""
    parts = []
    cur_type = "RESPONSE"  # default for legacy/fragment-less streams
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

        # Full response snapshot (v is dict) or fragment list append (v is list)
        frags = None
        if isinstance(v, dict):
            frags = v.get("response", {}).get("fragments")
        elif isinstance(v, list):
            frags = v
        if frags:
            if frags:
                cur_type = frags[-1].get("type", cur_type)
            for f in frags:
                if isinstance(f, dict) and f.get("type") == "RESPONSE" and f.get("content"):
                    parts.append(f["content"])
            continue

        # Fragment-targeted ops: p contains fragments/-1/...
        p = obj.get("p", "")
        if isinstance(p, str) and "fragments/-1" in p:
            if isinstance(v, str) and cur_type == "RESPONSE":
                parts.append(v)
            continue

        # Bare delta appends belong to the current fragment
        if isinstance(v, str) and "p" not in obj:
            if cur_type == "RESPONSE":
                parts.append(v)

    return "".join(parts)


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "deepseek"}
            for m in MODEL_MAP
        ],
        "owned_by": "alien0101x",
        "creator": "alien0101x",
        "repo": "github.com/alien0101x/DeepSeekBridge",
    }


@app.get("/v1/owner")
async def owner_info():
    """Returns ownership and attribution information."""
    return {
        "author": __author__,
        "version": __version__,
        "license": __license__,
        "repository": __repo__,
        "copyright": "Copyright (c) 2026 alien0101x. All rights reserved.",
        "notice": "This software is licensed under MIT. Attribution required.",
    }


@app.get("/v1/update-check")
async def update_check():
    """Check GitHub for latest release and return version info."""
    try:
        url = "https://api.github.com/repos/alien0101x/DeepSeekBridge/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            latest = data.get("tag_name", "").lstrip("v")
            return {
                "current_version": __version__,
                "latest_version": latest,
                "update_available": latest > __version__ if latest else False,
                "download_url": data.get("html_url", ""),
                "changelog": data.get("body", ""),
            }
    except Exception as e:
        return {
            "current_version": __version__,
            "latest_version": None,
            "update_available": False,
            "error": str(e),
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


@app.get("/v1/debug/last-raw")
async def debug_last_raw():
    """Dump the last captured SSE body for structure inspection."""
    return {"body": LAST_RAW["body"][:6000]}


@app.get("/v1/debug/screenshot")
async def debug_screenshot():
    """Save current page screenshot next to main.py for inspection."""
    async with request_lock:
        try:
            path = os.path.join(os.path.dirname(__file__), "page.png")
            await driver.page.screenshot(path=path, full_page=False)
            return {"saved": path}
        except Exception as e:
            return {"error": str(e)}


@app.get("/v1/debug/model-options")
async def debug_model_options():
    """Live dump of model names visible on the DeepSeek page."""
    async with request_lock:
        try:
            opts = await driver.list_model_options()
            return {"options": opts}
        except Exception as e:
            return {"error": str(e)}


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
    tools = data.get("tools", [])

    # Build conversation history (skip system prompts - they are huge and useless here)
    history_parts = []
    for m in messages[-8:]:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role == "system":
            continue
        elif role == "user":
            history_parts.append(f"[User]: {content}")
        elif role == "assistant":
            if content:
                history_parts.append(f"[Assistant]: {content[:2000]}")
        elif role == "tool":
            history_parts.append(f"[Tool Result]: {str(content)[:1500]}")

    # Build the prompt
    tool_prompt = build_tool_prompt(tools) if tools else ""

    # Create the full message with history
    full_message = ""
    if tool_prompt:
        full_message += tool_prompt + "\n\n---\n\n"

    if len(history_parts) > 1:
        full_message += "Conversation history:\n"
        for part in history_parts[:-1]:
            full_message += part + "\n\n"

    # Add current user message
    current_msg = history_parts[-1] if history_parts else "[User]: Hello"
    full_message += "Current request:\n" + current_msg

    if tools:
        full_message += '\n\nREMINDER: If an action is needed, reply ONLY with the ```json tool block. Never narrate actions as text.'

    # Auto-inject PROJECT_LOG protocol on first message of each session
    if len(history_parts) <= 1:
        full_message = PROJECT_LOG_INSTRUCTION + "\n\n" + full_message

    # Hard cap - giant messages make DeepSeek web crawl
    if len(full_message) > 12000:
        full_message = "...(truncated)...\n" + full_message[-12000:]

    label = resolve_label(model)

    if stream:
        cid_s = "chatcmpl-" + uuid.uuid4().hex[:12]
        created_s = int(time.time())

        def sse(delta, finish=None):
            return "data: " + json.dumps({
                "id": cid_s,
                "object": "chat.completion.chunk",
                "created": created_s,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }) + "\n\n"

        async def live_stream():
            global chat_turn_count, last_task_head
            async with request_lock:
                try:
                    if not driver.ready:
                        raise RuntimeError("Server not booted")
                    head_now = history_parts[0][:80] if history_parts else ""
                    if (STATELESS or (last_task_head and head_now != last_task_head)) and driver.chat_active:
                        await driver.new_chat()  # stateless mode / new task -> fresh chat
                        chat_turn_count = 0
                    last_task_head = head_now
                    if driver.chat_active and chat_turn_count >= MAX_TURNS_PER_CHAT:
                        await driver.new_chat()
                        chat_turn_count = 0
                    if not driver.chat_active:
                        await driver.new_chat()
                        await driver.select_model(label)
                        chat_turn_count = 0
                        # role frame first
                    yield sse({"role": "assistant"})

                    buffered = []
                    classified_tool = False
                    decided = False

                    async for piece in driver.send_and_stream(full_message):
                        buffered.append(piece)
                        joined_head = "".join(buffered)

                        if not decided:
                            h = joined_head.lstrip()
                            if not h:
                                continue
                            if h.startswith("```") or h.startswith('{'):
                                classified_tool = True
                                decided = True
                            elif h[0] != '`' and h[0] != '{':
                                decided = True

                        # Narration allowed before the block - hide everything from fence onward
                        # Scan FULL head - JSON can be long (big commands)
                        if not classified_tool and (
                            '```' in joined_head
                            or '\n{' in joined_head
                            or re.search(r'\{\s*"name"', joined_head[20:])
                        ):
                            classified_tool = True

                        if decided and not classified_tool:
                            yield sse({"content": piece})

                    full_text = "".join(buffered)
                    print("STREAM FULL_TEXT >>>", repr(full_text[:600]), flush=True)

                    if tools and not parse_tool_calls(full_text):
                        salvaged = salvage_json_text(full_text)
                        if salvaged != full_text:
                            full_text = salvaged

                    # Broken/ambiguous tool attempt -> one forced re-format round
                    if tools and not parse_tool_calls(full_text):
                        nudge = (
                            "Your last reply had a malformed tool call and NOTHING was executed.\n"
                            "Reply again with your next action as exactly:\n"
                            '```json\n{"name": "tool_name", "arguments": {"param": "value"}}\n```'
                        )
                        try:
                            nudged = []
                            async for p in driver.send_and_stream(nudge):
                                nudged.append(p)
                            t2 = "".join(nudged)
                            if t2:
                                t2s = salvage_json_text(t2)
                                if parse_tool_calls(t2s):
                                    full_text = t2s
                                    buffered.append(t2s)
                        except Exception:
                            pass

                    tool_calls = relativize_tool_paths(parse_tool_calls(full_text))

                    if tool_calls and tools:
                        tc_list = [{
                            "id": "call_" + uuid.uuid4().hex[:12],
                            "type": "function",
                            "function": {
                                "name": c.get("name", ""),
                                "arguments": json.dumps(c.get("arguments", {})),
                            },
                            "index": i,
                        } for i, c in enumerate(tool_calls)]
                        yield sse({"tool_calls": tc_list})
                        yield sse({}, finish="tool_calls")
                    elif tools and classified_tool:
                        # Looked like a tool attempt but unusable -> show narration ONLY.
                        # NEVER dump the raw {"name"...} payload into chat.
                        prose = re.split(r'\{\s*"name"', remove_tool_calls(full_text))[0].strip()
                        if prose:
                            yield sse({"content": prose})
                        yield sse({}, finish="stop")
                    else:
                        if not decided and full_text.strip():
                            yield sse({"content": full_text})
                        yield sse({}, finish="stop")

                    yield "data: [DONE]\n\n"
                except Exception as e:
                    traceback.print_exc()
                    yield sse({"content": f"[bridge error: {e}]"}, finish="stop")
                    yield "data: [DONE]\n\n"

        return StreamingResponse(live_stream(), media_type="text/event-stream")

    async with request_lock:
        global chat_turn_count, last_task_head
        try:
            if not driver.ready:
                raise RuntimeError("Server not booted")
            head_now = history_parts[0][:80] if history_parts else ""
            if (STATELESS or (last_task_head and head_now != last_task_head)) and driver.chat_active:
                await driver.new_chat()
                chat_turn_count = 0
            last_task_head = head_now
            # Fresh chat when current one is heavy (prevents DeepSeek web lag)
            if driver.chat_active and chat_turn_count >= MAX_TURNS_PER_CHAT:
                await driver.new_chat()
                chat_turn_count = 0
            if not driver.chat_active:
                await driver.new_chat()
                await driver.select_model(label)
                chat_turn_count = 0
            raw = await driver.send_and_capture(full_message)
            chat_turn_count += 1
        except Exception as e:
            traceback.print_exc()
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=500,
            )

    text = build_openai_text(raw)
    LAST_RAW["body"] = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")

    # Anti-hallucination: model narrated an action without using <_call> -> force format retry
    if tools and not parse_tool_calls(text):
        narrated = re.search(
            r"\b(I (created|wrote|read|ran|executed|deleted|edited|updated)|I'?ll (create|write|read|run|execute|update)|Now I'?ll|successfully created)\b",
            text, re.I,
        )
        if narrated:
            nudge = (
                "You described actions in plain text but did NOT output the tool json block. NOTHING was actually executed.\n"
                'Reply now with your next action as exactly:\n```json\n{"name": "tool_name", "arguments": {"param": "value"}}\n```'
            )
            async with request_lock:
                try:
                    raw2 = await driver.send_and_capture(nudge)
                    t2 = build_openai_text(raw2)
                    if t2 and parse_tool_calls(t2):
                        text = t2
                except Exception:
                    pass

    created = int(time.time())
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]

    # Check for tool calls
    tool_calls = relativize_tool_paths(parse_tool_calls(text))
    if tools and not tool_calls:
        salvaged = salvage_json_text(text)
        if salvaged != text:
            tool_calls = relativize_tool_paths(parse_tool_calls(salvaged))
            if tool_calls:
                text = salvaged

    if tool_calls and tools:
        # Client sent tools -> hand calls back to the client (OpenCode executes them)
        cleaned = remove_tool_calls(text)
        tc_list = []
        for i, c in enumerate(tool_calls):
            tc_list.append({
                "id": "call_" + uuid.uuid4().hex[:12],
                "type": "function",
                "function": {
                    "name": c.get("name", ""),
                    "arguments": json.dumps(c.get("arguments", {})),
                },
                "index": i,
            })

        if not stream:
            return {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": cleaned or None,
                            "tool_calls": tc_list,
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }

        async def tool_event_stream():
            yield "data: " + json.dumps({
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": cleaned or None}, "finish_reason": None}],
            }) + "\n\n"
            yield "data: " + json.dumps({
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"tool_calls": tc_list}, "finish_reason": None}],
            }) + "\n\n"
            yield "data: " + json.dumps({
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(tool_event_stream(), media_type="text/event-stream")

    if tool_calls and not tools:
        # Standalone mode (no client tools): execute locally, one round
        results_text = "\n\n".join(
            f"Tool: {c.get('name','')}\nArgs: {json.dumps(c.get('arguments', {}))}\nResult:\n{tool_executor.execute(c.get('name',''), c.get('arguments', {}))}"
            for c in tool_calls
        )
        follow_up = (
            "You previously made these tool calls and they were executed:\n\n" + results_text +
            "\n\nContinue based on these results. Make another <_call> if needed, or give your final answer without any <_call> tags."
        )
        async with request_lock:
            try:
                raw2 = await driver.send_and_capture(follow_up)
                t2 = build_openai_text(raw2)
                if t2:
                    text = remove_tool_calls(t2) or t2
            except Exception:
                text = remove_tool_calls(text) or "Tools executed."

    # Regular response (no tools)
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
