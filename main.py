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
    # New DeepSeek UI: mode tabs Instant / Expert / Vision, dropdown shows DeepSeek V3.2
    "deepseek-chat": "Instant",
    "deepseek-v3": "Instant",
    "deepseek-v3.2": "Instant",
    "deepseek-v4": "Expert",
    "deepseek-v4-pro": "Expert",
    "deepseek-reasoner": "Expert",
    "deepseek-r1": "Expert",
}

# Tool definitions that AI agents send
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
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {}
            arguments = {
                k: (_to_rel_path(v) if isinstance(v, str) and re.search(r'(path|file|dir)', k, re.I) else v)
                for k, v in arguments.items()
            }
            # Map common client tool names to internal names
            TOOL_MAP = {
                "write": "create_file",
                "bash": "execute_command",
                "read": "read_file",
                "edit": "edit_file",
                "glob": "list_files",
                "grep": "search_files",
                "ls": "list_files",
            }
            tool_name = TOOL_MAP.get(tool_name, tool_name)

            # Normalize argument names (clients send camelCase, internals use snake_case)
            ARG_ALIASES = {"filePath": "file_path", "filepath": "file_path", "path": "file_path",
                           "cmd": "command", "query": "pattern"}
            arguments = {ARG_ALIASES.get(k, k): v for k, v in arguments.items()}

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

    # Dynamic example built from a REAL tool so the model can't parrot placeholders.
    # Prefer a simple tool; use required args only, with type-correct placeholders
    # (a copied wrong-typed value like "timeout": "..." fails client schemas).
    def _pick_example_tool():
        best = None
        for t in tools[:5]:
            f = t.get("function", {}) or {}
            params = f.get("parameters", {}) or {}
            props = params.get("properties", {}) or {}
            req = params.get("required", []) or []
            if req and all((props.get(k, {}).get("type", "string") == "string") for k in req):
                return f, props, req
            if best is None and f.get("name"):
                best = (f, props, req)
        return best if best else (tools[0].get("function", {}) or {}, {}, [])

    ex_func, ex_props, ex_req = _pick_example_tool()
    ex_name = ex_func.get("name", "")
    keys = [k for k in ex_req if k in ex_props] or list(ex_props.keys())[:1]
    ex_args = {}
    for k in keys:
        ptype = ex_props.get(k, {}).get("type", "string")
        ex_args[k] = 1 if ptype in ("number", "integer") else (True if ptype == "boolean" else "...")
    example = json.dumps({"name": ex_name, "arguments": ex_args})

    return f"""YOU ARE AN AI AGENT WITH TOOL ACCESS. You MUST use tools to complete tasks.

FORMAT (MANDATORY for every action):
STEP 1 - NARRATE: Write 1-2 sentences explaining what you will do.
STEP 2 - ACT: Include the JSON tool call block BELOW your narration. This is REQUIRED, not optional.

Example shape (use the real tool "{ex_name}"):
{example}

CRITICAL RULES:
- NEVER respond with ONLY text narration. Every response that takes action MUST include the JSON block.
- Keep narration SHORT. 1-2 sentences max. No essays.
- After writing a file, run it ONCE to verify. Then STOP. Do NOT run Get-ChildItem, Get-Content, or other verification commands.
- Maximum 4 tool calls per request. After that, STOP and give final summary.
- When DONE, give a 1-sentence summary — no JSON block then.
TOOLS:
{chr(10).join(lines)}"""


def _is_filler_only(tool_calls: list) -> bool:
    """True when every call is a pointless 'sign-off' command (echo Done / exit 0).
    These otherwise loop forever: model echoes completion, client executes,
    model announces completion again."""
    if not tool_calls:
        return False
    for c in tool_calls:
        cmd = str((c.get("arguments") or {}).get("command", ""))
        base = cmd.split('|')[0].split('&')[0].strip().lower()
        if c.get("name") != "bash" or not (base.startswith("echo") or base.startswith("exit")):
            return False
    return True


def _narrate_call(tc: dict) -> str:
    """Fallback narration when the model emits a bare tool call with no text."""
    name = tc.get("name", "tool")
    args = tc.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if name == "bash":
        cmd = str(args.get("command", ''))[:90]
        return f"I'll run this command: `{cmd}` to execute the operation and check the results."
    target = args.get("filePath") or args.get("path") or ""
    if target:
        verb = {"write": "create", "create_file": "create"}.get(name, "update")
        content_preview = str(args.get("content", ""))[:80].replace('\n', ' ')
        return f"I'll {verb} **{target}** with the following content: {content_preview}..."
    if name == "edit":
        return f"I'll edit **{target or 'the file'}** by replacing the old string with the new one."
    return f"I'll use the {name} tool now to complete the requested operation."


def _strip_json_fragment(text: str, has_tools: bool) -> str:
    """Drop truncated/broken JSON fragments and failed tool attempts from final answers."""
    if has_tools:
        t = (text or "").strip()
        if (t.startswith("{") or '"arguments"' in t or '"filePath"' in t) and not parse_tool_calls(t):
            return ""
    return text or ""


def _repair_broken_json(fragment: str):
    """Lenient extraction when the model emits invalid JSON with unescaped quotes."""
    name_m = re.search(r'"name"\s*:\s*"([^"]+)"', fragment)
    if not name_m:
        return None
    pairs = re.findall(
        r'"([A-Za-z_]\w*)"\s*:\s*"(.*?)"(?=\s*(?:,\s*"[A-Za-z_]\w*"\s*:|[}\]]))',
        fragment,
        re.DOTALL,
    )
    def _unesc(s):
        # Model intended JSON escapes; strict parse died on quotes, apply the rest manually.
        return (s.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t')
                 .replace("\\'", "'").replace('\\"', '"').replace('\\\\', '\\'))
    args = {k: _unesc(v) for k, v in pairs if k != "name"}
    if not args:
        return None
    # The big payload (content/command/newString) is usually the LAST key.
    # Non-greedy pair matching cuts it at the first inner quote that happens
    # to precede '}' or ']' — re-extract greedily up to the fragment's true end.
    tail_m = re.search(
        r'"(content|command|newString|oldString|file_path|filePath)"\s*:\s*"(.*)"\s*}\s*\}\s*$',
        fragment,
        re.DOTALL,
    )
    if tail_m:
        key = tail_m.group(1)
        if key in args or key in ("content", "command", "newString"):
            args[key] = _unesc(tail_m.group(2))
    return {"name": name_m.group(1), "arguments": args}


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
                    unescaped = span.replace('\\"', '"')
                    if '"name"' in unescaped and '"arguments"' in unescaped:
                        candidates.append(span)
                    elif '"arguments"' in unescaped and ('"filePath"' in unescaped or '"command"' in unescaped or '"content"' in unescaped):
                        candidates.append(span)
                    # DeepSeek sometimes outputs args directly: {"filePath": "...", "content": "..."}
                    elif ('"filePath"' in unescaped or '"command"' in unescaped) and '"content"' in unescaped:
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
            elif 'filePath' in match or 'content' in match:
                if not call.get("arguments"):
                    call = {"name": "write", "arguments": call}
                tool_calls.append(call)
            elif 'command' in match:
                if not call.get("arguments"):
                    call = {"name": "bash", "arguments": call}
                tool_calls.append(call)
        except (json.JSONDecodeError, TypeError):
            # Tolerant repair: DeepSeek emits unescaped inner quotes, e.g.
            # "content": "print("Hello World")" — re-extract key/value pairs leniently.
            repaired = _repair_broken_json(cleaned)
            if repaired:
                tool_calls.append(repaired)

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


def clean_dom_artifacts(text: str) -> str:
    """Remove trailing UI artifacts from DOM-extracted text."""
    text = re.sub(r'[\s]*d[\?\uFFFD\ufffdY`<>]{1,8}[\s]*$', '', text)
    # Clean leading/trailing code block UI artifacts (e.g. "jsonCopyDownload", "pythonCopy")
    text = re.sub(r'^(json|python|javascript|typescript|bash|shell|html|css|copy|download|Copy|Download)+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(jsonCopyDownload|pythonCopy|javascriptCopy|typescriptCopy|bashCopy|shellCopy|htmlCopy|cssCopy|Copy|Download)+\s*$', '', text, flags=re.IGNORECASE)
    # Strip lone code fences (``` only, not code blocks with content)
    text = re.sub(r'^```\w*\s*$', '', text, flags=re.MULTILINE)
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
        # Chrome visible by default. Set DEEPSEEK_HIDE_BROWSER=1 to hide.
        self.ctx = await self.pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            # Off-screen by default; set DEEPSEEK_SHOW_BROWSER=1 to show
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--window-position={'-32000,-32000' if os.environ.get('DEEPSEEK_HIDE_BROWSER', '0') == '1' else '100,100'}",
                "--window-size=1280,900",
            ],
        )

    async def boot(self):
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()

        # Use CDP to track HTTP responses for SSE body capture.
        # Bodies are fetched IMMEDIATELY on loadingFinished — page navigations
        # (stateless new_chat) evict cached bodies otherwise.
        self._cdp_response_ids = {}
        self._cdp_bodies = {}
        try:
            self.cdp = await self.page.context.new_cdp_session(self.page)
            await self.cdp.send("Network.enable")
            loop = asyncio.get_event_loop()

            def on_response_received(params):
                resp = params.get("response", {})
                url = resp.get("url", "")
                req_id = params.get("requestId", "")
                if "chat/completion" in url:
                    self._cdp_response_ids.setdefault("chat_completion", []).append(req_id)

            def on_loading_finished(params):
                req_id = params.get("requestId", "")
                if req_id not in self._cdp_response_ids.get("chat_completion", []):
                    return
                if req_id in self._cdp_bodies:
                    return

                async def grab():
                    try:
                        result = await self.cdp.send("Network.getResponseBody", {"requestId": req_id})
                        body = result.get("body", "")
                        if body:
                            self._cdp_bodies[req_id] = body
                        else:
                            print(f"[cdp] grab empty body for rid={req_id}", flush=True)
                    except Exception as e:
                        print(f"[cdp] grab error rid={req_id}: {e}", flush=True)

                loop.create_task(grab())

            self.cdp.on("Network.responseReceived", on_response_received)
            self.cdp.on("Network.loadingFinished", on_loading_finished)
        except Exception:
            self.cdp = None

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
        """New UI: click the mode tab (Instant / Expert / Vision) or dropdown item (DeepSeek V3.2)."""
        if not label:
            return
        
        # First try direct label match (Instant / Expert / Vision)
        clicked = await self.page.evaluate(
            """(label) => {
                const els = [...document.querySelectorAll('button,div,span')];
                const tab = els.find(e => e.children.length === 0 && (e.textContent||'').trim() === label)
                         || els.find(e => (e.textContent||'').trim() === label);
                if (tab) {
                    tab.click();
                    return true;
                }
                return false;
            }""",
            label,
        )
        
        # If not found, try dropdown model selector (DeepSeek V3.2 / Instant / Expert)
        if not clicked:
            await self.page.evaluate(
                """(label) => {
                    // Click model dropdown/selector to open it
                    const selectors = [...document.querySelectorAll('button,div[role="button"],span[role="button"]')];
                    const dropdown = selectors.find(e => {
                        const t = (e.textContent||'').toLowerCase();
                        return t.includes('deepseek') || t.includes('model') || t.includes('v3') || t.includes('v4');
                    });
                    if (dropdown) dropdown.click();
                }""",
                label,
            )
            await self.page.wait_for_timeout(300)
            
            # Now try to select from dropdown
            await self.page.evaluate(
                """(label) => {
                    const items = [...document.querySelectorAll('li,div[role="menuitem"],div[role="option"],button,span')];
                    const item = items.find(e => (e.textContent||'').trim().includes(label));
                    if (item) item.click();
                }""",
                label,
            )
        
        await self.page.wait_for_timeout(600)

    async def ensure_think_off(self):
        """Disable the DeepThink toggle. DOM classes are obfuscated, so detect state
        via computed background-color signature persisted in think_off.json."""
        try:
            sig_file = Path(__file__).parent / "think_off.json"
            sig = ""
            if sig_file.exists():
                try:
                    sig = json.loads(sig_file.read_text(encoding="utf-8")).get("bg", "")
                except Exception:
                    sig = ""

            async def get_bg():
                return await self.page.evaluate(
                    """() => {
                        const norm = s => (s||'').trim().toLowerCase();
                        const els = [...document.querySelectorAll('div,span')];
                        const dt = els.find(e => e.children.length === 0 && norm(e.textContent) === 'deepthink')
                                || els.find(e => norm(e.textContent).startsWith('deepthink'));
                        if (!dt) return '';
                        const target = dt.closest('div[class]') || dt;
                        return getComputedStyle(target).backgroundColor + '|' + getComputedStyle(dt).color;
                    }"""
                )

            bg = await get_bg()
            if not bg:
                return False
            if sig and bg == sig:
                return False  # already off
            await self.page.evaluate(
                """() => {
                    const norm = s => (s||'').trim().toLowerCase();
                    const els = [...document.querySelectorAll('div,span')];
                    const dt = els.find(e => e.children.length === 0 && norm(e.textContent) === 'deepthink')
                            || els.find(e => norm(e.textContent).startsWith('deepthink'));
                    const target = (dt && dt.closest('div[class]')) || dt;
                    if (target) target.click();
                }"""
            )
            await self.page.wait_for_timeout(400)
            bg2 = await get_bg()
            if bg2 and bg2 != bg:
                sig_file.write_text(json.dumps({"bg": bg2}), encoding="utf-8")
            print(f"[DeepThink] toggled off (state {bg!r} -> {bg2!r})", flush=True)
            return True
        except Exception as e:
            print(f"[DeepThink] ensure_think_off error: {e}", flush=True)
            return False

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

    async def send_and_capture(self, text, has_tools=False):
        """Non-streaming: delegate to send_and_stream and join the chunks."""
        chunks = []
        async for piece in self.send_and_stream(text, has_tools=has_tools):
            chunks.append(piece)
        return self.take_network_text("".join(chunks))

    def take_network_text(self, fallback: str, has_tools: bool = False) -> str:
        """Pristine response text: CDP capture first, then raw <pre><code> textContent
        (unrendered markdown, preserves indentation/underscores), else DOM fallback."""
        net = (getattr(self, "_final_net", "") or "").strip()
        self._final_net = ""
        sane = bool(net) and len(net) > 20 and (
            not has_tools or parse_tool_calls(net) or '"arguments"' not in net
        )
        if sane:
            return net
        code = (getattr(self, "_last_code_text", "") or "").strip()
        self._last_code_text = ""
        if has_tools and len(code) > 20:
            print(f"[capture] using raw code block ({len(code)} chars, net={len(net)})", flush=True)
            return code if parse_tool_calls(code) else salvage_json_text(code)
        print(f"[capture] fell back to DOM text (net={len(net)}, code={len(code)})", flush=True)
        return fallback

    async def send_and_stream(self, text, has_tools=False):
        """Send and yield live chunks via DOM polling."""
        await self.page.wait_for_selector("textarea", timeout=20000)

        try:
            extract_js = """() => {
                const containers = document.querySelectorAll('[class*="ds-markdown"], [class*="message-content"], [class*="markdown"], [class*="answer"], div[class*="message"]');
                if (containers.length) {
                    const last = containers[containers.length - 1];
                    const t = last.innerText || last.textContent || '';
                    if (t.trim()) return t.trim();
                }
                const chatArea = document.querySelector('[class*="chat-content"], [class*="conversation"], main');
                if (chatArea) {
                    const t = chatArea.innerText || chatArea.textContent || '';
                    if (t.trim()) return t.trim();
                }
                return '';
            }"""

            # For tool calls, also grab code blocks from the page
            extract_code_js = """() => {
                // Find the LAST code block on the page (where tool call JSON lives)
                const codeBlocks = document.querySelectorAll('pre code, code, [class*="code-block"]');
                if (codeBlocks.length) {
                    const last = codeBlocks[codeBlocks.length - 1];
                    return last.textContent || last.innerText || '';
                }
                return '';
            }"""

            # Capture baseline BEFORE sending — DOM state includes conversation history
            try:
                baseline_text = await self.page.evaluate(extract_js)
            except Exception:
                baseline_text = ""

            await self._type_and_send(text)

            self._last_code_text = ""
            last_text = baseline_text
            sent = ""
            stable_polls = 0
            RISKY = ("`", "{", "<", ">", "\u2713", "\u2717", "|")
            deadline = time.time() + 120
            while time.time() < deadline:
                await asyncio.sleep(0.35)
                try:
                    current = await self.page.evaluate(extract_js)
                except Exception:
                    current = ""

                # For tool calls, also check if a code block appeared
                code_text = ""
                if has_tools:
                    try:
                        code_text = await self.page.evaluate(extract_code_js)
                        if code_text and len(code_text) > len(getattr(self, "_last_code_text", "")):
                            self._last_code_text = code_text
                    except Exception:
                        pass

                # Combine text + code block for tool call detection
                full_current = current
                if has_tools and code_text and code_text not in current:
                    full_current = current + "\n" + code_text

                if not has_tools and (
                    "\nCopy\n" in current
                ):
                    # Only break if the response footer is visible (response truly done)
                    if "AI-generated" in current or "for reference only" in current:
                        break
                    # Also check for the stop indicator (thumbs up/down buttons)
                    if "\nCopy\n" in current and current.count("\n") > 15:
                        break

                # For tool calls, wait for stable text
                if has_tools and len(sent) >= 3000:
                    break

                if full_current == last_text:
                    stable_polls += 1
                    # Network body already captured -> no need to wait for DOM stability
                    # Code block may still be rendering -> need more patience
                    has_code = has_tools and code_text and len(code_text) > 50
                    limit = 10 if getattr(self, "_cdp_bodies", None) else (20 if has_code else 40)
                    if stable_polls >= limit:
                        break
                elif len(full_current) > len(last_text) and full_current.startswith(last_text):
                    chunk = full_current[len(last_text):]
                    last_text = full_current
                    stable_polls = 0
                    chunk = clean_dom_artifacts(chunk)
                    if chunk.strip() and not (not has_tools and any(c in chunk for c in RISKY)):
                        sent += chunk
                        yield chunk
                else:
                    last_text = full_current
                    stable_polls = 0
                    cleaned = clean_dom_artifacts(full_current)
                    if cleaned.strip() and cleaned.strip() != clean_dom_artifacts(baseline_text).strip():
                        # DOM re-rendered - only yield the new tail
                        if len(cleaned) > len(sent):
                            tail = cleaned[len(sent):]
                            if tail.strip():
                                sent = cleaned
                                yield tail
                        else:
                            sent = cleaned
                    stable_polls += 1
                    if stable_polls >= 40:
                        break

            # Wait briefly for response to complete
            await asyncio.sleep(0.5 if getattr(self, "_cdp_bodies", None) else 2.0)

            # If no network body yet, the model may just be mid-generation
            # (early-exit polling fired during a pause). Give it a moment —
            # falling back to partial DOM text truncates long tool arguments.
            if not self._cdp_bodies and has_tools:
                for _ in range(16):
                    await asyncio.sleep(0.5)
                    if self._cdp_bodies:
                        break

            # Try to get full SSE body via CDP Network.getResponseBody.
            # DeepSeek fires several chat/completion XHRs per send; pick the LARGEST body
            # (the real completion) rather than whichever response arrived last.
            final_net = ""
            req_ids = self._cdp_response_ids.pop("chat_completion", [])
            if self.cdp and req_ids:
                best_body = ""
                for rid in req_ids:
                    body = self._cdp_bodies.pop(rid, "")
                    if not body:  # cache miss -> last-chance direct fetch
                        try:
                            result = await self.cdp.send("Network.getResponseBody", {"requestId": rid})
                            body = result.get("body", "")
                        except Exception:
                            body = ""
                    print(f"[cdp] rid={rid} body_len={len(body)}", flush=True)
                    if body and len(body) > len(best_body):
                        best_body = body
                self._cdp_bodies.clear()
                if best_body:
                    try:
                        (Path(__file__).parent / "last_sse.txt").write_text(best_body, encoding="utf-8")
                    except Exception:
                        pass
                    final_net = build_openai_text(best_body)
            else:
                print(f"[cdp] no candidates (cdp={bool(self.cdp)}, ids={len(req_ids)})", flush=True)

            # Fallback: if CDP body empty but code block on page has content, use it
            if not final_net and has_tools:
                try:
                    code_fallback = await self.page.evaluate("""() => {
                        const blocks = document.querySelectorAll('pre code, code, [class*="code-block"]');
                        if (blocks.length) {
                            const last = blocks[blocks.length - 1];
                            return last.textContent || last.innerText || '';
                        }
                        return '';
                    }""")
                    if code_fallback and len(code_fallback) > 50:
                        print(f"[capture] CDP empty, using DOM code block ({len(code_fallback)} chars)", flush=True)
                        final_net = code_fallback
                except Exception:
                    pass

            # If network capture produced more than DOM streaming sent, yield the tail
            self.last_network_text = final_net
            self._final_net = final_net
            if final_net and len(final_net) > len(sent):
                net_tail = final_net[len(sent):] if final_net.startswith(sent) else final_net
                if net_tail.strip():
                    yield net_tail
        except Exception:
            pass
        finally:
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
MAX_TURNS_PER_CHAT = 15
last_task_head = ""
# Stateless mode: fresh web chat per request. Default ON: agent clients (OpenCode etc.)
# resend full history each call, and chat reuse leaks old responses into captures.
STATELESS = os.getenv("DEEPSEEK_STATELESS", "1") == "1"
# AUTOEXEC=1: bridge executes tools itself and returns only the final answer
# (invisible to the client). Default 0: tool calls are returned to the client
# (OpenCode etc. execute + display them natively).
AUTOEXEC = os.getenv("DEEPSEEK_AUTOEXEC", "0") == "1"


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
    """Rebuild answer text from DeepSeek SSE (JSON-patch format).

    Handles: full snapshots ({"v":{"response":{"fragments":[...]}}}),
    patch ops ({"p":"response/fragments/-1/content","o":"APPEND","v":"..."}),
    and bare deltas ({"v":"..."}).
    """
    frags = []  # [{type, content}]
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
        if not isinstance(obj, dict):
            continue
        v = obj.get("v")
        p = obj.get("p", "")
        o = obj.get("o", "")
        if isinstance(v, dict) and isinstance(v.get("response"), dict):
            fs = v["response"].get("fragments")
            if isinstance(fs, list):
                frags = [
                    {"type": f.get("type", "RESPONSE"), "content": f.get("content", "") or ""}
                    for f in fs if isinstance(f, dict)
                ]
                continue
        if isinstance(v, str) and not p:
            if not frags or frags[-1]["type"] != "RESPONSE":
                frags.append({"type": "RESPONSE", "content": ""})
            frags[-1]["content"] += v
            continue
        if isinstance(v, str) and p.endswith("/content") and o in ("APPEND", "SET", "REPLACE"):
            if not frags:
                frags.append({"type": "RESPONSE", "content": ""})
            if o == "APPEND":
                frags[-1]["content"] += v
            else:
                frags[-1]["content"] = v

    return "".join(f["content"] for f in frags if f["type"] == "RESPONSE")

# ---------- Chat history log (in-memory, viewable/editable via API) ----------
chat_log: list[dict] = []  # [{role, content, timestamp, index}]

def _log_chat(role: str, content: str):
    chat_log.append({"role": role, "content": content, "ts": time.time(), "i": len(chat_log)})

CHAT_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Bridge Chat</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0b1120;color:#e2e8f0;padding:1rem}
h1{font-size:1.4rem;color:#94a3b8;margin-bottom:1rem;text-align:center}
.msg{border-radius:1rem;padding:0.8rem 1rem;margin-bottom:0.6rem;max-width:85%;word-wrap:break-word;white-space:pre-wrap;position:relative;font-size:0.95rem;line-height:1.4}
.user{background:#1e40af;margin-left:auto;border-bottom-right-radius:0.2rem}
.assistant{background:#1e293b;border:1px solid #334155;border-bottom-left-radius:0.2rem}
.tool{background:#0f172a;border:1px solid #475569;font-family:monospace;font-size:0.85rem;color:#94a3b8}
.system{background:#1a1a2e;border:1px solid #555;font-style:italic;color:#94a3b8}
.role{font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;margin-bottom:0.3rem;opacity:0.6}
.bar{display:flex;gap:0.5rem;justify-content:center;margin-bottom:1rem}
.bar button{background:#334155;border:none;color:#e2e8f0;padding:0.4rem 0.8rem;border-radius:0.5rem;cursor:pointer;font-size:0.85rem}
.bar button:hover{background:#475569}
.bar button.del{background:#991b1b}.bar button.del:hover{background:#b91c1c}
.bar button.edit{background:#1d4ed8}.bar button.edit:hover{background:#2563eb}
textarea.edit-box{width:100%;min-height:60px;background:#0f172a;color:#e2e8f0;border:1px solid #3b82f6;border-radius:0.5rem;padding:0.5rem;font-family:inherit;resize:vertical}
.count{text-align:center;color:#64748b;font-size:0.8rem;margin-bottom:0.5rem}
</style></head><body>
<h1>Bridge Chat Log</h1>
<div class="count" id="count"></div>
<div id="log"></div>
<div class="bar">
<button onclick="load()">Refresh</button>
<button class="del" onclick="clearAll()">Clear All</button>
</div>
<script>
async function load(){
  const r=await fetch('/v1/chat/history');
  const d=await r.json();
  const el=document.getElementById('log');
  el.innerHTML='';
  document.getElementById('count').textContent=d.history.length+' messages';
  d.history.forEach((m,i)=>{
    const div=document.createElement('div');
    div.className='msg '+(m.role==='user'?'user':m.role==='assistant'?'assistant':m.role==='tool'?'tool':'system');
    div.innerHTML='<div class="role">'+m.role+' #'+i+'</div><div class="content">'+escHtml(m.content)+'</div>'
      +'<div class="bar" style="justify-content:flex-end;margin-top:0.4rem">'
      +'<button class="edit" onclick="editMsg('+i+')">Edit</button>'
      +'<button class="del" onclick="delMsg('+i+')">Delete</button></div>';
    el.appendChild(div);
  });
}
function escHtml(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
async function delMsg(i){await fetch('/v1/chat/history/'+i,{method:'DELETE'});load()}
async function clearAll(){await fetch('/v1/chat/history',{method:'DELETE'});load()}
async function editMsg(i){
  const r=await fetch('/v1/chat/history');
  const d=await r.json();
  const old=d.history[i]?.content||'';
  const box=document.createElement('div');
  box.className='msg assistant';
  box.innerHTML='<div class="role">Edit #'+i+'</div>'
    +'<textarea class="edit-box" id="et'+i+'">'+escHtml(old)+'</textarea>'
    +'<div class="bar" style="margin-top:0.4rem">'
    +'<button class="edit" onclick="saveMsg('+i+')">Save</button>'
    +'<button onclick="this.closest(\'.msg\').remove()">Cancel</button></div>';
  document.getElementById('log').children[i].after(box);
  document.getElementById('et'+i).focus();
}
async function saveMsg(i){
  const txt=document.getElementById('et'+i).value;
  await fetch('/v1/chat/history/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:txt})});
  load();
}
load();
</script></body></html>"""


@app.get("/v1/chat/history")
async def get_chat_history():
    async with request_lock:
        return {"history": chat_log, "count": len(chat_log)}


@app.get("/v1/chat/history/html")
async def chat_history_html():
    return Response(content=CHAT_HTML, media_type="text/html")


@app.put("/v1/chat/history/{index}")
async def edit_chat_message(index: int, request: Request):
    body = await request.json()
    async with request_lock:
        if 0 <= index < len(chat_log):
            chat_log[index]["content"] = body.get("content", chat_log[index]["content"])
            return {"ok": True, "index": index}
    return {"ok": False, "error": "index out of range"}


@app.delete("/v1/chat/history/{index}")
async def delete_chat_message(index: int):
    async with request_lock:
        if 0 <= index < len(chat_log):
            chat_log.pop(index)
            return {"ok": True}
    return {"ok": False, "error": "index out of range"}


@app.delete("/v1/chat/history")
async def clear_chat_history():
    async with request_lock:
        chat_log.clear()
    return {"ok": True}


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
    # Client can specify workspace per-request via body or header
    req_workspace = data.get("workspace", "") or request.headers.get("x-workspace", "")
    if req_workspace and os.path.isdir(req_workspace):
        tool_executor.workspace = Path(req_workspace)
        print(f"[workspace] using client workspace: {req_workspace}", flush=True)

    # Debug: log incoming request structure
    print(f"REQUEST: model={model} stream={stream} tools={len(tools)} msgs={len(messages)}", flush=True)
    for i, m in enumerate(messages[-3:]):
        role = m.get("role", "")
        content = str(m.get("content", ""))[:200]
        print(f"  msg[{i}]: role={role} content={content[:100]}...", flush=True)
    if tools:
        for t in tools[:5]:
            fn = t.get("function", {})
            print(f"  tool: {fn.get('name', '?')}", flush=True)

    # Build conversation history (skip system prompts - they are huge and useless here)
    # Truncate to stay under DeepSeek web timeout
    MAX_HISTORY = 12
    history_parts = []
    system_text = ""
    for m in messages[-MAX_HISTORY:]:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role == "system":
            system_text += (content or "") + "\n"
        elif role == "user":
            history_parts.append(f"[User]: {content}")
        elif role == "assistant":
            if content:
                history_parts.append(f"[Assistant]: {content[:2000]}")
        elif role == "tool":
            history_parts.append(f"[Tool Result]: {str(content)[:2000]}")

    # Build the prompt
    tool_prompt = build_tool_prompt(tools) if tools else ""

    # Create the full message with history
    full_message = ""

    # Client system prompt (defines the agent's persona/behavior) — keep it
    if system_text.strip():
        full_message += "YOUR INSTRUCTIONS:\n" + system_text.strip()[:6000] + "\n\n"

    # Platform info — prevents Linux commands on Windows
    full_message += (
        "PLATFORM: Windows PowerShell. Use PowerShell commands only. "
        "Examples: New-Item, Get-ChildItem, Set-Content, Copy-Item, Remove-Item. "
        "Do NOT use Linux commands like mkdir, ls, cat, cp, rm.\n\n"
    )

    # Override any conciseness rules — user wants visible reasoning
    full_message += (
        "COMMUNICATION STYLE: Be CONCISE. 1-2 sentences before each action. "
        "After writing a file, run it ONCE to verify output. Then STOP immediately. "
        "No extra verification commands (Get-ChildItem, Get-Content, etc). "
        "Max 4 tool calls per request. Final summary: 1 sentence only.\n\n"
    )

    # Tool format rules
    if tool_prompt:
        full_message += tool_prompt + "\n\n"

    if len(history_parts) > 1:
        full_message += "Conversation history:\n"
        for part in history_parts[:-1]:
            full_message += part + "\n\n"

    # Add current user message
    # Current request = last NON-assistant message. When the transcript ends
    # with our own reply (post-tool rounds), asking the model to respond to
    # itself makes it echo completion ("Done.") forever.
    current_msg = "[User]: Hello"
    for _p in reversed(history_parts):
        if not _p.startswith("[Assistant]:"):
            current_msg = _p
            break
    else:
        if history_parts:
            current_msg = history_parts[-1]
    full_message += "Current request:\n" + current_msg

    # Log user request to chat history
    _log_chat("user", current_msg.replace("[User]: ", "", 1))

    # Tool prompt AGAIN at the END as reminder
    if tool_prompt:
        full_message += "\n\n" + tool_prompt

    # Auto-inject PROJECT_LOG protocol on first message of each session
    # Disabled: confuses DeepSeek and prevents tool calls
    # if len(history_parts) <= 1:
    #     full_message = PROJECT_LOG_INSTRUCTION + "\n\n" + full_message

    # Debug: log final message
    print(f"FULL_MSG ({len(full_message)} chars):", flush=True)
    print(full_message[:500], flush=True)

    # Hard cap - DeepSeek web UI times out with long messages (~12K safe limit)
    MAX_MSG_LEN = 12000
    if len(full_message) > MAX_MSG_LEN:
        # Keep tool prompt + current message, trim history
        if tool_prompt and len(full_message) > MAX_MSG_LEN:
            history_budget = MAX_MSG_LEN - len(tool_prompt) - 200
            trimmed_history = "\n\n".join(history_parts[:-1])[-history_budget:]
            full_message = tool_prompt + "\n\n---\n\n" + trimmed_history + "\n\nCurrent request:\n" + current_msg
        # Still too long? Hard truncate current message
        if len(full_message) > MAX_MSG_LEN:
            full_message = full_message[:MAX_MSG_LEN] + "\n...(truncated)..."

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
                        await driver.ensure_think_off()
                        chat_turn_count = 0
                    driver._round_count = 0  # Reset round counter for new request
                    driver._recent_sigs = []  # Reset repeat-loop tracker
                    yield sse({"role": "assistant"})

                    # Buffer ALL chunks — classify the full response before yielding content
                    buffered = []
                    async for piece in driver.send_and_stream(full_message, has_tools=bool(tools)):
                        buffered.append(piece)

                    full_text = driver.take_network_text("".join(buffered))
                    print(f"DEBUG full_text ({len(full_text)} chars): {full_text[:300]}", flush=True)

                    full_text = clean_dom_artifacts(full_text)

                    # DeepSeek web rate limit -> fail fast, do NOT retry (retrying
                    # hammers the limit and poisons every later round)
                    if re.search(r"messages?\s+too\s+frequent|too\s+many\s+requests|rate\s*-?\s*limit", full_text, re.I):
                        yield sse({"content": "[DeepSeek web rate limit reached. Wait 10-30 minutes, then resend.]"}, finish="stop")
                        yield "data: [DONE]\n\n"
                        return

                    # If the model echoed our instruction block instead of acting,
                    # force one retry focused on the actual request.
                    _ECHO_MARKS = ("YOUR INSTRUCTIONS:", "REPLY IN ENGLISH", "COMMUNICATION STYLE")
                    if tools and any(m in full_text[:600] for m in _ECHO_MARKS) and not relativize_tool_paths(parse_tool_calls(full_text)):
                        print("[bridge] instruction echo detected, retrying", flush=True)
                        try:
                            nudged = []
                            async for p in driver.send_and_stream(
                                "You repeated the instructions instead of acting. "
                                "Respond ONLY to the CURRENT REQUEST below, in the required format.\n\n"
                                + full_message[-1500:]
                            ):
                                nudged.append(p)
                            t2 = driver.take_network_text("".join(nudged), has_tools=True)
                            if t2 and not any(m in t2[:400] for m in _ECHO_MARKS):
                                full_text = t2
                        except Exception:
                            pass

                    # Tool call classification (post-streaming)
                    classified_tool = bool(
                        re.search(r'```(?:json)?\s*\{', full_text)
                        or re.search(r'\{\s*"name"', full_text)
                        or re.search(r'"name"\s*:\s*"', full_text)
                        or re.search(r'"arguments"\s*:', full_text)
                    )
                    if tools and not parse_tool_calls(full_text):
                        salvaged = salvage_json_text(full_text)
                        if salvaged != full_text:
                            full_text = salvaged

                    # No tool call detected -> force one retry
                    if tools and not parse_tool_calls(full_text):
                        # Check if model just narrated without acting
                        has_action_words = bool(re.search(
                            r"(?:I'll|I will|let me|going to|now I'll|I need to|I should).*(?:read|create|write|edit|update|add|run|check|fix)",
                            full_text, re.I
                        ))
                        if has_action_words:
                            nudge = (
                                "You narrated what you would do but DID NOT include the JSON tool call block.\n"
                                "Your narration said you would act — now ACT. Include the JSON block:\n"
                                '```json\n{"name": "tool_name", "arguments": {"param": "value"}}\n```\n'
                                "Do NOT just narrate again. Execute the tool call NOW."
                            )
                        else:
                            nudge = (
                                "Your last reply had no tool call and NOTHING was executed.\n"
                                "Reply with your next action as exactly:\n"
                                '```json\n{"name": "tool_name", "arguments": {"param": "value"}}\n```'
                            )
                        try:
                            nudged = []
                            async for p in driver.send_and_stream(nudge):
                                nudged.append(p)
                            t2 = driver.take_network_text("".join(nudged))
                            if t2:
                                t2s = salvage_json_text(t2)
                                if parse_tool_calls(t2s):
                                    full_text = t2s
                        except Exception:
                            pass

                    tool_calls = relativize_tool_paths(parse_tool_calls(full_text))
                    # Reject calls with empty args (DOM truncation artifacts)
                    usable = [c for c in tool_calls if c.get("name") and any(v != "" for v in c.get("arguments", {}).values())]
                    if tool_calls and not usable:
                        try:
                            nudged = []
                            async for p in driver.send_and_stream(
                                "Your previous JSON block was incomplete/truncated. Repeat the FULL tool call JSON block now, nothing else."
                            ):
                                nudged.append(p)
                            t2 = driver.take_network_text("".join(nudged), has_tools=True)
                            tc2 = [c for c in relativize_tool_paths(parse_tool_calls(t2)) if c.get("name") and any(v != "" for v in c.get("arguments", {}).values())]
                            if tc2:
                                usable = tc2
                        except Exception:
                            pass
                    tool_calls = usable

                    # Reject hallucinated tool names (e.g. literal "tool_name"
                    # copied from the example) and force one re-pick round.
                    if tools and tool_calls:
                        valid_names = {t.get("function", {}).get("name", "") for t in tools}
                        bad_names = {c.get("name", "") for c in tool_calls} - valid_names
                        if bad_names:
                            print(f"[bridge] invalid tool names: {bad_names}", flush=True)
                            try:
                                nudged = []
                                async for p in driver.send_and_stream(
                                    f"These tools DO NOT exist: {sorted(bad_names)}. "
                                    f"Choose ONE real tool from: {sorted(valid_names)}. "
                                    "Reply with the first-line sentence plus its JSON block only."
                                ):
                                    nudged.append(p)
                                t2 = driver.take_network_text("".join(nudged), has_tools=True)
                                tc2 = [c for c in relativize_tool_paths(parse_tool_calls(t2))
                                       if c.get("name") in valid_names and any(v != "" for v in c.get("arguments", {}).values())]
                                if tc2:
                                    tool_calls = tc2
                                    full_text = t2
                            except Exception:
                                pass
                            tool_calls = [c for c in tool_calls if c.get("name") in valid_names]

                    # Sign-off filler (echo Done / exit 0) = final answer, not actions
                    if tool_calls and _is_filler_only(tool_calls):
                        print("[bridge] filler sign-off detected -> final text", flush=True)
                        tool_calls = []

                    # Repeat-loop guard: stop on ANY repeated command
                    if tool_calls:
                        for tc in tool_calls:
                            sig = tc.get("name", "")
                            args = tc.get("arguments", {})
                            if isinstance(args, dict):
                                if sig == "bash":
                                    cmd = str(args.get("command", "")).lower().strip()
                                    # Normalize: strip paths, just keep command + key args
                                    cmd = re.sub(r'["\']?[A-Za-z]:\\[^"\s]*["\']?', '', cmd)
                                    cmd = re.sub(r'\s+', ' ', cmd).strip()
                                    sig = f"bash:{cmd[:60]}"
                                else:
                                    sig = f"{sig}:{json.dumps(args, sort_keys=True)[:60]}"
                            if not hasattr(driver, "_recent_sigs"):
                                driver._recent_sigs = []
                            driver._recent_sigs.append(sig)
                        # Keep only last 6 signatures
                        if len(driver._recent_sigs) > 6:
                            driver._recent_sigs = driver._recent_sigs[-6:]
                        # Check if ANY signature appeared 2+ times
                        for s in driver._recent_sigs:
                            if driver._recent_sigs.count(s) >= 2:
                                print(f"[bridge] repeat-loop detected ({s}) -> stopping", flush=True)
                                tool_calls = []
                                break
                        # Hard limit: max 4 tool calls total per request
                        if not hasattr(driver, "_round_count"):
                            driver._round_count = 0
                        driver._round_count += len(tool_calls)
                        if driver._round_count > 4:
                            print(f"[bridge] max rounds (4) reached -> stopping", flush=True)
                            tool_calls = []

                    # Client-executes mode: hand tool calls back so the client
                    # (OpenCode etc.) runs + displays them natively.
                    if not (tools and not AUTOEXEC):
                        # AUTO-EXECUTE: loop until no more tool calls
                        MAX_AUTO_ROUNDS = 3
                        for _auto in range(MAX_AUTO_ROUNDS):
                            if not tool_calls:
                                break
                            results = []
                        for tc in tool_calls:
                            name = tc.get("name", "")
                            args = tc.get("arguments", {})
                            result = tool_executor.execute(name, args)
                            results.append(f"Tool: {name}\nArgs: {json.dumps(args)}\nResult:\n{result}")
                            _log_chat("tool", f"{name}({json.dumps(args)}) => {result[:500]}")
                            follow_up = "Tool results:\n\n" + "\n\n".join(results) + "\n\nContinue. If done, give your final answer."
                            try:
                                nudged = []
                                async for p in driver.send_and_stream(follow_up):
                                    nudged.append(p)
                                full_text = driver.take_network_text("".join(nudged))
                                if full_text:
                                    tool_calls = relativize_tool_paths(parse_tool_calls(full_text))
                                else:
                                    break
                            except Exception:
                                break

                    if tool_calls and tools:
                        # OpenCode expects camelCase (filePath, command) — do NOT alias
                        # The model already emits camelCase, just pass through as-is
                        # OpenCode expects arguments as a JSON string per OpenAI API spec
                        for tc in tool_calls:
                            args = tc.get("arguments", {})
                            # Ensure arguments is already a string (from parse_tool_calls)
                            if isinstance(args, dict):
                                args = json.dumps(args)
                            tc["arguments"] = args
                        tc_list = [{
                            "id": "call_" + uuid.uuid4().hex[:12],
                            "type": "function",
                            "function": {
                                "name": c.get("name", ""),
                                "arguments": c.get("arguments", {}),
                            },
                            "index": i,
                        } for i, c in enumerate(tool_calls)]
                        # Narration that accompanied the action (model thinking out loud)
                        prose = clean_dom_artifacts(remove_tool_calls(full_text)).strip()
                        if prose and (parse_tool_calls(prose) or prose.lstrip().startswith('{"')):
                            prose = ""  # leftover JSON junk, not real narration
                        if any(m in prose for m in ("REPLY IN ENGLISH", "COMMUNICATION STYLE", "YOUR INSTRUCTIONS:")):
                            prose = ""  # echoed instructions are not narration
                        _log_chat("assistant", prose or _narrate_call(tool_calls[0]))
                        _log_chat("assistant", f"[tool_call: {tool_calls[0].get('name')}({json.dumps(tool_calls[0].get('arguments',{}))})]")
                        yield sse({"content": prose or _narrate_call(tool_calls[0])})
                        yield sse({"tool_calls": tc_list})
                        yield sse({}, finish="tool_calls")
                    elif tools and classified_tool:
                        # Looked like a tool attempt but unusable -> show narration ONLY
                        prose = re.split(r'\{\s*"name"', remove_tool_calls(full_text))[0].strip()
                        prose = _strip_json_fragment(prose, True)
                        if prose:
                            _log_chat("assistant", prose)
                            yield sse({"content": prose})
                        yield sse({}, finish="stop")
                    else:
                        reply = _strip_json_fragment(full_text, bool(tools))
                        _log_chat("assistant", reply)
                        yield sse({"content": reply})
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
                await driver.ensure_think_off()
                chat_turn_count = 0
            raw = await driver.send_and_capture(full_message, has_tools=bool(tools))
            chat_turn_count += 1
        except Exception as e:
            traceback.print_exc()
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=500,
            )

    text = clean_dom_artifacts(raw)
    LAST_RAW["body"] = raw

    created = int(time.time())
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]

    # Client-executes mode: return tool calls for the client to run/display.
    client_calls = [c for c in relativize_tool_paths(parse_tool_calls(text)) if c.get("name") and any(v != "" for v in c.get("arguments", {}).values())]
    if tools and not AUTOEXEC and client_calls:
        if _is_filler_only(client_calls):
            client_calls = []
    if tools and not AUTOEXEC and client_calls:
        prose = clean_dom_artifacts(remove_tool_calls(text)).strip()
        if parse_tool_calls(prose) or (prose.lstrip().startswith('{"')):
            prose = ""
        if not prose:
            prose = _narrate_call(client_calls[0])
        # OpenCode expects arguments as a JSON string per OpenAI API spec
        for c in client_calls:
            args = c.get("arguments", {})
            if isinstance(args, dict):
                c["arguments"] = json.dumps(args)
        tc_list = [{
            "id": "call_" + uuid.uuid4().hex[:12],
            "type": "function",
            "function": {
                "name": c.get("name", ""),
                "arguments": c.get("arguments", {}),
            },
            "index": i,
        } for i, c in enumerate(client_calls)]
        if not stream:
            return {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": prose or None, "tool_calls": tc_list},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        async def tool_event_stream():
            if prose:
                yield "data: " + json.dumps({
                    "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"content": prose}, "finish_reason": None}],
                }) + "\n\n"
            yield "data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}],
            }) + "\n\n"
            yield "data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"tool_calls": tc_list}, "finish_reason": None}],
            }) + "\n\n"
            yield "data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(tool_event_stream(), media_type="text/event-stream")

    # AUTO-EXECUTE: loop until no more tool calls or max iterations
    MAX_AUTO_ROUNDS = 3
    for _round in range(MAX_AUTO_ROUNDS):
        tool_calls = [c for c in relativize_tool_paths(parse_tool_calls(text)) if c.get("name") and any(v != "" for v in c.get("arguments", {}).values())]
        if not tool_calls:
            break

        # Execute tool calls locally
        results = []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            result = tool_executor.execute(name, args)
            results.append(f"Tool: {name}\nArgs: {json.dumps(args)}\nResult:\n{result}")

        # Send results back to DeepSeek
        follow_up = "Tool results:\n\n" + "\n\n".join(results) + "\n\nContinue. If done, give your final answer."
        async with request_lock:
            try:
                raw = await driver.send_and_capture(follow_up, has_tools=True)
                text = clean_dom_artifacts(raw)
            except Exception:
                break

    # Regular response (no tools or after auto-execution)
    if not stream:
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _strip_json_fragment(text, bool(tools))},
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
