import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import {
  PLATFORM_LABEL,
  type EmailAccount,
  type MailDirection,
  type UnifiedEmail,
} from "../types";

interface Toast {
  kind: "success" | "error" | "info";
  text: string;
}

interface SyncResult {
  imported: number;
  matched: number;
  message?: string;
  error?: string;
}

interface ReplyTrackingViewProps {
  accounts: EmailAccount[];
}

function priorityClass(score: number | null | undefined): string {
  if (score === null || score === undefined) return "priority-low";
  if (score >= 70) return "priority-high";
  if (score >= 40) return "priority-mid";
  return "priority-low";
}

function priorityLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return "P—";
  return `P${score}`;
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}

export function ReplyTrackingView({ accounts }: ReplyTrackingViewProps) {
  const { t } = useI18n();
  const [accountId, setAccountId] = useState<number | "all">("all");
  const [pending, setPending] = useState<UnifiedEmail[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);

  // 当前展开查看线程的邮件 id（弹窗模式）
  const [threadEmail, setThreadEmail] = useState<UnifiedEmail | null>(null);

  const showToast = useCallback((kind: Toast["kind"], text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast(null), 2800);
  }, []);

  const loadPending = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.getPendingReplies(
        accountId === "all" ? undefined : accountId,
      );
      setPending(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  useEffect(() => {
    loadPending();
  }, [loadPending]);

  async function handleSyncSent() {
    if (accounts.length === 0) {
      showToast("info", t("replies.noAccount"));
      return;
    }

    const targets =
      accountId === "all"
        ? accounts
        : accounts.filter((a) => a.id === accountId);

    if (targets.length === 0) {
      showToast("info", t("replies.selectAccount"));
      return;
    }

    setSyncing(true);
    setError("");
    let totalImported = 0;
    let totalMatched = 0;
    const errors: string[] = [];
    let lastMessage = "";

    try {
      for (const acc of targets) {
        try {
          const res: SyncResult = await api.syncSentMail(acc.id);
          if (res.error) {
            errors.push(`${acc.email}: ${res.error}`);
          } else {
            totalImported += res.imported ?? 0;
            totalMatched += res.matched ?? 0;
            if (res.message) lastMessage = res.message;
          }
        } catch (e) {
          errors.push(
            `${acc.email}: ${e instanceof Error ? e.message : String(e)}`,
          );
        }
      }

      if (errors.length > 0 && totalImported === 0 && totalMatched === 0) {
        showToast("error", `${t("replies.syncFailed")}${errors[0]}`);
      } else if (errors.length > 0) {
        showToast(
          "info",
          `${t("replies.syncPartial")} ${totalImported} ${t("account.count")}${t("replies.matched")} ${totalMatched} ${t("account.count")}`,
        );
      } else if (lastMessage && totalImported === 0) {
        showToast("info", lastMessage);
      } else {
        showToast(
          "success",
          `${t("replies.syncDone")} ${totalImported} ${t("account.count")}${t("replies.matched")} ${totalMatched} ${t("account.count")}`,
        );
      }
      await loadPending();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  }

  const syncDisabled =
    syncing ||
    loading ||
    accounts.length === 0 ||
    (accountId !== "all" && !accounts.some((a) => a.id === accountId));

  const syncLabel = syncing
    ? t("replies.syncing")
    : accountId === "all"
      ? t("replies.syncAll")
      : t("replies.syncOne");

  const selectedAccountLabel =
    accountId === "all"
      ? t("replies.allAccounts")
      : (() => {
          const acc = accounts.find((a) => a.id === accountId);
          return acc
            ? (PLATFORM_LABEL[acc.platform] ?? acc.platform) + " · " + acc.email
            : "—";
        })();

  return (
    <div className="reply-view">
      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}

      <section className="reply-stats">
        <div className="reply-stat-card">
          <span className="reply-stat-label">{t("replies.pending")}</span>
          <span className="reply-stat-value mono">{pending.length}</span>
        </div>
        <div className="reply-stat-card">
          <span className="reply-stat-label">{t("replies.currentFilter")}</span>
          <span className="reply-stat-value">{selectedAccountLabel}</span>
        </div>
        <div className="reply-stat-card">
          <span className="reply-stat-label">{t("replies.syncedAccounts")}</span>
          <span className="reply-stat-value mono">{accounts.length}</span>
        </div>
      </section>

      <div className="reply-controls">
        <div className="reply-controls-left">
          <label className="reply-field">
            <span>{t("replies.account")}</span>
            <select
              value={accountId}
              onChange={(e) =>
                setAccountId(
                  e.target.value === "all" ? "all" : Number(e.target.value),
                )
              }
              disabled={syncing}
            >
              <option value="all">{t("replies.allAccounts")}</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {(PLATFORM_LABEL[a.platform] ?? a.platform) + " · " + a.email}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="reply-controls-right">
          <button
            className="btn small primary-soft"
            onClick={handleSyncSent}
            disabled={syncDisabled}
            title={
              accountId === "all" && accounts.length > 0
                ? t("replies.confirmSyncAll")
                : t("replies.confirmSyncAllSub")
            }
          >
            {syncLabel}
          </button>
          <button
            className="btn small ghost"
            onClick={loadPending}
            disabled={loading || syncing}
          >
            {loading ? t("action.refreshing") : t("action.refresh")}
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}

      {loading ? (
        <div className="loading">{t("misc.loading")}</div>
      ) : pending.length === 0 ? (
        <div className="empty-state">
          <p className="empty-title">{t("replies.empty")}</p>
          <p className="empty-sub">{t("replies.emptySub")}</p>
        </div>
      ) : (
        <ul className="reply-list">
          {pending.map((m) => {
            const senderLabel = m.sender || m.sender_email || t("misc.unknownSender");
            return (
              <li
                key={m.id}
                className="reply-item"
                onClick={() => setThreadEmail(m)}
              >
                <div className="reply-item-main">
                  <div className="reply-item-top">
                    <span
                      className={`tag priority ${priorityClass(m.priority_score)}`}
                    >
                      {priorityLabel(m.priority_score)}
                    </span>
                    <span className={`platform-tag ${m.platform}`}>
                      {PLATFORM_LABEL[m.platform] ?? m.platform}
                    </span>
                    <span className="reply-sender">{senderLabel}</span>
                    <span className="reply-time mono">
                      {fmtTime(m.received_at)}
                    </span>
                  </div>
                  <div className="reply-subject">
                    {m.subject || t("misc.noSubject")}
                  </div>
                  {m.summary ? (
                    <div className="reply-summary">{m.summary}</div>
                  ) : (
                    <div className="reply-summary reply-summary-empty">
                      {m.body_snippet || t("misc.noSnippet")}
                    </div>
                  )}
                </div>
                <div className="reply-item-action">
                  <span className="hint">{t("replies.viewThread")}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {threadEmail && (
        <ThreadDialog
          email={threadEmail}
          onClose={() => setThreadEmail(null)}
        />
      )}
    </div>
  );
}

interface ThreadDialogProps {
  email: UnifiedEmail;
  onClose: () => void;
}

function ThreadDialog({ email, onClose }: ThreadDialogProps) {
  const { t } = useI18n();
  const [thread, setThread] = useState<UnifiedEmail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const DIRECTION_LABELS: Record<MailDirection, string> = {
    inbox: t("replies.inbox"),
    sent: t("replies.sent"),
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .getReplyThread(email.id)
      .then((data) => {
        if (cancelled) return;
        setThread(data);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [email.id]);

  const hasReplies = thread.length > 1;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal thread-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <div>
            <h2>{email.subject || t("misc.noSubject")}</h2>
            <p className="thread-modal-sub hint">
              {t("replies.threadTitlePrefix")}{thread.length}{t("replies.threadTitleSuffix")}
            </p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label={t("misc.close")}>
            ✕
          </button>
        </header>

        <div className="modal-body">
          {loading ? (
            <p className="hint">{t("replies.loadingThread")}</p>
          ) : error ? (
            <div className="alert error">{error}</div>
          ) : thread.length === 0 ? (
            <p className="hint">{t("replies.threadNotFound")}</p>
          ) : (
            <>
              {!hasReplies && (
                <div className="thread-empty-hint">
                  {t("replies.threadEmpty")}
                </div>
              )}
              <ul className="thread-view">
                {thread.map((m) => {
                  const isSent = m.direction === "sent";
                  const senderLabel =
                    m.sender || m.sender_email || t("misc.unknownSender");
                  return (
                    <li
                      key={m.id}
                      className={`thread-item ${isSent ? "sent" : "inbox"}`}
                    >
                      <div className="thread-item-bubble">
                        <div className="thread-item-head">
                          <span
                            className={`thread-direction ${isSent ? "sent" : "inbox"}`}
                          >
                            {DIRECTION_LABELS[m.direction]}
                          </span>
                          <span className="thread-from">{senderLabel}</span>
                          <span className="thread-time mono">
                            {fmtTime(m.received_at)}
                          </span>
                        </div>
                        {m.recipients && m.recipients.length > 0 && (
                          <div className="thread-recipients">
                            <span className="dim">{t("replies.recipients")}</span>
                            <span className="mono">
                              {m.recipients.join(", ")}
                            </span>
                          </div>
                        )}
                        <div className="thread-subject">
                          {m.subject || t("misc.noSubject")}
                        </div>
                        <p className="thread-snippet">
                          {m.body_snippet || t("misc.noSnippet")}
                        </p>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>

        <footer className="modal-foot">
          <button className="btn primary" onClick={onClose}>
            {t("misc.close")}
          </button>
        </footer>
      </div>
    </div>
  );
}
