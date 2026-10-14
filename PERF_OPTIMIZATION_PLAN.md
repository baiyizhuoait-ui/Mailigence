# Mailigence 性能优化改造方案

> 来源：一份第三方性能分析报告的逐条核实（2026-10-14），全部 8 项断言经代码级验证属实，2 项细节需修正。
> 原则：每阶段独立可验证、可回滚；不新建 Python 环境；严格遵守项目既有约定（本地 Qwen 3.5 基准、改动前后对比、最优版本推 GitHub）。
> 决策记录：**保留 `full` 开屏动画（CRT 多窗口），只修"动画被慢接口卡住"的问题，不改默认模式为 simple/off。**

---

## 0. 核实结论

8 项断言全部成立。两处需要修正报告表述：

| 报告表述 | 实际情况 |
|---|---|
| 问题 4：后台"每分析完一批新邮件"就清空 schedule 缓存 | `invalidate_cache()` 由后台 analysis sweep（每 15s）跑完后触发，非逐批；且前端 30s 轮询有 fingerprint 门控，**仅指纹变化才真调 LLM**——报告暗示的"每 30 秒必调一次"偏重 |
| 问题 6：pending/schedule 只读接口"重复执行全表 UPDATE" | `auto_handle_emails` 只更新 `handled_at IS NULL` 且命中 4 类条件的行，**不是无条件全表写**；但每次请求仍执行一次扫描 + `commit()`（即使 `rowcount=0` 也提交空事务） |

### 代码级证据（已核实位置）

| # | 问题 | 证据位置 |
|---|---|---|
| 1 | 开屏动画硬编码 | [boot.ts:31-46](file:///d:/Projects/Web/Mailigence/frontend/src/boot.ts#L31-L46) `MIN_MS=7600`/`DESKTOP_AT=4150`/`BANNER_MS=1150`/`FADE_MS=550`；[boot.ts:162-165](file:///d:/Projects/Web/Mailigence/frontend/src/boot.ts#L162-L165) 默认 `full`；index.html:1054 同 |
| 2 | 三级串行瀑布 + 慢 AI 卡死 loading | [App.tsx:200-205](file:///d:/Projects/Web/Mailigence/frontend/src/App.tsx#L200-L205)；[DashboardView.tsx:105-136](file:///d:/Projects/Web/Mailigence/frontend/src/components/DashboardView.tsx#L105-L136)、[:235-237](file:///d:/Projects/Web/Mailigence/frontend/src/components/DashboardView.tsx#L235-L237) |
| 3 | localhost 回退 + dev 模式 + StrictMode | [vite.config.ts:11](file:///d:/Projects/Web/Mailigence/frontend/vite.config.ts#L10-L14)；[start.ps1:250](file:///d:/Projects/Web/Mailigence/start.ps1#L250-L251)；[main.tsx:12](file:///d:/Projects/Web/Mailigence/frontend/src/main.tsx#L11-L16) |
| 4 | schedule 请求内同步调 LLM + 缓存易失 | [schedule_analyzer.py:370](file:///d:/Projects/Web/Mailigence/backend/app/services/schedule_analyzer.py#L351-L374)（30 封×800 字、max_tokens 2000）；`_cache` 为进程内存字典 |
| 5 | 启动期冗余 DDL / echo=True / 立刻拉起 IDLE | [database.py:23](file:///d:/Projects/Web/Mailigence/backend/app/database.py#L21-L30)；[main.py:174-176](file:///d:/Projects/Web/Mailigence/backend/app/main.py#L154-L183) |
| 6 | 5 次串行查询 / 只读接口重复写 / N+1 | [dashboard.py:149-197](file:///d:/Projects/Web/Mailigence/backend/app/api/dashboard.py#L149-L197)；[analysis_service.py:191](file:///d:/Projects/Web/Mailigence/backend/app/services/analysis_service.py#L157-L191)；[mail_sync.py:133-148](file:///d:/Projects/Web/Mailigence/backend/app/services/mail_sync.py#L133-L148)、[:319-324](file:///d:/Projects/Web/Mailigence/backend/app/services/mail_sync.py#L319-L324) |
| 7 | 每次全文/LLM 新建连接，正文无缓存 | [emails.py:143-190](file:///d:/Projects/Web/Mailigence/backend/app/api/emails.py#L143-L190)；[models/email.py:71-72](file:///d:/Projects/Web/Mailigence/backend/app/models/email.py#L71-L72) 仅 `body_snippet`；`httpx.AsyncClient()` 共 **22 处** 无连接池 |
| 8 | start.ps1 固定休眠偏长 | [start.ps1:128/145/155/223/254](file:///d:/Projects/Web/Mailigence/start.ps1#L253-L259)，223/254 在 `try` 之前无条件 sleep 750ms |

---

## 1. 验收基线（测量方法，先测后改）

冷启动用户可见总时长当前约 **15~20s**（记忆中的实测值）：
- 服务启动 ≈7s（其中 PostgreSQL/后端就绪）
- 页面 + React 加载 ≈1s
- **AI Advisor 的 LLM 调用 7~18s（占大头，且是纯等待）**
- 其余 dashboard 接口 16~67ms

**基线采集方式**（不改代码即可做）：
1. `boot.ts` 已有 `stamp()` 逐行打点（`[  0.42s]` 格式），冷启动后把终端日志截图留档。
2. 浏览器 Performance 面板记录 `navigationStart → 首次可交互`；或临时在 `bootReady()` 里 `console.log("boot_ready_ms", elapsed)`，取 3 次冷启动中位数。

**对比要求**（项目硬约束）：每阶段改完，用同一台机、同一本地 Qwen 3.5 模型，冷启动 3 次取中位数，与基线并列记录。达标后才提交，最优版本推 GitHub。

**核心验收指标**：
- `bootReady()` 触发时刻（= 用户看到仪表盘）从 7~18s 降到 **≤2s**（P0 完成后）
- `GET /api/dashboard/summary` p95 < 50ms
- 打开同一封邮件第二次 < 100ms（P3 完成后）

---

## 2. P0 前端：解除首屏阻塞（零风险、收益最大）

> 目标：不动后端、不动动画，仅让"数据就绪"信号不再被慢接口扣住。
> 预期：首屏可用时间 7~18s → ≤2s。

### P0-1 `DashboardView` 解耦 schedule（核心改动）

**现状**（[DashboardView.tsx:126-135](file:///d:/Projects/Web/Mailigence/frontend/src/components/DashboardView.tsx#L126-L135)）：`await fetchSchedule()` 在 `try` 内，`setLoading(false)` + `bootReady()` 在 `finally`。因为 [:235](file:///d:/Projects/Web/Mailigence/frontend/src/components/DashboardView.tsx#L235-L237) `if (loading) return <div className="loading">`，**统计卡和待办列表全被 schedule 接口扣住**。

**改法**：

```tsx
// DashboardView.tsx —— fullRefresh
try {
  const [s, p] = await Promise.all([
    api.getDashboardSummary(),
    api.getDashboardPending(),
  ]);
  setSummary(s);
  prevSummaryRef.current = s;
  setPending(p);
  setLastUpdated(new Date());
  bootStats({
    today: String(s.today_count),
    unread: String(s.unread_count),
    pending: String(s.pending_count),
    urgent: String(s.urgent_count),
  });
  okSummary("ok", `待处理 ${s.pending_count}`);
  okPending("ok", `${p.length} 封`);
} catch {
  okSummary("fail");
  okPending("fail");
} finally {
  // 核心数据到位即刻解锁首屏与开屏动画，不再等 AI 日程
  setRefreshing(false);
  setLoading(false);
  bootReady();
}

// AI 日程独立异步加载，不进入上面的阻塞链
fetchSchedule().catch(() => {});
```

**配套：给 AI 助理卡补骨架占位**。当前 [:285](file:///d:/Projects/Web/Mailigence/frontend/src/components/DashboardView.tsx#L284-L286) 是 `{schedule && (...)}`，schedule 为 null 时**整块空白**。需新增 `scheduleLoading` 状态：

```tsx
const [scheduleLoading, setScheduleLoading] = useState(true);
// fetchSchedule 内： try{...} finally { setScheduleLoading(false); }
// 渲染： schedule ? <卡片/> : scheduleLoading ? <div className="dash-advisor-skeleton">{t("misc.loading")}</div> : null
```

**风险**：低。`fetchSchedule` 已有自己的失败处理（`bootStep("schedule")` 记 fail），不会静默。
**验证**：冷启动后统计卡应立刻出现，AI 助理卡先骨架后填充；`bootReady` 日志时间应从 7~18s 降到 ≤2s。

### P0-2 `App.tsx` 解除组件挂载门禁

**现状**（[App.tsx:200-205](file:///d:/Projects/Web/Mailigence/frontend/src/App.tsx#L200-L205)）：`loadingAccounts ? <loading/> : <DashboardView />`，`DashboardView` 不依赖 accounts 却要等它。

**改法**：

```tsx
) : view === "dashboard" ? (
  <DashboardView />
) : view === "accounts" ? (
```

`accounts` 的加载仍由 `refreshAccounts()` 在 `useEffect` 里进行，侧栏 `nav-count` 会在稍后自己填上。
**风险**：低。需确认 `DashboardView` 确实不读 `accounts`（已核实：其 props 为空）。
**收益**：省掉 accounts 接口带来的挂载延迟（约 0.3~1s）。

### P0-3 下调 `MIN_MS`（保留动画）

**现状**：若数据在 4.15s（`DESKTOP_AT`）之后、7.6s（`MIN_MS`）之前就绪，`warmCut=false`，用户被强制等到 `7600+1150+550≈9.3s`。

**改法**：[boot.ts:31](file:///d:/Projects/Web/Mailigence/frontend/src/boot.ts#L30-L36) `MIN_MS = 7600` → `MIN_MS = 4500`。
**说明**：P0-1 之后 `bootReady()` 不再被 LLM 扣住，触发点回落到"后端就绪"时刻（实测约 7~8s，因为前端 health 检查在后端起监听前一直失败）；若数据早于 `DESKTOP_AT=4150ms` 到齐（如后端已在运行的热启动），则走 `warmCut` 分支立即接管。`MIN_MS` 下调只兜底 4150ms~7600ms 这个中间窗口，不改动正常路径的观感（动画仍完整播完 banner 再淡出）。
**风险**：低。只影响动画节奏，不影响功能。

### P0-4 Vite 代理改 `127.0.0.1`（仅代理目标，浏览器地址不动）

**已改**：[vite.config.ts:11](file:///d:/Projects/Web/Mailigence/frontend/vite.config.ts#L10-L14) `target: "http://localhost:8000"` → `"http://127.0.0.1:8000"`。这是 Vite 服务端行为，不改变浏览器侧来源。
**理由**：Windows + Node 17+ 下 `localhost` 优先解析 IPv6 `::1`，与只监听 IPv4 的后端不一致时触发回退，每个 `/api` 代理请求平白 +300ms~2s。前端全部请求都走该代理，因此是高频收益点。
**实测修正（与原方案不同）**：原方案建议把 `start.ps1` 的浏览器地址与前端就绪轮询一并改成 `127.0.0.1`，**已放弃**。证据：`backend/.env:6` `CORS_ORIGINS=["http://localhost:5173",…]` 与 `backend/.env:24` `PUBLIC_BASE_URL=http://localhost:8000`（OAuth 回调基址）都以 `localhost` 为准；若浏览器来源变为 `127.0.0.1:5173` 会被 CORS 拒绝并打断 OAuth 回调。另注意 `start.ps1:225` 的后端健康检查原本已是 `127.0.0.1:8000`。
**风险**：低（单处配置，可一行回滚）。

### P0-5 StrictMode 仅生产规避（可选，低优先）

**现状**：[main.tsx:12](file:///d:/Projects/Web/Mailigence/frontend/src/main.tsx#L11-L16) `<React.StrictMode>` 使开发模式下所有 `useEffect` 双跑，首屏请求翻倍。

**改法**：不删 StrictMode（它是有价值的开发约束），改为在 `useEffect` 里做请求去重（模块级 in-flight promise 缓存）；或按报告建议改用 `vite build + vite preview` 日常启动。
**注意**：改 `start.ps1` 为 build+preview 会牺牲前端热更新，**与日常开发习惯冲突**，建议做成开关（如 `start.ps1 -Prod`）而非直接替换默认行为。此项列为可选。

---

## 3. P1 后端低风险（配置与查询级，不动业务逻辑）

### P1-1 关闭开发环境 SQL 全量日志

**现状**（[database.py:23](file:///d:/Projects/Web/Mailigence/backend/app/database.py#L21-L30)）：`echo=settings.app_env == "development"`，而 [config.py:26](file:///d:/Projects/Web/Mailigence/backend/app/config.py#L24-L28) 默认 `app_env="development"`；1536 维向量字符串也会被格式化打印。

**改法**：

```python
echo=os.getenv("SQL_ECHO", "0") == "1",
```

**风险**：极低。**验证**：启动后 `backend.log` 不再有 SQL 语句，功能不变。
**回滚**：设 `SQL_ECHO=1` 即恢复。

### P1-2 `/summary` 5 次查询合并为 1 次

**现状**（[dashboard.py:149-197](file:///d:/Projects/Web/Mailigence/backend/app/api/dashboard.py#L149-L197)）：5 次独立 `await db.execute(select(func.count/max...))`，该接口被前端每 10s 轮询（[DashboardView.tsx:187](file:///d:/Projects/Web/Mailigence/frontend/src/components/DashboardView.tsx#L186-L189)）。

**改法**：

```python
today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
is_actionable = UnifiedEmail.suggested_action.in_(["reply", "review"])
row = (await db.execute(
    select(
        func.count(UnifiedEmail.id).filter(is_actionable).label("pending_count"),
        func.count(UnifiedEmail.id).filter(is_actionable, UnifiedEmail.priority_score >= 70).label("urgent_count"),
        func.count(UnifiedEmail.id).filter(UnifiedEmail.is_read.is_(False)).label("unread_count"),
        func.max(UnifiedEmail.received_at).label("last_mail_at"),
        func.count(UnifiedEmail.id).filter(UnifiedEmail.received_at >= today_start).label("today_count"),
    ).where(*base_cond)
)).one()
```

**风险**：低。返回字段与顺序必须与现有一致（前端读 5 个键）。
**验证**：改前后对同一时刻请求 `/api/dashboard/summary`，5 个数值必须逐项相等。

### P1-3 `auto_handle_emails` 空写时跳过 commit

**现状**（[schedule_analyzer.py:122-167](file:///d:/Projects/Web/Mailigence/backend/app/services/schedule_analyzer.py#L122-L167)）：每次 `pending`/`schedule` 请求都扫描 + `commit()`，`rowcount=0` 也提交。

**改法**：

```python
result = await db.execute(stmt)
count = result.rowcount or 0
if count > 0:
    await db.commit()
return count
```

**风险**：低。需确认调用方不依赖 commit 副作用（核实：无）。
**注意**：报告建议的"复合索引"可一并评估，但需先用 `EXPLAIN ANALYZE` 证明当前确实是热点，避免无谓加索引。

### P1-4 共享 `httpx.AsyncClient` 连接池

**现状**：全仓 **22 处** `async with httpx.AsyncClient(...)`（[ai_analyzer.py](file:///d:/Projects/Web/Mailigence/backend/app/services/ai_analyzer.py#L631) 6 处、[schedule_analyzer.py](file:///d:/Projects/Web/Mailigence/backend/app/services/schedule_analyzer.py#L370) 2 处、[embedding_service.py:91](file:///d:/Projects/Web/Mailigence/backend/app/services/embedding_service.py#L91)、memories、ms_graph、oauth、probes…），每次调用新建 TCP+TLS。

**改法**：新增 `backend/app/services/http_client.py`

```python
import httpx
_limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
client = httpx.AsyncClient(limits=_limits)  # 各调用点按需传 timeout
async def aclose() -> None:
    await client.aclose()
```

调用点改为 `async with` → 复用 `client`（timeout 通过 `client.get(..., timeout=...)` 逐次传入保持现状语义）；`main.py` lifespan 的 `yield` 之后调用 `await aclose()`。
**风险**：低-中。需确认无代码依赖"每次新 client 的干净状态"；OAuth 的 `follow_redirects` 等参数需逐点核对。
**建议**：先只改热路径（ai_analyzer / schedule_analyzer / embedding），验证后再铺开，控制爆炸半径。

### P1-5 IDLE 首次同步延后，把启动前 15s 让给首屏

**现状**（[main.py:174-176](file:///d:/Projects/Web/Mailigence/backend/app/main.py#L174-L176)）：lifespan 里 `await idle_manager.start(acct.id)`；每个任务启动即连 IMAP 并跑一次 `_sync_new_mail`（[idle_manager.py:169](file:///d:/Projects/Web/Mailigence/backend/app/services/idle_manager.py#L165-L169)），与首屏请求争抢连接与 CPU。

**改法**：只在**每账户任务的第一次**循环前等一拍——放在 `_run` 开头（[idle_manager.py:121-125](file:///d:/Projects/Web/Mailigence/backend/app/services/idle_manager.py#L121-L125)），**不要**放在 `_run_one_cycle` 里（那会让每次重连都白白延迟）：

```python
async def _run(self, account_id: int) -> None:
    if IDLE_START_DELAY > 0:
        await asyncio.sleep(IDLE_START_DELAY)   # 常量 15
    while account_id not in self._stop_flags:
        ...
```

**风险**：低。**注意**：项目既有约束是"扫描间隔 15s、启动延迟 5s"，此处延迟需与既有 `BACKGROUND_SYNC_SECONDS` 量级协调，避免新邮件出现被推迟过久。

---

## 4. P2 中风险（收益大，需回归测试）

### P2-1 `/schedule` 改 SWR：秒回 + 后台刷新 + 缓存持久化

**现状**：无缓存/指纹变化时在 HTTP 请求内 `await _analyze_with_llm`（7~45s）。

**改法**：
1. 无缓存或指纹变化时，**立即返回** `_analyze_with_rules(...)` 结果并附 `stale: true`（耗时 ms 级）。
2. `asyncio.create_task(_refresh_schedule_cache(...))` 后台调 LLM，写回缓存；前端下次 30s 轮询即拿到 `source: "ai"`。
3. 缓存持久化到 DB（新表 `schedule_cache`（fingerprint, payload JSONB, updated_at）或复用 `app_settings`），避免重启即冷。
**风险**：中。需保证并发刷新去重（同一 fingerprint 同时只跑一个 task），并保留现有 `unchanged=true` 语义不变。
**验证**：冷启动首屏 `schedule` 应在 <100ms 返回 `source=rules`；30s 后轮询变为 `source=ai`。需确认前端 `dash-source-badge`（[:290-292](file:///d:/Projects/Web/Mailigence/frontend/src/components/DashboardView.tsx#L290-L292)）正确显示 Rules→AI 切换，不闪回。

### P2-2 N+1 写入批量化

三处独立改动，建议逐个验证：

1. **分析结果持久化统一提交**（[analysis_service.py:158-191](file:///d:/Projects/Web/Mailigence/backend/app/services/analysis_service.py#L157-L191)）：把 `await db.commit()`（第 191 行）移出 `for email in emails:` 循环，循环后提交一次。200 封邮件 = 200 次 fsync → 1 次。
   - **风险**：中。需确认 `_register_categories`（循环内调用）与 `on_progress` 不依赖"逐封已落库"。`on_progress` 是 UI 进度回调，可改为批量提交后再回调总数。
2. **已读标记批量更新**（[mail_sync.py:133-148](file:///d:/Projects/Web/Mailigence/backend/app/services/mail_sync.py#L133-L148)）：按 `is_read` 分成两组 `message_id` 列表，用 2 条 `UPDATE ... WHERE account_id=? AND message_id IN (...) AND is_read != ?` 替代 N 次循环。
   - **风险**：中。`updated` 计数语义要等价（现在按 rowcount 累加）。
3. **收窄 message_id 查询**（[mail_sync.py:319-324](file:///d:/Projects/Web/Mailigence/backend/app/services/mail_sync.py#L319-L324)）：加 `.where(UnifiedEmail.message_id.in_(list(uid_to_mid.values())))`，避免每次同步把该账户全量历史 `message_id` 拉进内存。
   - **风险**：低-中。注意 `uid_to_mid` 可能返回 ng 条，`IN (...)` 参数过多时需分批（如每 500 个一批）。**这是最容易出正确性问题的点**（去重逻辑），必须验证同步后无重复插入、无漏插。

**验证**：改动后跑一次全账户同步，核对 `unified_emails` 行数增量与 `(account_id, message_id)` 唯一约束冲突数为 0。

---

## 5. P3 需改表结构（最后做）

### P3-1 邮件正文按需持久化

**现状**（[models/email.py:71-72](file:///d:/Projects/Web/Mailigence/backend/app/models/email.py#L71-L72)）：只存 `body_snippet`（≤500 字）；每次点开全文都重连 IMAP 拉取（[emails.py:143-190](file:///d:/Projects/Web/Mailigence/backend/app/api/emails.py#L143-L190)），2~8s。

**改法**：
1. `unified_emails` 增列 `full_body_html TEXT`、`full_body_text TEXT`、`full_body_fetched_at TIMESTAMPTZ`，按 `init_db()` 既有 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 模式加迁移。
2. `_fetch_full_body` 改为：先查列 → 命中直接返回（<5ms）；未命中再走 IMAP，成功后写回该行。
3. **必须保留现有降级路径**：服务器已删除邮件时回落 `body_snippet`（[:182-189](file:///d:/Projects/Web/Mailigence/backend/app/api/emails.py#L182-L189)），不要因为新增缓存破坏 QQ/netease 的多文件夹回退逻辑。

**风险**：中。DB 体积增长——建议只缓存"用户主动打开过"的邮件，并加单封大小上限（如 2MB，超限不缓存）。
**收益**：重复打开从 2~8s → <5ms。

---

## 6. 明确不做的事

| 报告建议 | 决策 | 理由 |
|---|---|---|
| 默认 `bootMode` 改为 `simple`/`off` | **不做** | CRT 多窗口开场是用户明确要的效果；问题不在动画，而在 `bootReady()` 被慢接口扣住。P0-1 修好后 `full` 模式走 `warmCut` 自动提前接管，动画完整保留 |
| `start.ps1` 默认改 `vite build + preview` | **不做（默认）** | 会牺牲前端热更新，与日常开发习惯冲突。可作为 `start.ps1 -Prod` 可选开关 |
| 给 `unified_emails` 加复合索引 | **待定** | 先用 `EXPLAIN ANALYZE` 证明是热点再决定，避免无谓索引拖慢写入 |

---

## 7. 实施顺序

```
P0-1 DashboardView 解耦  ─┐
P0-2 App 解除挂载门禁    ─┼─→ 一次冷启动实测（核心指标：bootReady ≤2s）
P0-3 MIN_MS 下调         ─┤
P0-4 代理/脚本改 127.0.0.1 ─┘
        ↓
P1 全部（5 项，互不依赖，可并行；每项独立验证）
        ↓
P2-1 SWR  ← 收益最大、风险最高，单独提交
P2-2 N+1 批量化 ← 拆 3 个独立提交，逐个验证同步正确性
        ↓
P3-1 正文缓存（改表结构，最后）
```

**每阶段收尾动作**：`npx tsc --noEmit` + `vite build` → 冷启动 3 次中位数对比 → 记录改动前后差异 → 通过后提交；全部完成推 GitHub。

---

## 8. 预期总收益

| 阶段 | 用户可感知变化 |
|---|---|
| P0 完成 | 冷启动 15~20s → **≤2s**（首屏不再等 LLM） |
| P1 完成 | dashboard 轮询、启动日志 I/O、IMAP 抢资源问题消除 |
| P2 完成 | 日程卡秒出、邮件批量落库不再卡顿 |
| P3 完成 | 重复点开邮件 2~8s → **<5ms** |
