import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ChatPanel } from "./ChatPanel";
import { EmailDetailPanel } from "./EmailDetailPanel";
import { useI18n } from "../i18n";
import {
  PLATFORM_LABEL,
  type DashboardSummary,
  type ScheduleResult,
  type UnifiedEmail,
} from "../types";

const URGENCY_COLORS: Record<string, string> = {
  high: "var(--error)",
  medium: "var(--accent)",
  low: "var(--text-faint)",
};

const TYPE_ICONS: Record<string, string> = {
  meeting: "📅",
  deadline: "⏰",
  appointment: "📌",
  reminder: "🔔",
};

// Column splitter: left/right width ratio (30–75%) persisted per browser.
const SPLIT_KEY = "mailigence.dash.split";
const clampSplit = (pct: number) => Math.min(75, Math.max(30, pct));

export function DashboardView() {
  const { t, lang } = useI18n();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [schedule, setSchedule] = useState<ScheduleResult | null>(null);
  const [pending, setPending] = useState<UnifiedEmail[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [handlingIds, setHandlingIds] = useState<Set<number>>(new Set());
  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);
  const [aiAvailable, setAiAvailable] = useState<boolean | null>(null);
  const scheduleTimer = useRef<ReturnType<typeof setInterval> | undefined>();
  const prevSummaryRef = useRef<DashboardSummary | null>(null);
  // Fingerprint of the advisor data we last rendered. Sent back on every
  // fetch so the server can skip the LLM entirely when nothing changed.
  const scheduleFp = useRef<string | null>(null);

  // Draggable divider state between the advisor and chat columns.
  const [splitPct, setSplitPct] = useState<number>(() => {
    const stored = Number(localStorage.getItem(SPLIT_KEY));
    return Number.isFinite(stored) && stored > 0 ? clampSplit(stored) : 62;
  });
  const [dragging, setDragging] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);

  const onSplitPointerDown = useCallback((e: React.PointerEvent) => {
    const grid = gridRef.current;
    if (!grid) return;
    e.preventDefault();
    setDragging(true);
    const onMove = (ev: PointerEvent) => {
      const rect = grid.getBoundingClientRect();
      if (rect.width < 50) return;
      setSplitPct(clampSplit(((ev.clientX - rect.left) / rect.width) * 100));
    };
    const onUp = () => {
      setDragging(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setSplitPct((p) => {
        localStorage.setItem(SPLIT_KEY, p.toFixed(1));
        return p;
      });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []);

  const onSplitKeyDown = useCallback((e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 5 : 2;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      setSplitPct((p) => clampSplit(p - step));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      setSplitPct((p) => clampSplit(p + step));
    }
  }, []);

  // Fetch the AI advisor board (brief + schedule merged).
  const fetchSchedule = useCallback(async () => {
    const sc = await api.getDashboardSchedule(scheduleFp.current ?? undefined);
    scheduleFp.current = sc.fingerprint ?? scheduleFp.current;
    if (!sc.unchanged) setSchedule(sc);
  }, []);

  const fullRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [s, p] = await Promise.all([
        api.getDashboardSummary(),
        api.getDashboardPending(),
      ]);
      setSummary(s);
      prevSummaryRef.current = s;
      setPending(p);
      setLastUpdated(new Date());
      await fetchSchedule();
    } catch {
      /* ignore */
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, [fetchSchedule]);

  // Force sync: pull new mail from all accounts, then refresh dashboard.
  const forceSyncAndRefresh = useCallback(async () => {
    setSyncing(true);
    try {
      await api.syncAllAccounts();
    } catch {
      /* sync errors are non-fatal, still refresh */
    }
    await fullRefresh();
    setSyncing(false);
  }, [fullRefresh]);

  const quickPoll = useCallback(async () => {
    try {
      const s = await api.getDashboardSummary();
      const prev = prevSummaryRef.current;
      prevSummaryRef.current = s;
      setSummary(s);
      // Refresh the full view when anything the dashboard shows changed —
      // not only when new mail arrived. AI analysis finishing also changes
      // categories/counts without touching last_mail_at, so comparing only
      // last_mail_at left the list stale until the next new mail.
      if (
        prev &&
        (s.pending_count !== prev.pending_count ||
          s.urgent_count !== prev.urgent_count ||
          s.unread_count !== prev.unread_count ||
          s.today_count !== prev.today_count ||
          s.last_mail_at !== prev.last_mail_at)
      ) {
        await fullRefresh();
      }
    } catch {
      /* ignore */
    }
  }, [fullRefresh]);

  useEffect(() => {
    fullRefresh();
    api
      .getAiSettings()
      .then((s) =>
        setAiAvailable(s.api_key_configured && s.analysis_mode !== "rules_only"),
      )
      .catch(() => setAiAvailable(false));
  }, [fullRefresh]);

  useEffect(() => {
    const timer = setInterval(quickPoll, 10_000);
    return () => clearInterval(timer);
  }, [quickPoll]);

  useEffect(() => {
    // Fingerprint-gated: when the mail window is unchanged this costs the
    // server one cheap DB query and zero LLM calls, so a short interval
    // keeps the advisor board fresh for free.
    scheduleTimer.current = setInterval(() => {
      fetchSchedule().catch(() => {
        /* ignore */
      });
    }, 30_000);
    return () => clearInterval(scheduleTimer.current);
  }, [fetchSchedule]);

  const handleEmail = useCallback(async (emailId: number) => {
    setHandlingIds((prev) => new Set(prev).add(emailId));
    try {
      await api.handleEmail(emailId);
      // Remove from pending list immediately.
      setPending((prev) => prev.filter((e) => e.id !== emailId));
      setSchedule((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          priority_queue: prev.priority_queue.filter(
            (q) => q.email_id !== emailId,
          ),
        };
      });
    } catch {
      /* ignore */
    } finally {
      setHandlingIds((prev) => {
        const next = new Set(prev);
        next.delete(emailId);
        return next;
      });
    }
  }, []);

  // Build email lookup map.
  const emailMap = new Map(pending.map((e) => [e.id, e]));
  const queueEmails = schedule?.priority_queue
    ?.map((q) => ({ ...q, email: emailMap.get(q.email_id) }))
    .filter((q) => q.email) ?? [];

  if (loading) {
    return <div className="loading">{t("misc.loading")}</div>;
  }

  const timeFmt = (d: string) =>
    new Date(d).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });

  return (
    <div className="dashboard-view">
      {/* Stats row — full width on top */}
      <div className="dash-stats-row">
        <div className={`dash-stat-card ${summary?.urgent_count ? "urgent" : ""}`}>
          <div className="dash-stat-num">{summary?.urgent_count ?? 0}</div>
          <div className="dash-stat-label">{t("dash.urgent")}</div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-num">{summary?.pending_count ?? 0}</div>
          <div className="dash-stat-label">{t("dash.pending")}</div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-num">{summary?.unread_count ?? 0}</div>
          <div className="dash-stat-label">{t("dash.unread")}</div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-num">{summary?.today_count ?? 0}</div>
          <div className="dash-stat-label">{t("dash.today")}</div>
        </div>
        <button
          className="dash-refresh-btn"
          onClick={forceSyncAndRefresh}
          disabled={syncing || refreshing}
          title={syncing ? t("dash.syncing") : t("dash.forceSync")}
        >
          {syncing ? "⟳" : "↻"}
        </button>
      </div>

      {/* Two-column layout: AI advisor (left) | splitter | AI chat (right) */}
      <div
        className={`dash-grid ${dragging ? "dragging" : ""}`}
        ref={gridRef}
        style={{ "--dash-left": `${splitPct}%` } as React.CSSProperties}
      >
        {/* Left column: merged AI advisor (brief + actions + schedule) */}
        <div className="dash-left">
          {schedule && (
            <div className="dash-brief-card dash-advisor-card">
              <div className="dash-brief-header">
                <span className="dash-brief-icon">✦</span>
                <span className="dash-brief-title">{t("dash.aiAdvisor")}</span>
                <span className={`dash-source-badge ${schedule.source}`}>
                  {schedule.source === "ai" ? "AI" : "Rules"}
                </span>
              </div>
              <p className="dash-brief-text">{schedule.daily_brief}</p>

              {/* Suggested actions — what to do, in order */}
              {queueEmails.length === 0 ? (
                <div className="dash-advisor-done">
                  <span className="dash-advisor-done-icon">✓</span>
                  {t("dash.allDone")}
                </div>
              ) : (
                <div className="dash-advisor-actions">
                  {queueEmails.map((item, idx) => {
                    const email = item.email!;
                    const isHandling = handlingIds.has(item.email_id);
                    return (
                      <div
                        key={item.email_id}
                        className="dash-queue-item"
                        onClick={() => setSelectedEmailId(item.email_id)}
                      >
                        <div className="dash-queue-rank">{idx + 1}</div>
                        <div
                          className="dash-queue-bar"
                          style={{ background: URGENCY_COLORS[item.urgency] }}
                        />
                        <div className="dash-queue-body">
                          <div className="dash-queue-subject">
                            {email.subject || t("misc.noSubject")}
                          </div>
                          <div className="dash-queue-meta">
                            <span className="dash-queue-sender">
                              {email.sender || t("misc.unknownSender")}
                            </span>
                            {email.received_at && (
                              <span className="dash-queue-time">
                                {timeFmt(email.received_at)}
                              </span>
                            )}
                          </div>
                          <div className="dash-queue-reason">
                            <span
                              className="dash-urgency-tag"
                              style={{ color: URGENCY_COLORS[item.urgency] }}
                            >
                              {t(`dash.urgency.${item.urgency}`)}
                            </span>
                            <span
                              className="dash-reason-text"
                              title={item.reason}
                            >
                              {item.action || item.reason}
                            </span>
                            <span className="dash-est-time">
                              ~{item.estimated_minutes}{t("dash.min")}
                            </span>
                          </div>
                        </div>
                        <div className="dash-queue-actions">
                          <span className={`platform-badge sm ${email.platform}`}>
                            {PLATFORM_LABEL[email.platform] ?? email.platform}
                          </span>
                          <button
                            className="dash-handle-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleEmail(item.email_id);
                            }}
                            disabled={isHandling}
                            title={t("dash.handle")}
                          >
                            {isHandling ? "…" : "✓"}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Upcoming time-sensitive items as compact chips */}
              {(schedule.schedule_items?.length ?? 0) > 0 && (
                <div className="dash-advisor-schedule">
                  {schedule.schedule_items.map((item, i) => (
                    <button
                      key={i}
                      className="dash-advisor-chip"
                      onClick={() => setSelectedEmailId(item.email_id)}
                      title={item.title}
                    >
                      <span className="dash-advisor-chip-icon">
                        {TYPE_ICONS[item.type] || "📋"}
                      </span>
                      <span className="dash-advisor-chip-title">
                        {item.title}
                      </span>
                      {(item.date || item.time) && (
                        <span className="dash-advisor-chip-time mono">
                          {[item.date, item.time].filter(Boolean).join(" ")}
                        </span>
                      )}
                      <span className={`dash-schedule-type ${item.type}`}>
                        {t(`dash.type.${item.type}`)}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              {lastUpdated && (
                <div className="dash-last-updated">
                  {t("dash.lastUpdated")}: {lastUpdated.toLocaleTimeString(lang === "zh" ? "zh-CN" : "en-US")}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Draggable divider between the two columns */}
        <button
          type="button"
          className="dash-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label={t("dash.resize")}
          title={t("dash.resize")}
          onPointerDown={onSplitPointerDown}
          onKeyDown={onSplitKeyDown}
        />

        {/* Right column: AI cross-mailbox chat (when AI is configured) */}
        <div className="dash-right">
          {aiAvailable && <ChatPanel onOpenEmail={setSelectedEmailId} />}
        </div>
      </div>

      {/* Email reader — opens when a priority-queue item is clicked */}
      {selectedEmailId !== null && (
        <EmailDetailPanel
          emailId={selectedEmailId}
          onClose={() => setSelectedEmailId(null)}
          onReadChange={(id) =>
            setPending((prev) =>
              prev.map((e) => (e.id === id ? { ...e, is_read: true } : e)),
            )
          }
        />
      )}
    </div>
  );
}
