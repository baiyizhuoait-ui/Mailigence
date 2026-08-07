import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
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

const GROUP_ORDER = ["today", "tomorrow", "this_week", "upcoming"];

export function DashboardView() {
  const { t, lang } = useI18n();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [schedule, setSchedule] = useState<ScheduleResult | null>(null);
  const [pending, setPending] = useState<UnifiedEmail[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [lastMailAt, setLastMailAt] = useState<string | null>(null);
  const [handlingIds, setHandlingIds] = useState<Set<number>>(new Set());
  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);
  const scheduleTimer = useRef<ReturnType<typeof setInterval> | undefined>();

  const fullRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [s, p, sc] = await Promise.all([
        api.getDashboardSummary(),
        api.getDashboardPending(),
        api.getDashboardSchedule(),
      ]);
      setSummary(s);
      setPending(p);
      setSchedule(sc);
      setLastUpdated(new Date());
      setLastMailAt(s.last_mail_at);
    } catch {
      /* ignore */
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, []);

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
      setSummary(s);
      if (lastMailAt && s.last_mail_at && s.last_mail_at !== lastMailAt) {
        await fullRefresh();
      } else {
        setLastMailAt(s.last_mail_at);
      }
    } catch {
      /* ignore */
    }
  }, [lastMailAt, fullRefresh]);

  useEffect(() => {
    fullRefresh();
  }, [fullRefresh]);

  useEffect(() => {
    const timer = setInterval(quickPoll, 10_000);
    return () => clearInterval(timer);
  }, [quickPoll]);

  useEffect(() => {
    scheduleTimer.current = setInterval(
      async () => {
        try {
          const sc = await api.getDashboardSchedule();
          setSchedule(sc);
        } catch {
          /* ignore */
        }
      },
      3 * 60 * 1000,
    );
    return () => clearInterval(scheduleTimer.current);
  }, []);

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

  // Group schedule items.
  const groupedSchedule = GROUP_ORDER.map((group) => ({
    group,
    items: (schedule?.schedule_items ?? []).filter(
      (item) => (item.group || "upcoming") === group,
    ),
  })).filter((g) => g.items.length > 0);

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
      {/* Two-column layout */}
      <div className="dash-grid">
        {/* Left column: Brief + Schedule */}
        <div className="dash-left">
          {/* AI Brief */}
          {schedule && (
            <div className="dash-brief-card">
              <div className="dash-brief-header">
                <span className="dash-brief-icon">✦</span>
                <span className="dash-brief-title">{t("dash.dailyBrief")}</span>
                <span className={`dash-source-badge ${schedule.source}`}>
                  {schedule.source === "ai" ? "AI" : "Rules"}
                </span>
              </div>
              <p className="dash-brief-text">{schedule.daily_brief}</p>
              {lastUpdated && (
                <div className="dash-last-updated">
                  {t("dash.lastUpdated")}: {lastUpdated.toLocaleTimeString(lang === "zh" ? "zh-CN" : "en-US")}
                </div>
              )}
            </div>
          )}

          {/* Schedule timeline */}
          <div className="dash-schedule-card">
            <h3 className="dash-section-title">{t("dash.schedule")}</h3>
            {groupedSchedule.length === 0 ? (
              <div className="dash-schedule-empty">{t("dash.noSchedule")}</div>
            ) : (
              <div className="dash-schedule-groups">
                {groupedSchedule.map(({ group, items }) => (
                  <div key={group} className="dash-schedule-group">
                    <div className="dash-schedule-group-label">
                      {t(`dash.group.${group}`)}
                    </div>
                    {items.map((item, i) => (
                      <div key={i} className="dash-schedule-item">
                        <span className="dash-schedule-icon">
                          {TYPE_ICONS[item.type] || "📋"}
                        </span>
                        <div className="dash-schedule-content">
                          <div className="dash-schedule-title">{item.title}</div>
                          {(item.date || item.time) && (
                            <div className="dash-schedule-time">
                              {item.date} {item.time}
                            </div>
                          )}
                        </div>
                        <span className={`dash-schedule-type ${item.type}`}>
                          {t(`dash.type.${item.type}`)}
                        </span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right column: Stats + Priority Queue */}
        <div className="dash-right">
          {/* Stats row */}
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

          {/* Priority queue */}
          <div className="dash-queue-card">
            <h3 className="dash-section-title">{t("dash.priorityQueue")}</h3>
            {queueEmails.length === 0 ? (
              <div className="dash-empty">
                <div className="dash-empty-icon">✓</div>
                <p>{t("dash.allDone")}</p>
              </div>
            ) : (
              <div className="dash-queue-list">
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
                          <span className="dash-reason-text">{item.reason}</span>
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
          </div>
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
