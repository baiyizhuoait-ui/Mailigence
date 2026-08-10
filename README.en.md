English | [简体中文](./README.md)

# Mailigence — AI-Powered Multi-Account Email Dashboard

> Turn scattered inboxes into one intelligent action list.

Mailigence is a self-hosted email aggregation and AI analysis tool. Connect Gmail, Outlook, QQ Mail, 163, or any IMAP-compatible mailbox into a single dashboard, and let AI (or pure rule-based logic) automatically handle **classification, priority scoring, a to-do queue, schedule extraction, and daily digests** — check it once in the morning and know exactly what needs your attention today.

<p>
  <img src="https://img.shields.io/badge/stack-FastAPI%20%2B%20React%20%2B%20PostgreSQL-4a9eff" alt="stack">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
  <img src="https://img.shields.io/badge/PRs-welcome-orange" alt="prs">
</p>

## ✨ Features

- **Multi-account aggregation** — Manage all your mailboxes (Gmail / Outlook / QQ Mail / 163 / any IMAP server) from one dashboard, with support for both app-specific passwords and OAuth2
- **AI-powered analysis** — Every email is automatically classified (work / meetings / finance / notifications / ads…), scored by priority, and given a suggested action. A pure rule-based mode is also available for zero-cost, fully offline operation
- **To-do dashboard** — A two-column bento layout: an AI daily briefing + schedule timeline on the left, statistics + a priority queue on the right. Handled emails automatically drop out of the queue, and older ones auto-archive
- **Schedule extraction** — AI pulls meetings, deadlines, and appointments out of your emails and groups them into Today / Tomorrow / This Week / Upcoming
- **Real-time sync** — IMAP IDLE pushes new emails instantly; servers that don't support IDLE fall back to 2-minute polling. Manual force-sync is also available
- **Reply tracking** — Identifies emails that still need a reply, with one-click jump to respond
- **Ad filtering** — Automatically detects marketing/promotional email, with support for a manual blocklist
- **Analytics** — View email volume, priority distribution, and top senders by day/week/month
- **Bilingual UI** — Chinese / English, light/dark themes, 5 accent color options

## 🧱 Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · psycopg 3 |
| Frontend | React 18 · TypeScript · Vite |
| Database | PostgreSQL 14+ |
| Email | IMAP (imaplib) · IMAP IDLE |
| AI | OpenAI-compatible API / Anthropic / Ollama (swappable) |

## 🚀 Quick Start

### Windows — one-click start (recommended)

Requirements: **Python 3.11+** and **Node.js 18+** (add both to PATH during install).

1. Double-click `start.bat` (or run `.\start.ps1`) in the project root.
2. On first run the script automatically:
   - Detects PostgreSQL (prefers the bundled portable build at `\.pginstall\pgsql`, falls back to a system install)
   - Runs `initdb` and starts the database on first launch
   - Creates the `mailigence` role and database if missing
   - Creates a Python venv and installs backend dependencies
   - Generates `backend\.env` with a fresh encryption key
   - Installs frontend dependencies
   - Starts the backend (:8000) + frontend (:5173) and opens the browser

To stop: double-click `stop.bat` (run `.\stop.ps1 -StopDb` to also stop the database).

> **Portable PostgreSQL**: download
> `https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64-binaries.zip`,
> unzip it and place the `pgsql` folder at `\.pginstall\pgsql`.

### Manual start (macOS / Linux / advanced)

### Requirements

- Python 3.11+
- Node.js 18+
- PostgreSQL 14+

### 1. Database

```bash
# Create the database and user (PostgreSQL example)
psql -U postgres -c "CREATE USER mailigence WITH PASSWORD 'mailigence_dev_pw';"
psql -U postgres -c "CREATE DATABASE mailigence OWNER mailigence;"
```

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then edit .env — see "Configuration" below
# Generate the encryption master key (required, used to encrypt mailbox credentials)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Start the server (tables are created automatically on first run)
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

Open http://localhost:5173, add a mailbox account, and you're ready to go.

## ⚙️ Configuration (backend/.env)

See `.env.example` for the full template. Key settings:

```ini
# Database connection (matches the database created above)
DATABASE_URL=postgresql+asyncpg://mailigence:mailigence_dev_pw@localhost:5432/mailigence

# Encryption master key — required, used to encrypt mailbox passwords and OAuth credentials
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CREDENTIAL_ENCRYPTION_KEY=

# ---- AI analysis (optional — falls back to rule-based mode if unset) ----
# Analysis mode: auto (AI first, falls back to rules on failure) | ai_only | rules_only
AI_ANALYSIS_MODE=auto
# Provider: openai (OpenAI-compatible: OpenAI / DeepSeek / Kimi / Qwen / GLM / Ollama) | anthropic
AI_PROVIDER=openai
AI_API_KEY=
AI_BASE_URL=https://api.deepseek.com/v1     # example: DeepSeek
AI_MODEL=deepseek-chat
```

## 🤖 Supported AI Providers

The settings page (Settings → AI Email Analysis) has built-in presets for common providers — pick one and the endpoint auto-fills:

| Provider | Base URL example | Model example | Notes |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | Best quality, requires international billing |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | Direct access in China, cost-effective |
| Moonshot (Kimi) | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | Direct access in China |
| Qwen (Tongyi) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | Alibaba Cloud, direct access in China |
| Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | Direct access in China, free tier available |
| Ollama (local) | `http://localhost:11434/v1` | `qwen2.5:7b` | Free, offline, private |
| Anthropic Claude | `https://api.anthropic.com/v1` | `claude-sonnet-4-...` | International |

**Three analysis modes:**

- **Smart mode (recommended)** — uses AI when configured, automatically falls back to rules on failure or when unconfigured
- **AI-only mode** — always calls the AI provider
- **Rules-only mode** — no AI calls at all; pure keyword/header-based analysis, zero cost and zero external dependency

> API keys saved in the settings page are stored **encrypted** in the database. You can also leave it blank and use `AI_API_KEY` from `.env` instead. Changing settings automatically invalidates the analysis cache.

## 📁 Project Structure

```
mailigence/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routes (accounts/dashboard/settings/reports…)
│   │   ├── models/       # SQLAlchemy models
│   │   ├── schemas/      # Pydantic schemas
│   │   └── services/     # Email sync, AI analysis, IMAP IDLE, encryption…
│   ├── .env.example      # Environment variable template
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/   # React components
│   │   ├── api.ts        # Backend API client
│   │   └── i18n.tsx      # Chinese/English copy
│   └── package.json
└── .gitignore
```

## ❓ FAQ

**Adding a mailbox fails?** Most providers require an "authorization code" / app-specific password rather than your regular login password (for QQ Mail / 163, enable IMAP in the web settings and generate an authorization code first).

**Can I use it without an AI key?** Yes. Choose "rules-only mode", or just leave AI unconfigured and smart mode will fall back to rules automatically. Every feature (classification, priority, schedule extraction) has a rule-based fallback implementation.

**How do I use Ollama?** Install [Ollama](https://ollama.com/) → `ollama pull qwen2.5:7b` → select "Local Ollama Model" in the settings page.

**Port conflicts?** Backend defaults to 8000, frontend to 5173. Change these via `APP_PORT` in `.env` and in `vite.config.ts`.

## 🔒 Security Notes

- Mailbox passwords and OAuth credentials are encrypted at rest using Fernet (master key in `CREDENTIAL_ENCRYPTION_KEY`)
- AI API keys are encrypted at rest as well; `.env` is excluded via `.gitignore` — never commit real keys
- Plaintext credentials only exist in memory momentarily, while establishing an IMAP connection

## 📄 License

MIT

---

<p align="center">
  Built with 💛 · <a href="https://github.com/baiyizhuoait-ui/Mailigence">GitHub</a>
</p>
