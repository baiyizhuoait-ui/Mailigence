/**
 * Boot splash controller (CRT intro).
 *
 * The overlay markup and its keyframes live in ``index.html`` so it paints on
 * the very first frame — before the module graph, React or any API call. This
 * module plays two phases, both ported from the 550C reference:
 *
 *   phase 1  ``playReveal`` wipes the vector mark in shape by shape, ordered by
 *            bounding-box x, from a dim 8% ghost, then lets the phosphor breathe;
 *   phase 2  ``revealDesktop`` hands over to the CRT desktop — top/bottom HUD
 *            bars, four bordered windows, a scanning mailbox matrix and a
 *            floating summary popup — and the REAL load steps start typing into
 *            its CORE TERMINAL.
 *
 * Nothing about the progress is faked or timed: a line flips to ``[ OK ]`` and
 * a telemetry row fills only when the corresponding request actually came back
 * (:func:`bootStep` / :func:`bootStats` from App and DashboardView).
 *
 * The hand-off is readiness-driven rather than fixed. Measured cold starts on
 * this machine: ~7 s until the backend/frontend answer, then a first dashboard
 * load whose AI schedule call alone takes 7–18 s. Hence ``MIN_MS`` (the mark
 * reveal + a few seconds of desktop), a ``MAX_MS`` watchdog, and click / Esc to
 * skip at any time. A warm reload needs none of that: the ready signal arrives
 * almost immediately, so ``bootReady`` cuts to the desktop and hands over at
 * ``WARM_MS`` instead of dragging the user through the cold-start floor.
 */

export type StepKey = "server" | "accounts" | "summary" | "pending" | "schedule";

/** Cold start: minimum time up — the mark reveal plus a few seconds of desktop. */
const MIN_MS = 7600;
/**
 * Warm start: the app reports ready before the mark reveal is even due, so the
 * intro cuts straight to the desktop and hands over here instead of at MIN_MS.
 */
const WARM_MS = 2000;
/** Beat the desktop gets before the "ready" banner, whenever it was cut in early. */
const WARM_DESK_MS = 1000;
/** Beat the minimal overlay holds "ready" before fading (it has no banner). */
const SIMPLE_DONE_MS = 420;
/** Hard cap: fade whether or not the app ever reports ready. */
const MAX_MS = 25000;
/** Backdrop fade duration (must match #boot's transition in index.html). */
const FADE_MS = 550;
/** How long the "ready" banner is held after its 480 ms clip-open. */
const BANNER_MS = 1150;

/** Reveal pacing, mirroring the reference's 250 + i*300 schedule. */
const REVEAL_START = 250;
const REVEAL_STEP = 230;
const REVEAL_DUR = 600;
/** Caption fades in while the mark is still being drawn. */
const CAPTION_AT = 2200;
/** The desktop takes over once the reveal has landed. */
const DESKTOP_AT = 4150;
/** Milliseconds per character when typing a step label. */
const TYPE_MS = 14;
/** Milliseconds per character in the pipeline code stream. */
const CODE_MS = 18;
/** Cells in the mailbox matrix. */
const NODE_COUNT = 48;
/** Milliseconds between matrix scan steps (48 × 260 ms ≈ 12.5 s). */
const NODE_SCAN_MS = 260;
/** Stagger between queued terminal lines when the desktop appears. */
const DRAIN_STEP_MS = 190;

/** Every step counts once towards the pipeline bar. */
const STEP_ORDER: StepKey[] = ["server", "accounts", "summary", "pending", "schedule"];

const LABELS: Record<StepKey, { zh: string; en: string }> = {
  server: { zh: "连接后端服务", en: "Connecting to backend" },
  accounts: { zh: "加载邮箱账户", en: "Loading mail accounts" },
  summary: { zh: "读取邮箱概况", en: "Reading mailbox overview" },
  pending: { zh: "整理待处理邮件", en: "Collecting pending mail" },
  schedule: { zh: "AI 日程分析", en: "AI schedule analysis" },
};

const CHROME = {
  zh: {
    start: "正在启动…",
    caption: "邮件聚合分析 · 系统引导中",
    hint: "按 Esc 或点击任意位置跳过",
    ready: "系统就绪",
    popup: "同步摘要 / SYNC SUMMARY",
    popupStatus: "已就绪 · 等待接管",
    unit: "封待处理",
    noteOk: "所有邮件已完成分析与归档，随时可以接管。",
    noteBad: "检测到 {n} 封紧急邮件，建议接管后优先处理。",
  },
  en: {
    start: "STARTING…",
    caption: "MAIL AGGREGATION · BOOTING",
    hint: "Press Esc or click to skip",
    ready: "SYSTEM READY",
    popup: "SYNC SUMMARY",
    popupStatus: "READY · AWAITING HANDOVER",
    unit: "PENDING",
    noteOk: "Every mail is analysed and filed — ready to hand over.",
    noteBad: "{n} urgent mails detected. Handle them first after handover.",
  },
};

/** Telemetry / HUD values, keyed by the ``bootStats`` patch key. */
const STAT_ID: Record<string, string> = {
  accounts: "bt-v-accounts",
  folders: "bt-v-folders",
  idle: "bt-v-idle",
  mode: "bt-v-mode",
  today: "bt-v-today",
  unread: "bt-v-unread",
  pending: "bt-v-pending",
  urgent: "bt-v-urgent",
  api: "bt-v-api",
  sched: "bt-v-sched",
  net: "bt-v-net",
  platform: "bt-h-platform",
};

/** Mail-domain pseudocode for the PIPELINE window (mirrors the real modules). */
const CODE = [
  "// ── imap_client.py ───────────────────",
  "def fetch_new(account, since):",
  "    conn = login(account)",
  "    for uid in search(conn, SINCE=since):",
  "        raw = fetch_body(conn, uid)",
  "        yield parse_mime(raw)",
  "",
  "// ── classifier.py ────────────────────",
  "def classify(mail):",
  "    rule = deterministic_guard(mail)",
  "    if rule: return rule",
  "    return llm(mail, memory=feedback(mail.sender))",
  "",
  "// ── schedule_analyzer.py ─────────────",
  "def advisor(window_days=7):",
  "    fp = window_fingerprint(inbox)",
  "    if fp == cache.fp: return UNCHANGED",
  "    return llm_advisor(inbox, fp)",
  "",
  "// ── mail_sync.py ─────────────────────",
  "FOLDERS = ['INBOX', *platform_extra(acct)]",
  "await gather(sync(a) for a in accounts)",
];

type Lang = "zh" | "en";
/** Which overlay to play. Persisted by the Settings page (src/i18n.tsx). */
type BootMode = "off" | "simple" | "full";

/** The persisted UI settings blob, shared with the theme / language store. */
function readStored(): Record<string, unknown> {
  try {
    return JSON.parse(localStorage.getItem("emailui-settings") || "{}") || {};
  } catch {
    return {};
  }
}

function readLang(): Lang {
  return readStored().lang === "en" ? "en" : "zh";
}

function readBootMode(): BootMode {
  const mode = readStored().bootMode;
  return mode === "off" || mode === "simple" ? mode : "full";
}

let root: HTMLElement | null = null;
let stage: HTMLElement | null = null;
let desktop: HTMLElement | null = null;
let term: HTMLElement | null = null;
let codeEl: HTMLElement | null = null;
let nodesEl: HTMLElement | null = null;
let fillEl: HTMLElement | null = null;
let pctEl: HTMLElement | null = null;
let nodeCountEl: HTMLElement | null = null;
let stageNameEl: HTMLElement | null = null;
let popupsEl: HTMLElement | null = null;
let bannerEl: HTMLElement | null = null;
let hintEl: HTMLElement | null = null;
// Minimal variant ("simple" mode): its own small set of nodes.
let simpleFillEl: HTMLElement | null = null;
let simplePctEl: HTMLElement | null = null;
let simpleStepEl: HTMLElement | null = null;

let lang: Lang = "zh";
let startedAt = 0;
let readyAt: number | null = null;
let finished = false;
let desktopUp = false;
/** Playing the minimal variant: no mark reveal, no CRT desktop, no popups. */
let simpleMode = false;
/** The ready signal beat the mark reveal, so the intro is on the shortened path. */
let warmCut = false;
/** The overview already landed; hold its popup until the desktop is on screen. */
let summaryReady = false;
let nodeCursor = 0;
let popEl: HTMLElement | null = null;
let dimEl: HTMLElement | null = null;

const stats: Record<string, string> = {};
const timers: number[] = [];
const intervals: number[] = [];

type Rec = {
  key: StepKey;
  settled: boolean;
  state?: "ok" | "fail";
  detail?: string;
  line?: HTMLElement;
};
const recs = new Map<StepKey, Rec>();
const queue: Rec[] = [];

type NodeCell = { el: HTMLElement; st: HTMLElement };
const nodeCells: NodeCell[] = [];

/** Elapsed seconds, padded so the columns line up: ``[  0.42s]``. */
function stamp(): string {
  return `[${((performance.now() - startedAt) / 1000).toFixed(2).padStart(6)}s]`;
}

function clockStr(): string {
  const d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function setProgress(pct: number): void {
  const p = Math.max(0, Math.min(100, pct));
  if (fillEl) fillEl.style.width = `${p}%`;
  if (pctEl) pctEl.textContent = `${Math.round(p)}%`;
  if (simpleFillEl) simpleFillEl.style.width = `${p}%`;
  if (simplePctEl) simplePctEl.textContent = `${Math.round(p)}%`;
}

/**
 * The minimal overlay's one-line status. It shows a failure if any step
 * failed, else the step that is still running, else "ready" — so the text is
 * always derived from what actually happened rather than a script.
 */
function simpleStatus(): void {
  if (!simpleStepEl) return;
  const failed = STEP_ORDER.find((k) => {
    const rec = recs.get(k);
    return rec?.settled && rec.state === "fail";
  });
  if (failed) {
    simpleStepEl.textContent = `${LABELS[failed][lang]} · 失败`;
    return;
  }
  let running: StepKey | null = null;
  STEP_ORDER.forEach((k) => {
    const rec = recs.get(k);
    if (rec && !rec.settled) running = k;
  });
  simpleStepEl.textContent = running
    ? LABELS[running][lang]
    : CHROME[lang].ready;
}

function setNodeCount(n: number): void {
  if (nodeCountEl) {
    nodeCountEl.textContent = `${String(n).padStart(2, "0")} / ${NODE_COUNT}`;
  }
}

/** clip-path needs the -webkit- alias on older WebKit (the reference sets both). */
function setClip(el: SVGGraphicsElement, value: string): void {
  el.style.clipPath = value;
  el.style.setProperty("-webkit-clip-path", value);
}

/* ===================== phase 1 — the mark ===================== */

/**
 * Wipe the mark in, shape by shape, exactly like the reference's playBoot():
 * shapes are ordered by their bounding-box x, each is clipped from the right
 * (``inset(0 100% 0 0)``) and then animated back to ``inset(0 0 0 0)``, which
 * sweeps the visible region rightwards. They sit at 8% opacity beforehand, so
 * the whole wordmark reads as a faint ghost that fills in.
 */
function playReveal(): void {
  const logo = document.getElementById("bt-logo");
  if (!logo) return;

  const shapes = Array.from(
    logo.querySelectorAll<SVGGraphicsElement>("path, text, circle, rect"),
  );
  shapes.forEach((el) => {
    el.style.opacity = "0.08";
    setClip(el, "inset(0 0 0 0)");
  });

  const sorted = shapes
    .map((el) => ({ el, x: el.getBBox().x }))
    .sort((a, b) => a.x - b.x);

  sorted.forEach((item, i) => {
    timers.push(
      window.setTimeout(() => {
        const el = item.el;
        setClip(el, "inset(0 100% 0 0)");
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            el.style.transition = `clip-path ${REVEAL_DUR}ms cubic-bezier(.4,0,.2,1), opacity 350ms ease`;
            el.style.opacity = "1";
            setClip(el, "inset(0 0 0 0)");
          }),
        );
      }, REVEAL_START + i * REVEAL_STEP),
    );
  });

  timers.push(
    window.setTimeout(
      () => logo.classList.add("finished"),
      REVEAL_START + sorted.length * REVEAL_STEP + REVEAL_DUR,
    ),
  );
}

/* ===================== phase 2 — the desktop ===================== */

function buildNodes(): void {
  if (!nodesEl) return;
  for (let i = 0; i < NODE_COUNT; i++) {
    const cell = document.createElement("div");
    cell.className = "bt-node-cell";
    cell.innerHTML =
      `<svg viewBox="0 0 64 64" fill="none"><rect x="8" y="16" width="48" height="34" rx="3" stroke="currentColor" stroke-width="2.6"/><path d="M9 18 L32 36 L55 18" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg>`;
    const id = document.createElement("div");
    id.className = "id";
    id.textContent = `M${String(i + 1).padStart(2, "0")}`;
    const st = document.createElement("div");
    st.className = "st";
    st.textContent = "IDLE";
    cell.append(id, st);
    nodesEl.appendChild(cell);
    nodeCells.push({ el: cell, st });
  }
}

/** Advance the scan front one cell: the previous finishes, the next lights up. */
function scanTick(): void {
  if (finished || nodeCursor >= NODE_COUNT) return;
  const prev = nodeCells[nodeCursor - 1];
  if (prev) {
    prev.el.classList.remove("busy");
    prev.el.classList.add("done");
    prev.st.textContent = "OK";
  }
  const cur = nodeCells[nodeCursor];
  if (cur) {
    cur.el.classList.add("busy");
    cur.st.textContent = "SYNC";
  }
  nodeCursor += 1;
  setNodeCount(Math.min(nodeCursor, NODE_COUNT));
  if (nodeCursor > 0 && nodeCursor % 6 === 0) bumpStage();
}

/** Flip the stage label along the scan so the bottom HUD reads as live. */
function bumpStage(): void {
  if (!stageNameEl) return;
  const pct = Math.round((nodeCursor / NODE_COUNT) * 100);
  stageNameEl.textContent = pct >= 100 ? "COMPLETE" : pct >= 66 ? "ANALYZE" : "SYNC";
}

/** On hand-over, finish the matrix instantly. */
function completeNodes(): void {
  nodeCells.forEach((c) => {
    c.el.classList.remove("busy");
    c.el.classList.add("done");
    c.st.textContent = "OK";
  });
  nodeCursor = NODE_COUNT;
  setNodeCount(NODE_COUNT);
}

/** Stream the pipeline pseudocode, three characters at a time. */
function startCode(): void {
  if (!codeEl) return;
  let lineIdx = 0;
  let charIdx = 0;
  let line: HTMLElement | null = null;

  const tick = (): void => {
    if (finished || !codeEl) return;
    if (!line) {
      const text = CODE[lineIdx % CODE.length];
      line = document.createElement("div");
      line.className = text.startsWith("//") ? "bt-cln cm" : "bt-cln";
      codeEl.appendChild(line);
      charIdx = 0;
      // Keep the window short: drop the oldest lines as new ones arrive.
      while (codeEl.childElementCount > 20) codeEl.removeChild(codeEl.firstChild!);
    }
    charIdx += 3;
    line.textContent = CODE[lineIdx % CODE.length].slice(0, charIdx);
    if (charIdx >= CODE[lineIdx % CODE.length].length) {
      lineIdx += 1;
      line = null;
    }
    timers.push(window.setTimeout(tick, CODE_MS));
  };
  tick();
}

function typeInto(el: HTMLElement, text: string): void {
  let i = 0;
  const step = (): void => {
    if (finished || !el.isConnected) return;
    i += 1;
    el.textContent = text.slice(0, i);
    if (i < text.length) timers.push(window.setTimeout(step, TYPE_MS));
  };
  step();
}

/**
 * Create the terminal line for a step record and type its label. Called either
 * when the desktop appears (draining the queue) or immediately if a step is
 * first reported after that.
 */
function createLine(rec: Rec): void {
  if (!term || rec.line) return;
  const line = document.createElement("div");
  line.className = "bt-ln inf";

  const ts = document.createElement("span");
  ts.className = "ts";
  ts.textContent = stamp();

  const label = document.createElement("span");
  const res = document.createElement("span");
  res.className = "res";
  res.style.marginLeft = "6px";
  res.textContent = "…";

  line.append(ts, label, res);
  term.appendChild(line);
  rec.line = line;
  typeInto(label, `${LABELS[rec.key][lang]}`);
  paint(rec);
}

/** Write a record's settled state into its line, if it has one yet. */
function paint(rec: Rec): void {
  if (!rec.line || !rec.settled) return;
  const res = rec.line.querySelector(".res") as HTMLElement | null;
  if (res) {
    res.textContent = `[ ${rec.state === "ok" ? "OK" : "FAIL"} ]${
      rec.detail ? ` ${rec.detail}` : ""
    }`;
  }
  rec.line.classList.remove("inf");
  rec.line.classList.add(rec.state === "ok" ? "ok" : "bad");
}

/**
 * Show the desktop and replay any steps that landed while it was hidden.
 * ``withPopup`` is false when the desktop is cut in as part of the hand-over —
 * there is no point raising the summary popup only to fade it straight out.
 */
function revealDesktop(withPopup = true): void {
  if (desktopUp || finished) return;
  desktopUp = true;
  stage?.classList.add("bt-gone");
  desktop?.classList.add("bt-in");
  hintEl?.classList.add("show");

  queue.forEach((rec, i) => {
    timers.push(window.setTimeout(() => createLine(rec), i * DRAIN_STEP_MS));
  });
  // The overview usually lands while the mark is still being drawn; its popup
  // belongs to the desktop, so hold it until a beat after the hand-over.
  if (withPopup && summaryReady) {
    timers.push(window.setTimeout(openSummaryPopup, 900));
  }
  queue.length = 0;

  intervals.push(window.setInterval(scanTick, NODE_SCAN_MS));
  startCode();
}

/* ===================== telemetry ===================== */

/**
 * Fill the telemetry / HUD rows with real values. Keys are listed in STAT_ID;
 * unknown keys are ignored. Safe to call at any time — the desktop markup is
 * in the DOM from the first frame, so early values are simply waiting there
 * when the desktop fades in.
 */
export function bootStats(patch: Record<string, string>): void {
  Object.entries(patch).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    stats[k] = v;
    const el = document.getElementById(STAT_ID[k] ?? "");
    if (el) el.textContent = v;
    // An IDLE-capable account is the interesting case; flag it green.
    if (k === "idle" && el) el.className = v === "IDLE" ? "v ok" : "v warn";
  });
}

/**
 * The floating summary popup — the centrepiece of the reference screenshots.
 * Opened once the mailbox overview lands, closed on hand-over.
 */
function openSummaryPopup(): void {
  if (!popupsEl || popEl) return;
  const t = CHROME[lang];
  const urgent = Number(stats.urgent || "0");
  const danger = urgent > 0;

  dimEl = document.createElement("div");
  dimEl.className = "bt-dim";
  popupsEl.appendChild(dimEl);

  popEl = document.createElement("div");
  popEl.className = `bt-pop${danger ? " bt-danger" : ""}`;
  const rows: Array<[string, string, string]> = [
    ["账户", stats.accounts ?? "—", ""],
    ["今日邮件", stats.today ?? "—", ""],
    ["未读", stats.unread ?? "—", ""],
    ["紧急", stats.urgent ?? "—", danger ? "bad" : ""],
  ];
  popEl.innerHTML =
    `<div class="bt-wp-title"><span>▣</span>` +
    `<span class="bt-wp-name">${t.popup}</span>` +
    `<span class="bt-wp-btn">─</span><span class="bt-wp-btn">□</span><span class="bt-wp-btn">✕</span></div>` +
    (danger ? `<div class="bt-alert-strip"></div>` : "") +
    `<div class="bt-wp-body">` +
    `<div class="bt-meter"><span class="big">${stats.pending ?? "0"}</span>` +
    `<span class="unit">${t.unit}</span></div>` +
    rows
      .map(
        ([k, v, cls]) =>
          `<div class="bt-wp-row"><span class="k">${k}</span>` +
          `<span class="v ${cls}">${v}</span></div>`,
      )
      .join("") +
    `<div class="bt-wp-note">${
      danger ? t.noteBad.replace("{n}", String(urgent)) : t.noteOk
    }</div></div>` +
    `<div class="bt-wp-status"><span>${t.popupStatus}</span>` +
    `<span class="bt-spacer"></span><span>${clockStr()}</span></div>`;

  popupsEl.appendChild(popEl);
  requestAnimationFrame(() => {
    dimEl?.classList.add("show");
    popEl?.classList.add("show");
  });
}

function closePopups(): void {
  if (!popEl) return;
  dimEl?.classList.remove("show");
  popEl.classList.remove("show");
  const el = popEl;
  const dim = dimEl;
  popEl = null;
  dimEl = null;
  timers.push(
    window.setTimeout(() => {
      el.remove();
      dim?.remove();
    }, 260),
  );
}

/* ===================== load-step API ===================== */

export type StepDone = (state: "ok" | "fail", detail?: string) => void;

/**
 * Report the start of a load step; call the returned function when it settles.
 *
 * Returns a no-op once the step already settled or the overlay is gone, so
 * background refreshes can't make finished lines flicker back to "…".
 */
export function bootStep(key: StepKey): StepDone {
  if (!root || finished || recs.has(key)) return () => {};

  const rec: Rec = { key, settled: false };
  recs.set(key, rec);
  if (simpleMode) simpleStatus();
  else if (desktopUp) createLine(rec);
  else queue.push(rec);

  return (state, detail) => {
    if (rec.settled) return;
    rec.settled = true;
    rec.state = state;
    rec.detail = detail;

    const settled = Array.from(recs.values()).filter((r) => r.settled).length;
    setProgress((settled / STEP_ORDER.length) * 100);

    // The minimal overlay has no terminal, telemetry rows or popups to drive.
    if (simpleMode) {
      simpleStatus();
      return;
    }

    paint(rec);
    if (key === "server") {
      bootStats({
        api: state === "ok" ? "ONLINE" : "DOWN",
        net: state === "ok" ? "ONLINE" : "OFFLINE",
      });
    }
    if (key === "summary" && state === "ok") {
      summaryReady = true;
      if (desktopUp) openSummaryPopup();
    }
    if (key === "schedule" && state === "ok") bootStats({ sched: "READY" });
  };
}

/** The dashboard has its data: finish the desktop, open the banner, fade out. */
export function bootReady(): void {
  if (!root || readyAt !== null) return;
  readyAt = performance.now();
  const elapsed = readyAt - startedAt;

  // Minimal variant: no desktop to cut in and no banner to clip open — just
  // hold "ready" for a beat so the progress bar is seen landing at 100%.
  if (simpleMode) {
    setProgress(100);
    simpleStatus();
    timers.push(
      window.setTimeout(finish, Math.max(0, WARM_MS - elapsed) + SIMPLE_DONE_MS),
    );
    return;
  }

  // Warm path: the data was already in before the mark reveal was due. Cut to
  // the desktop now — otherwise the intro would fade off the logo and the user
  // would never see the multi-window hand-over at all.
  warmCut = !desktopUp;
  if (warmCut) revealDesktop(false);

  setProgress(100);
  completeNodes();
  if (stageNameEl) stageNameEl.textContent = "COMPLETE";
  pushedFlavor();
  closePopups();

  // Absolute time (from start) the banner should open at. The warm path still
  // owes the desktop a beat, in case it was cut in late.
  const target = warmCut
    ? Math.max(elapsed + WARM_DESK_MS, WARM_MS)
    : Math.max(elapsed, MIN_MS);
  const hold = target - elapsed;
  timers.push(
    window.setTimeout(() => bannerEl?.classList.add("show"), hold),
  );
  timers.push(window.setTimeout(finish, hold + BANNER_MS));
}

/** A closing "handover" line in the terminal, so the log ends on a beat. */
let flavorPushed = false;
function pushedFlavor(): void {
  if (flavorPushed || !term || finished) return;
  flavorPushed = true;
  const line = document.createElement("div");
  line.className = "bt-ln sys";
  line.textContent = `[${(
    (performance.now() - startedAt) /
    1000
  ).toFixed(2)}s] 前端界面已就绪，准备接管。`;
  term.appendChild(line);
}

function finish(): void {
  if (finished || !root) return;
  finished = true;
  timers.forEach((id) => window.clearTimeout(id));
  intervals.forEach((id) => window.clearInterval(id));
  timers.length = 0;
  intervals.length = 0;
  window.removeEventListener("keydown", onKey, true);
  const el = root;
  el.classList.add("boot-out");
  window.setTimeout(() => {
    if (el.parentNode) el.parentNode.removeChild(el);
  }, FADE_MS);
  root = null;
}

function onKey(event: KeyboardEvent): void {
  if (event.key === "Escape") finish();
}

/**
 * The minimal variant: the app's own theme, a brand lockup, a progress bar and
 * one line of real status. No reveal, no CRT desktop, no popups — but it still
 * waits for the same readiness signal, so the user never sees the UI rearrange
 * itself underneath.
 */
function initSimple(): void {
  simpleMode = true;
  simpleFillEl = document.getElementById("bt-sim-fill");
  simplePctEl = document.getElementById("bt-sim-pct");
  simpleStepEl = document.getElementById("bt-sim-step");

  const sub = document.getElementById("bt-sim-sub");
  if (sub) sub.textContent = CHROME[lang].caption;
  if (simpleStepEl) simpleStepEl.textContent = CHROME[lang].start;

  hintEl = document.getElementById("bt-hint");
  if (hintEl) {
    hintEl.textContent = CHROME[lang].hint;
    hintEl.classList.add("show");
  }

  startedAt = performance.now();
  setProgress(0);

  root?.addEventListener("click", finish);
  window.addEventListener("keydown", onKey, true);
  timers.push(window.setTimeout(finish, MAX_MS));
}

function init(): void {
  root = document.getElementById("boot");
  // "off" drops the overlay in index.html before the first paint, so there is
  // usually nothing here — every exported call then no-ops on its own.
  if (!root) return;

  lang = readLang();
  const mode = readBootMode();
  if (mode === "off") {
    root.remove();
    root = null;
    return;
  }
  root.setAttribute("data-mode", mode);
  if (mode === "simple") {
    initSimple();
    return;
  }

  stage = document.getElementById("bt-stage");
  desktop = document.getElementById("bt-desktop");
  term = document.getElementById("bt-term");
  codeEl = document.getElementById("bt-code");
  nodesEl = document.getElementById("bt-nodes");
  fillEl = document.getElementById("bt-fill");
  pctEl = document.getElementById("bt-pct");
  nodeCountEl = document.getElementById("bt-node-count");
  stageNameEl = document.getElementById("bt-stage-name");
  popupsEl = document.getElementById("bt-popups");
  bannerEl = document.getElementById("boot-final");
  hintEl = document.getElementById("bt-hint");

  const caption = document.getElementById("bt-caption");
  const hint = document.getElementById("bt-hint");
  if (caption) {
    caption.innerHTML = "";
    caption.append(
      document.createTextNode(CHROME[lang].caption),
      Object.assign(document.createElement("span"), { className: "bt-cursor" }),
    );
  }
  if (hint) hint.textContent = CHROME[lang].hint;
  if (bannerEl) bannerEl.textContent = CHROME[lang].ready;

  const session = document.getElementById("bt-h-session");
  if (session) {
    session.textContent = `0x${Math.random().toString(16).slice(2, 10).toUpperCase()}`;
  }
  const clock = document.getElementById("bt-h-time");
  if (clock) {
    clock.textContent = clockStr();
    intervals.push(window.setInterval(() => (clock.textContent = clockStr()), 1000));
  }
  const fps = document.getElementById("bt-fps");
  if (fps) {
    intervals.push(
      window.setInterval(() => (fps.textContent = String(58 + Math.round(Math.random() * 2))), 700),
    );
  }

  startedAt = performance.now();
  setProgress(0);
  setNodeCount(0);
  buildNodes();
  playReveal();
  timers.push(window.setTimeout(() => caption?.classList.add("show"), CAPTION_AT));
  timers.push(window.setTimeout(revealDesktop, DESKTOP_AT));

  root.addEventListener("click", finish);
  window.addEventListener("keydown", onKey, true);
  timers.push(window.setTimeout(finish, MAX_MS));
}

init();