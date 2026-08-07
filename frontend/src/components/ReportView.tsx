import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  PLATFORM_LABEL,
  type EmailAccount,
  type EmailCategory,
  type ReportRange,
  type ReportSummary,
} from "../types";
import { useI18n } from "../i18n";

// Report category_dist keys are *display labels* (mapped server-side via the
// dynamic email_categories table); colors come from the same table.
const FALLBACK_CATEGORY_COLOR = "#9ca3af";

const ACTION_LABELS: Record<string, string> = {
  reply: "priority.reply",
  review: "priority.review",
  note: "priority.notice",
  ignore: "priority.none",
};

const RANGE_OPTIONS: { key: ReportRange; label: string }[] = [
  { key: "day", label: "report.range.day" },
  { key: "week", label: "report.range.week" },
  { key: "month", label: "report.range.month" },
];

const PRIORITY_ROWS = [
  { key: "high", label: "mailbox.priorityHigh", color: "#ef4444" },
  { key: "medium", label: "mailbox.priorityMedium", color: "#f59e0b" },
  { key: "low", label: "mailbox.priorityLow", color: "#6b7280" },
] as const;

const ACTION_KEYS = ["reply", "review", "note", "ignore"] as const;

function fmtDate(iso: string): string {
  // YYYY-MM-DD -> MM-DD
  return iso.length >= 10 ? iso.slice(5) : iso;
}

interface ReportViewProps {
  accounts: EmailAccount[];
}

export function ReportView({ accounts }: ReportViewProps) {
  const { t } = useI18n();
  const [range, setRange] = useState<ReportRange>("week");
  const [accountId, setAccountId] = useState<number | "all">("all");
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [categories, setCategories] = useState<EmailCategory[]>([]);

  useEffect(() => {
    api.listCategories().then(setCategories).catch(() => {});
  }, []);

  // label -> color lookup from the dynamic category table.
  const colorByLabel = useCallback(
    (label: string) => {
      const hit = categories.find(
        (c) => c.label === label || c.name === label,
      );
      return hit?.color ?? FALLBACK_CATEGORY_COLOR;
    },
    [categories],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.getReportSummary(
        range,
        accountId === "all" ? undefined : accountId,
      );
      setSummary(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [range, accountId]);

  useEffect(() => {
    load();
  }, [load]);

  // ---- derived data ----
  const total = summary?.total ?? 0;
  const catEntries = summary
    ? Object.entries(summary.category_dist).sort((a, b) => b[1] - a[1])
    : [];
  const catTotal = catEntries.reduce((s, [, c]) => s + c, 0);

  // build conic-gradient stops (last slice absorbs rounding to reach 100%)
  let cumulative = 0;
  const stops: string[] = [];
  catEntries.forEach(([key, count], idx) => {
    const pct = catTotal > 0 ? (count / catTotal) * 100 : 0;
    const start = cumulative;
    cumulative += pct;
    const end = idx === catEntries.length - 1 ? 100 : cumulative;
    stops.push(`${colorByLabel(key)} ${start}% ${end}%`);
  });
  const pieGradient =
    stops.length > 0 ? `conic-gradient(${stops.join(", ")})` : "var(--panel-3)";

  const trend = summary?.daily_trend ?? [];
  const maxCount = trend.reduce((m, p) => Math.max(m, p.count), 0);

  const senders = summary?.top_senders ?? [];
  const maxSender = senders.reduce((m, s) => Math.max(m, s.count), 0);

  const prio = summary?.priority_dist ?? { high: 0, medium: 0, low: 0 };
  const prioTotal = prio.high + prio.medium + prio.low;

  const actions = summary?.action_dist ?? {};
  const replyCount = actions.reply ?? 0;

  return (
    <div className="report-view">
      <div className="report-controls">
        <div className="report-range-tabs">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              className={`report-range-tab ${range === opt.key ? "active" : ""}`}
              onClick={() => setRange(opt.key)}
            >
              {t(opt.label)}
            </button>
          ))}
        </div>
        <div className="report-controls-right">
          <select
            className="report-account-select"
            value={accountId}
            onChange={(e) =>
              setAccountId(
                e.target.value === "all" ? "all" : Number(e.target.value),
              )
            }
          >
            <option value="all">{t("report.allAccounts")}</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {(PLATFORM_LABEL[a.platform] ?? a.platform) + " · " + a.email}
              </option>
            ))}
          </select>
          {summary && (
            <span className="report-date-range mono">
              {summary.start_date} {t("report.dateRange")} {summary.end_date}
            </span>
          )}
          <button className="btn small ghost" onClick={load} disabled={loading}>
            {loading ? t("action.refreshing") : t("action.refresh")}
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}

      {loading && !summary ? (
        <div className="loading">{t("misc.loading")}</div>
      ) : summary ? (
        <>
          <section className="report-cards">
            <div className="report-card">
              <span className="report-card-label">{t("report.total")}</span>
              <span className="report-card-value mono">{summary.total}</span>
            </div>
            <div className="report-card">
              <span className="report-card-label">{t("report.unread")}</span>
              <span className="report-card-value mono">{summary.unread}</span>
            </div>
            <div className="report-card">
              <span className="report-card-label">{t("report.ads")}</span>
              <span className="report-card-value mono">{summary.ads}</span>
            </div>
            <div className="report-card">
              <span className="report-card-label">{t("report.pending")}</span>
              <span className="report-card-value mono">{replyCount}</span>
            </div>
          </section>

          {total === 0 ? (
            <div className="empty-state">
              <p className="empty-title">{t("report.empty")}</p>
              <p className="empty-sub">{t("report.emptySub")}</p>
            </div>
          ) : (
            <>
              <section className="report-section">
                <h3 className="report-section-title">{t("report.categoryDist")}</h3>
                <div className="report-pie-wrap">
                  <div
                    className="pie-chart"
                    style={{ background: pieGradient }}
                  />
                  <ul className="pie-legend">
                    {catEntries.map(([key, count]) => {
                      const pct =
                        catTotal > 0
                          ? Math.round((count / catTotal) * 100)
                          : 0;
                      return (
                        <li key={key} className="pie-legend-item">
                          <span
                            className="pie-legend-dot"
                            style={{ background: colorByLabel(key) }}
                          />
                          <span className="pie-legend-name">{key}</span>
                          <span className="pie-legend-count mono">{count}</span>
                          <span className="pie-legend-pct mono">{pct}%</span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </section>

              <section className="report-section">
                <h3 className="report-section-title">{t("report.dailyTrend")}</h3>
                {trend.length === 0 ? (
                  <div className="report-empty-hint">{t("report.noData")}</div>
                ) : (
                  <div className="bar-chart">
                    {trend.map((p) => {
                      const h = maxCount > 0 ? (p.count / maxCount) * 100 : 0;
                      return (
                        <div
                          key={p.date}
                          className="bar-item"
                          title={`${p.date} · ${p.count} ${t("account.count")}`}
                        >
                          <span className="bar-value mono">{p.count}</span>
                          <div className="bar-track">
                            <div
                              className="bar-fill"
                              style={{ height: `${Math.max(h, 3)}%` }}
                            />
                          </div>
                          <span className="bar-label mono">
                            {fmtDate(p.date)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>

              <section className="report-section">
                <h3 className="report-section-title">{t("report.topSenders")}</h3>
                {senders.length === 0 ? (
                  <div className="report-empty-hint">{t("report.noData")}</div>
                ) : (
                  <ul className="sender-list">
                    {senders.map((s) => {
                      const w =
                        maxSender > 0 ? (s.count / maxSender) * 100 : 0;
                      const name =
                        s.sender_name || s.sender_email || t("misc.unknown");
                      return (
                        <li
                          key={s.sender_email + ":" + s.count}
                          className="sender-row"
                        >
                          <div className="sender-info">
                            <span className="sender-name">{name}</span>
                            <span className="sender-email mono">
                              {s.sender_email}
                            </span>
                          </div>
                          <div className="sender-bar-track">
                            <div
                              className="sender-bar"
                              style={{ width: `${Math.max(w, 2)}%` }}
                            />
                          </div>
                          <span className="sender-count mono">{s.count}</span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>

              <section className="report-section">
                <h3 className="report-section-title">{t("report.priorityDist")}</h3>
                <div className="priority-dist">
                  {PRIORITY_ROWS.map((p) => {
                    const count = prio[p.key];
                    const pct =
                      prioTotal > 0
                        ? Math.round((count / prioTotal) * 100)
                        : 0;
                    return (
                      <div key={p.key} className="priority-row">
                        <span
                          className="priority-dot"
                          style={{ background: p.color }}
                        />
                        <span className="priority-label">{t(p.label)}</span>
                        <div className="priority-bar-track">
                          <div
                            className="priority-bar"
                            style={{ width: `${pct}%`, background: p.color }}
                          />
                        </div>
                        <span className="priority-count mono">
                          {count} · {pct}%
                        </span>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="report-section">
                <h3 className="report-section-title">{t("report.actionDist")}</h3>
                <div className="action-dist">
                  {ACTION_KEYS.map((a) => (
                    <div key={a} className="action-card">
                      <span className="action-card-label">
                        {t(ACTION_LABELS[a] ?? a)}
                      </span>
                      <span className="action-card-value mono">
                        {actions[a] ?? 0}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </>
      ) : null}
    </div>
  );
}
