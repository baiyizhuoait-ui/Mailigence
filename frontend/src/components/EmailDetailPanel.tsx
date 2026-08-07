import { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import {
  PLATFORM_LABEL,
  type EmailCategory,
  type UnifiedEmail,
} from "../types";

interface Toast {
  kind: "success" | "error" | "info";
  text: string;
}

interface Props {
  emailId: number | null;
  onClose: () => void;
  onReadChange: (id: number) => void;
}

const ACTION_LABELS: Record<string, string> = {
  reply: "priority.reply",
  review: "priority.review",
  note: "priority.notice",
  ignore: "priority.none",
};

function priorityClass(score: number | null): string {
  if (score === null) return "";
  if (score >= 80) return "priority-high";
  if (score >= 50) return "priority-mid";
  return "priority-low";
}

export function EmailDetailPanel({ emailId, onClose, onReadChange }: Props) {
  const { t } = useI18n();
  const [email, setEmail] = useState<UnifiedEmail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [categories, setCategories] = useState<EmailCategory[]>([]);

  const catLabel = (name: string) =>
    categories.find((c) => c.name === name)?.label ?? name;

  useEffect(() => {
    api.listCategories().then(setCategories).catch(() => {});
  }, []);

  const showToast = (kind: Toast["kind"], text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast(null), 2600);
  };

  async function handleBlock() {
    if (!email) return;
    setBusy(true);
    try {
      await api.blockSenderByEmail(email.id);
      showToast("success", `${t("ads.blockedSender")} ${email.sender || email.sender_email || ""}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.startsWith("409")) {
        showToast("info", t("ads.alreadyBlocked"));
      } else {
        showToast("error", msg);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleUnsubscribe() {
    if (!email) return;
    setBusy(true);
    try {
      const info = await api.getUnsubscribeInfo(email.id);
      if (info.url) {
        window.open(info.url, "_blank", "noopener,noreferrer");
        showToast("success", t("ads.unsubOpened"));
      } else if (info.mailto) {
        window.location.href = info.mailto;
        showToast("info", t("ads.unsubTriggered"));
      } else {
        showToast("info", t("ads.unsubNotFound"));
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      showToast("error", msg);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (emailId === null) {
      setEmail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getEmail(emailId)
      .then((data) => {
        if (cancelled) return;
        setEmail(data);
        // Auto-mark as read when opened.
        if (!data.is_read) {
          api
            .markEmailRead(emailId)
            .then(() => onReadChange(emailId))
            .catch(() => {});
        }
      })
      .catch(() => !cancelled && setEmail(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [emailId, onReadChange]);

  if (emailId === null) return null;

  const analyzed = email?.analyzed_at !== null && email?.analyzed_at !== undefined;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal email-detail" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>{email?.subject || t("misc.noSubject")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("misc.close")}>✕</button>
        </header>

        <div className="modal-body">
          {loading ? (
            <p className="hint">{t("detail.loading")}</p>
          ) : !email ? (
            <p className="hint">{t("detail.loadError")}</p>
          ) : (
            <>
              <div className="detail-meta">
                <div className="detail-meta-row">
                  <span className="dim">{t("detail.sender")}</span>
                  <span>{email.sender || email.sender_email || t("misc.unknown")}</span>
                </div>
                {email.sender_email && email.sender && email.sender !== email.sender_email && (
                  <div className="detail-meta-row">
                    <span className="dim">{t("detail.email")}</span>
                    <span className="mono">{email.sender_email}</span>
                  </div>
                )}
                <div className="detail-meta-row">
                  <span className="dim">{t("detail.platform")}</span>
                  <span className={`platform-tag ${email.platform}`}>
                    {PLATFORM_LABEL[email.platform] ?? email.platform}
                  </span>
                </div>
                <div className="detail-meta-row">
                  <span className="dim">{t("detail.time")}</span>
                  <span className="mono">
                    {email.received_at ? new Date(email.received_at).toLocaleString("zh-CN") : "—"}
                  </span>
                </div>
                {email.recipients && email.recipients.length > 0 && (
                  <div className="detail-meta-row">
                    <span className="dim">{t("detail.recipients")}</span>
                    <span className="mono">{email.recipients.join(", ")}</span>
                  </div>
                )}
              </div>

              <div className="detail-body">
                <p className="detail-section-title">{t("detail.bodyPreview")}</p>
                <p className="detail-snippet">{email.body_snippet || t("misc.noSnippet")}</p>
              </div>

              <div className="detail-ai">
                <p className="detail-section-title">{t("detail.aiAnalysis")}</p>
                {analyzed ? (
                  <div className="ai-grid">
                    {email.category && (
                      <div className="ai-field">
                        <span className="ai-label">{t("detail.category")}</span>
                        <span className={`tag category ${email.category}`}>
                          {catLabel(email.category)}
                        </span>
                      </div>
                    )}
                    {email.priority_score !== null && email.priority_score !== undefined && (
                      <div className="ai-cell">
                        <span className="ai-label">{t("detail.priority")}</span>
                        <span className={`tag priority ${priorityClass(email.priority_score)}`}>
                          {email.priority_score}
                        </span>
                      </div>
                    )}
                    {email.is_advertisement !== null && (
                      <div className="ai-cell">
                        <span className="ai-label">{t("detail.ad")}</span>
                        <span className={`tag ${email.is_advertisement ? "ad" : "not-ad"}`}>
                          {email.is_advertisement ? t("misc.yes") : t("misc.no")}
                        </span>
                      </div>
                    )}
                    {email.suggested_action && (
                      <div className="ai-cell">
                        <span className="ai-label">{t("detail.suggestion")}</span>
                        <span className="tag action">
                          {t(ACTION_LABELS[email.suggested_action] ?? email.suggested_action)}
                        </span>
                      </div>
                    )}
                    {email.summary && (
                      <div className="ai-cell ai-summary">
                        <span className="ai-label">{t("detail.summary")}</span>
                        <span className="ai-summary-text">{email.summary}</span>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="hint">{t("detail.notAnalyzed")}</p>
                )}
              </div>

              <div className="detail-actions">
                <p className="detail-section-title">{t("detail.adGovernance")}</p>
                {toast && <div className={`alert ${toast.kind === "error" ? "error" : "success"}`}>{toast.text}</div>}
                <div className="detail-action-row">
                  <button
                    className="btn small warn"
                    onClick={handleBlock}
                    disabled={busy || !email.sender_email}
                  >
                    {t("detail.blockSender")}
                  </button>
                  <button
                    className="btn small info"
                    onClick={handleUnsubscribe}
                    disabled={busy}
                  >
                    {t("detail.unsubscribe")}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

        <footer className="modal-foot">
          <button className="btn primary" onClick={onClose}>{t("detail.close")}</button>
        </footer>
      </div>
    </div>
  );
}
