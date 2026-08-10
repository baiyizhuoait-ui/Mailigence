[English](./README.en.md) | 简体中文

# Mailigence — 多平台邮件聚合分析仪表盘

> 把散落在各邮箱的信息，汇聚成一份智能行动清单。

Mailigence 是一个自托管的邮件聚合与 AI 分析工具。将 Gmail / Outlook / QQ 邮箱 / 163 等任意支持 IMAP 的邮箱统一聚合到一个界面，用 AI（或纯规则）自动完成**分类、优先级排序、待办队列、日程提取与每日摘要**——早上看一眼，今天要做什么一目了然。

<p>
  <img src="https://img.shields.io/badge/stack-FastAPI%20%2B%20React%20%2B%20PostgreSQL-4a9eff" alt="stack">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
  <img src="https://img.shields.io/badge/PRs-welcome-orange" alt="prs">
</p>

## ✨ 功能特性

- **多账户聚合** — 一个界面管理所有邮箱账户（Gmail / Outlook / QQ / 163 / 任意 IMAP），支持应用专用密码与 OAuth2
- **AI 智能分析** — 每封邮件自动分类（工作/会议/财务/通知/广告…）、优先级打分、处理建议；可选纯规则模式（零成本离线运行）
- **待办仪表盘** — 双栏便当盒布局：左侧「AI 每日简报 + 日程时间线」，右侧「统计 + 优先级队列」；已处理的邮件自动移出队列，旧邮件自动归档
- **日程提取** — AI 从邮件中提取会议/截止/预约，按 今天 / 明天 / 本周 / 即将到来 分组展示
- **实时同步** — IMAP IDLE 实时推送新邮件；不支持的服务器自动 2 分钟轮询；也可手动强制同步
- **回复追踪** — 识别需要回复的邮件，一键跳转处理
- **广告过滤** — 自动识别营销广告，支持黑名单管理
- **数据统计** — 按天/周/月查看邮件分布、优先级、发件人排行
- **双语界面** — 中文 / English，深色/浅色主题，5 种主题色

## 🧱 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · psycopg 3 |
| 前端 | React 18 · TypeScript · Vite |
| 数据库 | PostgreSQL 14+ |
| 邮件 | IMAP (imaplib) · IMAP IDLE |
| AI | OpenAI 兼容 API / Anthropic / Ollama（可切换） |

## 🚀 快速开始

### Windows 一键启动（推荐）

环境要求：**Python 3.11+**、**Node.js 18+**（安装时勾选加入 PATH）

1. 双击项目根目录的 `start.bat`（或运行 `.\start.ps1`）
2. 首次运行脚本会自动完成以下全部步骤：
   - 检测 PostgreSQL —— 优先使用项目内置便携版 `\.pginstall\pgsql`，没有则用系统安装版
   - 首次自动 `initdb` 初始化数据目录并启动数据库
   - 自动创建 `mailigence` 角色与数据库（已有则跳过）
   - 自动创建 Python 虚拟环境并安装后端依赖
   - 自动生成 `backend\.env` 与加密密钥
   - 自动安装前端依赖
   - 启动后端(:8000) + 前端(:5173)，打开浏览器 → http://localhost:5173

停止服务：双击 `stop.bat`（运行 `.\stop.ps1 -StopDb` 可连同数据库一起停止）。

> **便携版 PostgreSQL 获取方式**：下载
> `https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64-binaries.zip`
> 解压后把 `pgsql` 目录放到 `\.pginstall\pgsql` 即可，脚本会自动识别。

### 手动启动（macOS / Linux / 高级用户）

环境要求：Python 3.11+、Node.js 18+、PostgreSQL 14+

### 1. 数据库

```bash
# 创建数据库和用户（以 PostgreSQL 为例）
psql -U postgres -c "CREATE USER mailigence WITH PASSWORD 'mailigence_dev_pw';"
psql -U postgres -c "CREATE DATABASE mailigence OWNER mailigence;"
```

### 2. 后端

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 然后编辑 .env，见下方「配置」
# 生成加密主密钥（必填，用于加密邮箱凭据）
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 启动（首次启动自动建表；Windows 用户务必用下面的 run.py，避免 ProactorEventLoop 与 psycopg 不兼容）
python run.py
# 或：uvicorn app.main:app --reload --port 8000
```

### 3. 前端

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

打开 http://localhost:5173 ，添加邮箱账户即可开始使用。

## ⚙️ 配置（backend/.env）

参考 `.env.example`，关键项：

```ini
# 数据库连接（对应上面创建的库；psycopg 驱动，127.0.0.1 避免 IPv6 解析问题，
# sslmode=disable 防止便携版 PostgreSQL 在 Windows 上因 SSLRequest 崩溃）
DATABASE_URL=postgresql+psycopg://mailigence:mailigence_dev_pw@127.0.0.1:5432/mailigence?sslmode=disable

# 加密主密钥 —— 必须设置，用于加密邮箱密码和 OAuth 凭据
# 生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CREDENTIAL_ENCRYPTION_KEY=

# ---- AI 分析（可选，不配置则用纯规则模式） ----
# 分析模式：auto（AI优先，失败回退规则）| ai_only | rules_only
AI_ANALYSIS_MODE=auto
# 接入方式：openai（OpenAI 兼容：OpenAI/DeepSeek/Kimi/通义/GLM/Ollama）| anthropic
AI_PROVIDER=openai
AI_API_KEY=
AI_BASE_URL=https://api.deepseek.com/v1     # 以 DeepSeek 为例
AI_MODEL=deepseek-chat
```

## 🤖 AI 接入方式

设置页（设置 → AI 邮件分析）内置了主流接入方式，选择后自动填充地址：

| 接入方式 | Base URL 示例 | 模型示例 | 说明 |
|---|---|---|---|
| OpenAI 官方 | `https://api.openai.com/v1` | `gpt-4o-mini` | 效果最好，需海外支付 |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | 国内直连，性价比高 |
| Moonshot (Kimi) | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | 国内直连 |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | 阿里云，国内直连 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | 国内直连，有免费额度 |
| Ollama 本地 | `http://localhost:11434/v1` | `qwen2.5:7b` | 免费、离线、隐私 |
| Anthropic Claude | `https://api.anthropic.com/v1` | `claude-sonnet-4-...` | 海外 |

**三种分析模式：**

- **智能模式（推荐）**：配置了 AI 就用 AI，失败或未配置时自动回退到规则
- **纯 AI 模式**：始终调用 AI
- **纯规则模式**：不调用 AI，纯程序分析（关键词/头规则），零成本零依赖

> 在设置页保存的 API 密钥会**加密**存储于数据库；也可以选择不填密钥，改用 `.env` 中的 `AI_API_KEY`。修改设置后分析缓存自动失效。

## 📁 项目结构

```
mailigence/
├── start.bat / start.ps1      # Windows 一键启动（自动初始化数据库与依赖）
├── stop.bat / stop.ps1        # 一键停止（stop.ps1 -StopDb 连数据库一起停）
├── docker-compose.yml         # 可选：仅启动 PostgreSQL 容器
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI 路由（accounts/dashboard/settings/reports…）
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── schemas/      # Pydantic 模型
│   │   └── services/     # 邮件同步、AI 分析、IMAP IDLE、加密…
│   ├── run.py            # Windows 兼容启动脚本（推荐）
│   ├── .env.example      # 环境变量模板
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/   # React 组件
│   │   ├── api.ts        # 后端 API 封装
│   │   └── i18n.tsx      # 中英文文案
│   └── package.json
└── .gitignore
```

## ❓ 常见问题

**启动报 `Psycopg cannot use the 'ProactorEventLoop'`？** Windows 上 Python 3.13 默认使用 ProactorEventLoop，与 psycopg 异步模式不兼容，且 uvicorn 会强制覆盖事件循环。请用 `python run.py` 启动（脚本已固定 SelectorEventLoop）。

**`pip install -r requirements.txt` 安装失败？** 依赖使用 `psycopg[binary]`，自带各平台（含 Windows / Python 3.13）的预编译 wheel，无需 MSVC 编译工具链。

**数据库连接失败（IPv6 / SSLRequest）？** 确保 `.env` 中 `DATABASE_URL` 使用 `127.0.0.1` 而非 `localhost`，并保留 `?sslmode=disable`（`app/database.py` 的 `connect_args` 也会强制禁用 SSL）。

**邮箱添加失败？** 大部分邮箱需要用「授权码/应用专用密码」而非登录密码登录（163/QQ 需在网页设置里开启 IMAP 并生成授权码）。

**没有 AI 密钥能用吗？** 可以。选择「纯规则模式」或用默认的智能模式（未配置 AI 时自动用规则），所有功能（分类/优先级/日程提取）都有规则版兜底实现。

**Ollama 怎么用？** 安装 [Ollama](https://ollama.com/) → `ollama pull qwen2.5:7b` → 在设置页选择「Ollama 本地模型」即可。

**端口冲突？** 后端默认 8000、前端 5173，可在 `.env` 的 `APP_PORT` 和 `vite.config.ts` 中修改。

**一键启动脚本提示找不到 PostgreSQL？** 把便携版解压到 `\.pginstall\pgsql` 后重试，或安装 PostgreSQL 并加入 PATH。

**数据库端口被占用/被防火墙拦截？** 可在 `backend\.env` 的 `DATABASE_URL` 中修改端口（如 `@127.0.0.1:5433/`），启动脚本会自动读取 .env 中的端口，无需额外配置。

**如何迁移/备份数据？** 便携版数据目录在 `\.pginstall\pgdata`，直接复制该目录即可整体迁移；或用 `pg_dump` 导出。

**macOS/Linux 怎么跑？** 手动方式见上方「手动启动」；也可以使用根目录的 `docker-compose.yml` 只启动数据库（`docker compose up -d postgres`）。

## 🔒 安全说明

- 邮箱密码 / OAuth 凭据使用 Fernet 加密存储（主密钥在 `CREDENTIAL_ENCRYPTION_KEY`）
- API 密钥同样加密存储；`.env` 已被 `.gitignore` 排除，切勿提交真实密钥
- 明文凭据仅在建立 IMAP 连接的瞬间存在于内存中

## 📄 License

MIT

---

<p align="center">
  用 💛 构建 · <a href="https://github.com/baiyizhuoait-ui/Mailigence">GitHub</a>
</p>
