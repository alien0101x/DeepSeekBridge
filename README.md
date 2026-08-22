# DeepSeekBridge

OpenAI-compatible bridge to [chat.deepseek.com](https://chat.deepseek.com) using browser automation. Use DeepSeek models in any OpenAI-compatible tool.

## Features

- 🔌 OpenAI API compatible (`/v1/chat/completions`)
- 🧠 Supports DeepSeek-V3 and DeepSeek-R1
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

## Troubleshooting

**Port already in use:**
```
taskkill /F /IM python.exe
```

**Session expired:**
Delete `browser_profile/` and login again.

**Chrome not opening:**
Make sure Python and Playwright are installed:
```
pip install -r requirements.txt
python -m playwright install chromium
```

## Security

- Your login tokens stay in `browser_profile/` (local only)
- `.gitignore` excludes `browser_profile/` from git
- Never commit your `browser_profile/` folder

## License

MIT
