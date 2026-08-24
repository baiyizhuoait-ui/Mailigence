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

### 🤖 AI 智能
- **AI 智能分析** — 每封邮件自动分类（工作 / 会议 / 财务 / 通知 / 广告…）、优先级打分、生成处理建议；可选纯规则模式（零成本离线运行）
- **AI 记忆系统** — 在设置页的聊天框直接告诉 AI 你的偏好（如"亚马逊促销都当广告处理"、"老板的邮件置顶"），AI 会提炼成记忆并应用到所有后续分析，越用越懂你
- **动态类别** — 无需手动预设，AI 会自动发现邮件中出现的新类别并创建；也可手动添加 / 重命名 / 删除类别，删除后该类别的邮件自动重新排队分类
- **日程提取** — 从邮件正文中识别会议、截止、预约等安排，理解"明天 / 下周一 / 15:00"等自然语言表达，按 今天 / 明天 / 本周 / 即将到来 分组展示
- **AI 配置管理** — 可同时保存多套 Provider 配置（云端 / 本地并存，如 DeepSeek、本地 Ollama、LM Studio），一键切换「当前使用」，模型列表自动探测
- **AI 回复草稿** — 读信时一键生成 1-2 个可编辑的回复草稿，复制正文或跳转原邮箱粘贴发送
- **AI 邮件问答** — 跨全部邮箱用自然语言提问（如"学校最近有什么活动？"），基于检索到的邮件片段作答并附引用邮件卡片，点击直达详情

### 🔍 搜索
- **全文检索** — 顶栏全局搜索框（任意页面可用），300ms 防抖实时预览，独立结果页支持账户 / 类别 / 日期筛选，命中摘要高亮（`<mark>`）
- **语义检索（可选）** — 配置 embedding 模型后自动启用向量召回，解决"主题相关但字面不重叠 / 跨语言"的检索短板；三路召回（关键词 + 向量 + 结构化过滤）用 RRF 融合排序

### 📬 阅读与处理
- **多账户聚合** — 一个界面管理所有邮箱账户（Gmail / Outlook / QQ / 163 / 任意 IMAP），支持应用专用密码与 OAuth2；导入历史邮件时保留原始已读状态
- **批量操作** — 收件箱跨账户全选邮件，一键已读 / 归档 / 标星，与主流邮箱一致的高效操作
- **全文查看** — 无需登录原邮箱，点开邮件即可在安全沙箱中阅读完整正文（按需从服务器拉取）
- **待办仪表盘** — 便当盒布局：AI 每日简报 + 日程时间线 + 统计 + 优先级队列；已处理的邮件自动移出队列，旧邮件自动归档
- **优先级队列** — 点击队列里的邮件即可直接展开阅读，不必再跳回收件箱
- **实时同步** — IMAP IDLE 实时推送新邮件；不支持的服务器自动 2 分钟轮询；后台每 60 秒自动补扫漏分析 / 未分类的邮件
- **回复追踪** — 识别需要回复的邮件，一键跳转处理

### 🎨 个性化
- **11 种主题色 + 自定义调色板** — 琥珀 / 湖蓝 / 翠绿 / 紫罗兰 / 珊瑚红 / 青碧 / 靛蓝 / 樱花粉 / 活力橙 / 青色 / 岩灰，还可直接用调色板取任意颜色
- **自定义颜色** — 每个邮箱账户与邮件类别都可单独设置颜色，一眼分清邮件来源与归属
- **深浅主题 · 双语界面** — 深色 / 浅色一键切换，中文 / English 无缝切换

### 📊 数据与管控
- **广告过滤** — 自动识别营销广告，支持黑名单管理，广告面板一键批量清理
- **数据统计** — 按天 / 周 / 月查看邮件分布、优先级构成与发件人排行

## 🧱 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · psycopg 3 |
| 前端 | React 18 · TypeScript · Vite |
| 数据库 | PostgreSQL 14+ |
| 邮件 | IMAP (imaplib) · IMAP IDLE |
| AI | OpenAI 兼容 / Anthropic / 任意本地推理服务（Ollama · LM Studio · vLLM · llama.cpp）· 多配置并存一键切换 |

## 🚀 快速开始

本仓库提供两条互不干扰的启动方式，任选其一：

| 方式 | 适用 | 命令 / 操作 |
|---|---|---|
| **跨平台 Docker 一键启动**（推荐） | macOS / Linux / Windows（需 Docker） | `docker compose up -d` |
| **Windows 免 Docker 一键启动** | Windows（无需 Docker） | 双击 `start.bat` |

### Docker 一键启动（跨平台推荐）

环境要求：**Docker 24+**（自带 compose 插件）。

无需安装 Python / Node.js / PostgreSQL，一条命令拉起全部三个服务（数据库 + 后端 + 前端）：

```bash
# 1. 克隆仓库
git clone <仓库地址> && cd Mailigence

# 2. 准备环境变量（AI Key、OAuth 等按需填写；留空也能以纯规则模式运行）
cp .env.docker.example .env.docker

# 3. 一键启动（首次会构建镜像，稍等片刻）
docker compose up -d
```

启动后访问：

- 前端：**http://localhost:5173**
- 后端 API：http://localhost:8000（健康检查 http://localhost:8000/api/health）
- 数据库：localhost:5432（`mailigence` / `mailigence_dev_pw` / `mailigence`）

说明：

- **自动建表**：后端容器启动时自动创建全部数据表、内置分类并执行增量迁移，无需手动执行 SQL。
- **加密密钥**：若 `CREDENTIAL_ENCRYPTION_KEY` 留空，后端容器会在**首次启动**时自动生成并持久化到数据卷，之后重启不会重新生成（否则已加密的邮箱凭据会全部失效）。
- **数据持久化**：PostgreSQL 数据与加密密钥分别存于命名卷 `mailigence_pgdata`、`mailigence_backend_env`。
- 查看日志：`docker compose logs -f backend`
- 停止（保留数据）：`docker compose down`
- 彻底清空（删库，慎用）：`docker compose down -v`
- 若之前运行过旧版（仅数据库）的 docker-compose，请先 `docker compose down` 再启动新版，避免容器名冲突。

### Windows 免 Docker 一键启动（start.bat / start.ps1）

无需 Docker，环境要求：**Python 3.11+**、**Node.js 18+**（安装时勾选加入 PATH）

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

# ---- 语义搜索（可选，未配置则检索退化为纯关键词） ----
AI_EMBEDDING_MODEL=          # 留空自动用 text-embedding-3-small（需 PostgreSQL 装 pgvector）
EMBEDDING_DIM=1536           # 与所选 embedding 模型维度一致（nomic-embed-text=768）
```

## 🤖 AI 配置管理（多配置并存）

设置页 →「设置 → AI 邮件分析」→「AI 配置管理」：可以把**云端与本地多套 Provider 同时保存**下来，随时切换，互不覆盖。

### 基本流程

1. **新建配置**：点击「新建配置」，填写：
   - **名称**：任意自定义，如 `DeepSeek 云端` / `本地 Ollama` / `本地 LM Studio`
   - **类型**：`OpenAI 兼容`（OpenAI / DeepSeek / Kimi / 通义 / GLM / **Ollama / LM Studio / vLLM / llama.cpp / oneAPI** 等全部走此类型）｜ `Anthropic` ｜ `规则模式（无 AI）`
   - **接口地址（Base URL）**：自由文本，如 `https://api.deepseek.com/v1` 或 `http://localhost:11434/v1`
   - **模型名称**：可手动输入；也可点「**探测模型列表**」自动从 `{Base URL}/models` 拉取候选（点击填入，仍可手改；探测失败不影响手动输入）
   - **API 密钥**：云端必填；本地无需鉴权的服务（Ollama / LM Studio）可**留空**
2. **保存**：第一个配置自动成为「当前使用」；之后新建的配置需点卡片上的「**切换为当前使用**」激活
3. **测试连接**：卡片上的「测试连接」请求 `{Base URL}/models` 验证连通性（任意类型都支持）
4. **编辑 / 删除**：随时修改连接信息；删除当前生效配置后自动切换到剩余配置中的第一个
5. 新建时切换类型，表单会自动带出**该类型上次填写**的 Base URL / 模型，无需重输

> 说明：Base URL 与模型名**不做任何下拉限定**，探测只是辅助手段——接口不支持探测或探测失败时，直接手动输入即可保存使用。

### 三种分析模式（全局）

- **智能模式（推荐）**：配置了 AI 就用 AI，失败或未配置时自动回退到规则
- **纯 AI 模式**：始终调用 AI
- **纯规则模式**：不调用 AI，纯程序分析（关键词/头规则），零成本零依赖

> 保存的 API 密钥会**加密**存储于数据库；编辑时密钥留空则保留原密钥。修改配置后分析缓存自动失效。

### 常见云端接入参考

| Provider | Base URL | 模型示例 |
|---|---|---|
| OpenAI 官方 | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Moonshot (Kimi) | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Anthropic Claude | `https://api.anthropic.com/v1` | `claude-sonnet-4-...` |

### 本地 Ollama 配置教程（免费、离线、隐私）

1. **安装 Ollama**：到官网 https://ollama.com/ 下载对应系统安装包（Windows 双击安装 / macOS 拖动安装 / Linux 执行 `curl -fsSL https://ollama.com/install.sh | sh`）
2. **拉取一个模型**（邮箱分析用中档模型即可，例如）：

   ```bash
   ollama pull qwen2.5:7b
   ollama list          # 查看已安装模型
   ```

3. **确认服务已启动**：Ollama 默认监听 `http://localhost:11434`，其 **OpenAI 兼容端点**是 `http://localhost:11434/v1`。浏览器直接打开 `http://localhost:11434/v1/models` 能返回 JSON 即说明可用
4. **在 Mailigence 里新建配置**：
   - 名称：`本地 Ollama`
   - 类型：**OpenAI 兼容**
   - 接口地址：`http://localhost:11434/v1`
   - 模型：`qwen2.5:7b`（或点「探测模型列表」自动带出）
   - API 密钥：**留空**（本地无需鉴权）
5. **保存 →「切换为当前使用」**：之后分类 / 优先级 / 日程提取 / 回复草稿 / 聊天问答全部走本地模型，断网也可用。

> **语义搜索也用本地模型（可选）**：Ollama 同样提供 OpenAI 兼容的 `/v1/embeddings` 接口。拉取 `ollama pull nomic-embed-text`，在「语义搜索模型（可选）」填 `nomic-embed-text` 即可。注意该模型向量维度为 **768**（默认 text-embedding-3-small 为 1536）：若数据库是全新初始化，先在 `backend/.env` 设 `EMBEDDING_DIM=768` 再启动；若已初始化过，需删除 embedding 列后重启（向量会自动重新生成）。

> **其他本地推理服务同理**：LM Studio（地址 `http://localhost:1234/v1`）、vLLM / llama.cpp server / oneAPI 等只要实现了 OpenAI 兼容 `/v1` 接口，用同一套「OpenAI 兼容」配置即可，无需任何工具专属设置。

### 本地小模型 / 思考型模型适配

**现象**：用本地小模型（尤其思考型模型，如 qwen3 系列）时，生成回复草稿 / 聊天问答极慢（几十秒到数分钟），或提示"AI 返回的回复草稿无法解析"、输出为空。

**根因**：思考型模型经 OpenAI 兼容 `/v1/chat/completions` 接口调用时，会先输出一大段"思维链（reasoning）"，直接耗尽 `max_tokens` 预算——正文根本没来得及输出（`content` 为空），或 JSON 被截断，于是解析失败；时间也全耗在思考上。

**系统自动处理**：

- 检测到本地 Ollama（端口 11434）时，自动改走其**原生 `/api/chat` 接口并传 `think: false` 关闭思考**（实测 46 秒 → 3.5 秒）
- 同时上调了 token 预算（草稿 / 问答 2048）与超时（120 秒），并简化提示词
- 提供 JSON 容错解析：自动修复尾逗号、字符串内裸换行，并兼容单条对象包装等输出形状

**若仍慢 / 仍失败，请按顺序排查**：

1. `ollama list` 核对设置中的模型名是否**完全一致**（大小写与 `:tag`，如 `qwen3:8b`，不能只写 `qwen3`）
2. 确认 Ollama 服务已启动（浏览器打开 `http://localhost:11434/v1/models` 能返回 JSON）
3. 换**上下文更大**的模型（如 `qwen2.5:14b` / `qwen3:14b`），`ollama show <模型>` 可查看上下文长度
4. 查看后端日志中 `LLM HTTP ...` 报错后的提示文字，会区分"输入过长"与"模型名无效"两类原因

## 📁 项目结构

```
mailigence/
├── start.bat / start.ps1      # Windows 免 Docker 一键启动（自动初始化数据库与依赖）
├── stop.bat / stop.ps1        # 一键停止（stop.ps1 -StopDb 连数据库一起停）
├── docker-compose.yml         # 跨平台 Docker 一键启动：postgres + backend + frontend
├── .env.docker.example        # Docker 部署的环境变量模板（cp 为 .env.docker 使用）
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI 路由（accounts/dashboard/settings/reports…）
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── schemas/      # Pydantic 模型
│   │   └── services/     # 邮件同步、AI 分析、IMAP IDLE、加密…
│   ├── Dockerfile        # 后端镜像（python:3.11-slim，psycopg[binary] 免编译）
│   ├── entrypoint.sh     # 容器启动脚本（首次自动生成并持久化加密密钥）
│   ├── run.py            # 本地启动脚本（Windows 兼容）
│   ├── .env.example      # 环境变量模板（本地开发）
│   └── requirements.txt
├── frontend/
│   ├── Dockerfile        # 多阶段构建（node 构建 → nginx 托管静态文件）
│   ├── nginx.conf        # SPA 路由 fallback + /api 反向代理到 backend
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

**Ollama 怎么用？** 完整教程见上方「[本地 Ollama 配置教程](#本地-ollama-配置教程免费离线隐私)」：安装 Ollama → `ollama pull qwen2.5:7b` → 设置页「AI 配置管理」新建「OpenAI 兼容」配置，接口地址填 `http://localhost:11434/v1`，密钥留空，保存后切换为当前使用即可。

**本地模型生成很慢 / 提示草稿无法解析？** 见上方「本地小模型 / 思考型模型适配」：思考型模型（如 qwen3）会把 token 预算全耗在思维链上，系统已对 Ollama 自动关闭思考并加大预算 / 超时；若仍失败，用 `ollama list` 核对模型名（大小写与 `:tag`），或换上下文更大的模型。

**语义搜索（向量检索）为什么不可用？** 需要 PostgreSQL 安装 **pgvector** 扩展。Docker 部署已内置（`pgvector/pgvector:pg16` 镜像）；本机便携版需自行安装 pgvector，否则启动日志会提示 `pgvector not available — semantic search disabled`，检索自动退化为纯关键词（聊天问答会在 system prompt 里如实说明"当前使用关键词检索"），不影响其他功能。

**搜索不到语义相关但没字面关键词的邮件？** 该场景正是「语义检索」要解决的：配置好 embedding 模型（见上方语义搜索说明）并确保 pgvector 可用后，检索会自动升级为"关键词 + 向量"双路召回。

**端口冲突？** 后端默认 8000、前端 5173，可在 `.env` 的 `APP_PORT` 和 `vite.config.ts` 中修改。

**一键启动脚本提示找不到 PostgreSQL？** 把便携版解压到 `\.pginstall\pgsql` 后重试，或安装 PostgreSQL 并加入 PATH。

**数据库端口被占用/被防火墙拦截？** 可在 `backend\.env` 的 `DATABASE_URL` 中修改端口（如 `@127.0.0.1:5433/`），启动脚本会自动读取 .env 中的端口，无需额外配置。

**如何迁移/备份数据？** 便携版数据目录在 `\.pginstall\pgdata`，直接复制该目录即可整体迁移；或用 `pg_dump` 导出。

**macOS/Linux 怎么跑？** 手动方式见上方「手动启动」；**推荐**使用根目录的 `docker compose up -d` 一键启动全部服务（见「Docker 一键启动（跨平台推荐）」）。

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
