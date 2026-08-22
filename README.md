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

## Usage with OpenCode

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

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | Send chat completion |
| `/v1/chats` | GET | List chats in sidebar |
| `/v1/chats/switch` | POST | Switch to a chat |
| `/v1/reset` | POST | Start new chat |

## Model Mapping

| OpenCode Model | DeepSeek Model |
|----------------|----------------|
| `deepseek-chat` | DeepSeek-V3 |
| `deepseek-reasoner` | DeepSeek-R1 |

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
