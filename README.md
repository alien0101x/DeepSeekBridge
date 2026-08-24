# DeepSeekBridge

OpenAI-compatible bridge to [chat.deepseek.com](https://chat.deepseek.com) using browser automation. Use DeepSeek models in any OpenAI-compatible tool.

## Features

- 🔌 OpenAI API compatible (`/v1/chat/completions`)
- 🧠 Supports DeepSeek-V3 and DeepSeek-R1
- 🔧 **Tool Support** - Execute commands, create/edit files, search code
- 💾 Remembers login (only login once)
- 🚀 Fast - reuses same chat session
- 🔒 Your tokens stay local (not uploaded)

## Quick Start

### Windows (Recommended)

1. Clone this repo:
   ```
   git clone https://github.com/alien0101x/DeepSeekBridge.git
   cd DeepSeekBridge
   ```

2. Run setup:
   ```
   setup.cmd
   ```

3. First time: Chrome opens → login to chat.deepseek.com → done!

> **Note:** You need a **DeepSeek account** (not Google/Gmail). Create one free at https://chat.deepseek.com

### Auto-Start for Any Agent

Use `bridge-status.py` to auto-start the bridge from any agent:

```bash
# Check if running
python bridge-status.py

# Auto-start if not running
python bridge-status.py --start

# Wait until ready
python bridge-status.py --wait
```

**For OpenCode users:** The plugin auto-starts the bridge when needed.

4. After setup, just double-click `DeepSeekBridge` on desktop.

### Manual Setup

1. Install Python 3.8+
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Install Playwright browser:
   ```
   python -m playwright install chromium
   ```
4. Run the bridge:
   ```
   python main.py
   ```

## Tool Support

DeepSeekBridge v2 supports **function calling** - DeepSeek can now execute tools like a real AI agent!

### Available Tools

| Tool | Description |
|------|-------------|
| `execute_command` | Run shell commands (PowerShell, bash, etc.) |
| `create_file` | Create new files with content |
| `read_file` | Read file contents |
| `edit_file` | Edit existing files (find & replace) |
| `list_files` | List files in directory |
| `search_files` | Search for patterns in files |
| `delete_file` | Delete files or directories |

### How It Works

1. OpenCode sends tool definitions with your request
2. DeepSeekBridge converts tools to natural language instructions
3. DeepSeek responds with `<tool_call>` tags when it wants to use a tool
4. The bridge executes the tool locally
5. Results are sent back to DeepSeek for final response

### Workspace

Tools operate in the directory where you start the bridge. Just `cd` to your project before starting:

```bash
cd D:\MyProject
python main.py
```

Or use `DEEPSEEK_WORKSPACE` to set a fixed workspace:

```bash
set DEEPSEEK_WORKSPACE=D:\MyProject
python main.py
```

## Usage with AI Agents

This bridge works with **any OpenAI-compatible tool**. Here are instructions for popular AI agents:

### OpenCode

Add to your `opencode.json`:

```json
{
  "provider": {
    "deepseek": {
      "name": "DeepSeek",
      "api": "openai",
      "options": {
        "apiKey": "any",
        "baseURL": "http://localhost:8084/v1"
      },
      "models": {
        "deepseek-chat": {
          "id": "deepseek-chat",
          "name": "DeepSeek V3",
          "family": "deepseek"
        },
        "deepseek-reasoner": {
          "id": "deepseek-reasoner",
          "name": "DeepSeek R1",
          "family": "deepseek"
        }
      }
    }
  }
}
```

### Cursor

1. Open Cursor Settings → Models
2. Add Custom Model:
   - Name: `DeepSeek V3`
   - API Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `DeepSeek V3` from model dropdown

### Continue (VS Code Extension)

Add to `~/.continue/config.json`:

```json
{
  "models": [
    {
      "title": "DeepSeek V3",
      "provider": "openai",
      "model": "deepseek-chat",
      "apiBase": "http://localhost:8084/v1",
      "apiKey": "any"
    },
    {
      "title": "DeepSeek R1",
      "provider": "openai",
      "model": "deepseek-reasoner",
      "apiBase": "http://localhost:8084/v1",
      "apiKey": "any"
    }
  ]
}
```

### Cline (VS Code Extension)

1. Open Cline extension settings
2. Add new provider:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner` as model

### Roo Code (VS Code Extension)

1. Open Roo Code settings
2. Add custom API provider:
   - Provider Type: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select model in chat

### Aider

```bash
aider --model openai/deepseek-chat --openai-api-base http://localhost:8084/v1
```

### Any OpenAI-Compatible Tool

Use these settings:
- **Base URL:** `http://localhost:8084/v1`
- **API Key:** `any`
- **Model:** `deepseek-chat` or `deepseek-reasoner`

**Auto-start bridge from any agent:**
```bash
python bridge-status.py --start
```

### Google Antigravity

1. Open Antigravity settings
2. Add custom model provider:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### Claude Code

1. Open Claude Code settings
2. Add custom model:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### OpenAI Codex

1. Open Codex settings
2. Add custom model:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### Windsurf (Codeium)

1. Open Windsurf Settings → Models
2. Add Custom Model:
   - Name: `DeepSeek V3`
   - API Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `DeepSeek V3` from model dropdown

### Amazon Q Developer

1. Open Amazon Q settings
2. Add custom model provider:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### JetBrains AI Assistant

1. Open JetBrains IDE → Settings → Tools → AI Assistant
2. Add custom model:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### Sourcegraph Cody

1. Open Cody settings
2. Add custom model provider:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### Tabnine

1. Open Tabnine settings
2. Add custom model:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### LM Studio

1. Open LM Studio
2. Go to Developer tab
3. Add custom model:
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
4. Select `deepseek-chat` or `deepseek-reasoner`

### Ollama (with OpenAI-compatible mode)

1. Configure Ollama to use custom endpoint
2. Set base URL: `http://localhost:8084/v1`
3. Set API key: `any`
4. Select `deepseek-chat` or `deepseek-reasoner`

### Open WebUI

1. Open Open WebUI settings
2. Add custom model provider:
   - Provider: `OpenAI Compatible`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### LibreChat

1. Open LibreChat settings
2. Add custom endpoint:
   - Name: `DeepSeek Bridge`
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### ChatGPT Desktop (with custom endpoint)

1. Open ChatGPT Desktop settings
2. Add custom API endpoint:
   - Base URL: `http://localhost:8084/v1`
   - API Key: `any`
3. Select `deepseek-chat` or `deepseek-reasoner`

### Model Mapping

| Model Name | DeepSeek Model |
|------------|----------------|
| `deepseek-chat` | DeepSeek-V3 |
| `deepseek-reasoner` | DeepSeek-R1 |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | Send chat completion |
| `/v1/chats` | GET | List chats in sidebar |
| `/v1/chats/switch` | POST | Switch to a chat |
| `/v1/reset` | POST | Start new chat |
| `/v1/owner` | GET | Owner attribution info |
| `/v1/update-check` | GET | Check for updates |

## Troubleshooting

**Port already in use:**
```
taskkill /F /IM python.exe
```

**Session expired:**
Delete `browser_profile/` and login again.

**Login issues:**
- You need a **DeepSeek account** (not Google/Gmail)
- Create free account at https://chat.deepseek.com
- DeepSeek supports: Email, Phone, Google, GitHub sign-in

**Chrome not opening:**
Make sure Python and Playwright are installed:
```
pip install -r requirements.txt
python -m playwright install chromium
```

**Tools not working:**
- Make DeepSeek is in "Chat" mode (not "Write" mode)
- The model needs to output `<tool_call>` tags - some conversations may not trigger tool use
- Try starting a new chat with `/v1/reset`

**Workspace directory:**
Tools operate in the directory where you start the bridge. Just `cd` to your project first:
```bash
cd D:\MyProject
python main.py
```

## Security

- Your login tokens stay in `browser_profile/` (local only)
- `.gitignore` excludes `browser_profile/` from git
- Never commit your `browser_profile/` folder

## License

MIT
