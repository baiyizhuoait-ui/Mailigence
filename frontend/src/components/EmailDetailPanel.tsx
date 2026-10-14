import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import {
  PLATFORM_LABEL,
  type DraftReply,
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

// Webmail home URLs used as the fallback for the "open original mailbox"
// deep link — Gmail additionally gets a precise per-message link.
const MAILBOX_HOME: Record<string, string> = {
  outlook: "https://outlook.live.com/mail/0/",
  qq: "https://mail.qq.com/",
  netease: "https://mail.163.com/",
  yahoo: "https://mail.yahoo.com/",
  icloud: "https://www.icloud.com/mail/",
  aol: "https://mail.aol.com/",
  zoho: "https://mail.zoho.com/",
  yandex: "https://mail.yandex.com/",
};

function priorityClass(score: number | null): string {
  if (score === null) return "";
  if (score >= 80) return "priority-high";
  if (score >= 50) return "priority-mid";
  return "priority-low";
}

// Wrap the email HTML in a document with a strict CSP so opening a marketing
// or phishing mail can't phone home: with `img-src data: cid:` remote tracking
// pixels stay blocked (no IP / read-time leak). The user can opt in per mail
// via the "load remote images" toggle, which widens img-src to http(s).
//
// The CSP <meta> is ALWAYS emitted as the first child of our own <head>, never
// spliced into the mail's own markup: regex-matching the mail's <head> was
// bypassable (e.g. a leading `<!-- <head> -->` comment swallowed the meta).
// The browser honours the earliest CSP meta in document order, and the mail's
// own (ignored-in-body) html/head tags can't precede ours.
function buildIframeDoc(html: string, allowRemoteImages: boolean): string {
  const imgSrc = allowRemoteImages ? "data: cid: http: https:" : "data: cid:";
  const csp =
    `default-src 'none'; style-src 'unsafe-inline'; ` +
    `img-src ${imgSrc}; font-src data:;`;
  const meta = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;
  return (
    `<!DOCTYPE html><html><head><meta charset="utf-8">${meta}</head>` +
    `<body>${html}</body></html>`
  );
}

export function EmailDetailPanel({ emailId, onClose, onReadChange }: Props) {
  const { t } = useI18n();
  const [email, setEmail] = useState<UnifiedEmail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [categories, setCategories] = useState<EmailCategory[]>([]);
  const [fullBody, setFullBody] = useState<{ html: string; text: string } | null>(null);
  const [loadingFull, setLoadingFull] = useState(false);
  const [fullError, setFullError] = useState<string | null>(null);
  // Per-mail opt-in to load remote images in the HTML preview (off = privacy).
  const [loadRemoteImages, setLoadRemoteImages] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  // Watches the framed document so the iframe keeps growing as remote images
  // (or web fonts) finish loading after the initial onLoad fires.
  const resizeObsRef = useRef<ResizeObserver | null>(null);
  // ---- AI reply drafts ----
  const [aiAvailable, setAiAvailable] = useState<boolean | null>(null);
  const [draftOpen, setDraftOpen] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [drafts, setDrafts] = useState<DraftReply[] | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftEdits, setDraftEdits] = useState<string[]>([]);
  // Index of the draft currently being sent (null = no send in flight).
  const [sendingIdx, setSendingIdx] = useState<number | null>(null);

  const catLabel = (name: string) =>
    categories.find((c) => c.name === name)?.label ?? name;

  useEffect(() => {
    api.listCategories().then(setCategories).catch(() => {});
  }, []);

  // Whether AI reply drafting is usable: needs a key and a non-rules mode.
  // If the settings fetch fails, assume available and let the backend 400.
  useEffect(() => {
    api
      .getAiSettings()
      .then((s) => setAiAvailable(s.api_key_configured && s.analysis_mode !== "rules_only"))
      .catch(() => setAiAvailable(true));
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

  // P1 feedback loop: correct the AI verdict; the backend stores the
  // correction and re-queues this sender's other mails for re-analysis.
  async function handleCorrectCategory(name: string) {
    if (!email || !name || name === email.category) return;
    setBusy(true);
    try {
      const updated = await api.updateEmailClassification(email.id, { category: name });
      setEmail((prev) => (prev ? { ...prev, ...updated } : updated));
      showToast("success", t("detail.correctedToast"));
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleAd() {
    if (!email || email.is_advertisement === null) return;
    setBusy(true);
    try {
      const updated = await api.updateEmailClassification(email.id, {
        is_advertisement: !email.is_advertisement,
      });
      setEmail((prev) => (prev ? { ...prev, ...updated } : updated));
      showToast("success", t("detail.correctedToast"));
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : String(e));
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

  async function handleViewFull() {
    if (!email) return;
    setLoadingFull(true);
    setFullError(null);
    try {
      const body = await api.getEmailFullBody(email.id);
      setFullBody(body);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setFullError(msg.replace(/^4\d\d:\s*/, ""));
    } finally {
      setLoadingFull(false);
    }
  }

  async function handleGenerateDrafts() {
    if (!email) return;
    setDraftLoading(true);
    setDraftError(null);
    try {
      const result = await api.generateDraftReplies(email.id);
      setDrafts(result);
      setDraftEdits(result.map((d) => d.body));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setDraftError(msg.replace(/^\d{3}:\s*/, ""));
    } finally {
      setDraftLoading(false);
    }
  }

  async function copyBody(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      showToast("success", t("draft.copied"));
    } catch {
      showToast("error", t("draft.copyFailed"));
    }
  }

  // Send the edited draft straight from the app (SMTP / Graph backend side).
  // Confirmation guards against accidental sends; the toast surfaces the
  // backend's friendly error (auth hint / connectivity) on failure.
  async function handleSendDraft(i: number) {
    if (!email || sendingIdx !== null) return;
    const content = (draftEdits[i] ?? "").trim();
    if (!content) return;
    if (!window.confirm(t("draft.confirmSend"))) return;
    setSendingIdx(i);
    try {
      await api.sendReply(email.id, content);
      showToast("success", t("draft.sent"));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      showToast("error", `${t("draft.sendFailed")}${msg.replace(/^\d{3}:\s*/, "")}`);
    } finally {
      setSendingIdx(null);
    }
  }

  function openMailboxReply() {
    if (!email) return;
    // Gmail: jump straight to this exact message via an RFC822 Message-ID search.
    if (email.platform === "gmail") {
      if (email.message_id) {
        const query = `rfc822msgid:${email.message_id}`;
        window.open(
          `https://mail.google.com/mail/u/0/#search/${encodeURIComponent(query)}`,
          "_blank",
          "noopener,noreferrer",
        );
      } else {
        window.open("https://mail.google.com/mail/u/0/", "_blank", "noopener,noreferrer");
      }
      return;
    }
    // Other providers degrade to their inbox; unknown ones get a hint instead.
    const url = MAILBOX_HOME[email.platform];
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
    } else {
      showToast("info", t("draft.noDeeplink"));
    }
  }

  // Escape closes the modal, matching the backdrop click behaviour.
  useEffect(() => {
    if (emailId === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [emailId, onClose]);

  useEffect(() => {
    if (emailId === null) {
      setEmail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFullBody(null);
    setFullError(null);
    setLoadRemoteImages(false);
    // Drafts belong to a single email — reset when switching.
    setDraftOpen(false);
    setDrafts(null);
    setDraftEdits([]);
    setDraftError(null);
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

  // Grow the framed document to its full height so the user scrolls a single
  // container instead of fighting nested scrollbars. No upper cap: long mails
  // stay fully readable (the outer modal-body already scrolls).
  const syncFrameHeight = () => {
    const frame = iframeRef.current;
    const doc = frame?.contentDocument;
    if (!frame || !doc?.body) return;
    const h = Math.max(doc.body.scrollHeight + 16, 320);
    frame.style.height = `${h}px`;
  };

  // Measure once on load, then keep re-measuring via ResizeObserver: remote
  // images load asynchronously and would otherwise be clipped below the fold.
  const attachFrameObserver = () => {
    const frame = iframeRef.current;
    const doc = frame?.contentDocument;
    if (!frame || !doc?.body) return;
    syncFrameHeight();
    resizeObsRef.current?.disconnect();
    try {
      const obs = new ResizeObserver(() => syncFrameHeight());
      obs.observe(doc.body);
      resizeObsRef.current = obs;
    } catch {
      /* ResizeObserver unavailable — the onLoad measurement still applies */
    }
  };

  useEffect(() => {
    return () => {
      resizeObsRef.current?.disconnect();
      resizeObsRef.current = null;
    };
  }, []);

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
                <div className="detail-body-head">
                  <p className="detail-section-title">{t("detail.bodyPreview")}</p>
                  {!fullBody && (
                    <button
                      className="btn small ghost"
                      onClick={handleViewFull}
                      disabled={loadingFull}
                    >
                      {loadingFull ? t("detail.fullLoading") : t("detail.viewFull")}
                    </button>
                  )}
                </div>
                {fullBody ? (
                  fullBody.html ? (
                    <>
                      <div className="detail-body-tools">
                        <span className="detail-images-hint">
                          {t("detail.imagesBlocked")}
                        </span>
                        <button
                          type="button"
                          className="btn small ghost"
                          onClick={() => setLoadRemoteImages((v) => !v)}
                        >
                          {t("detail.loadImages")}
                        </button>
                      </div>
                      {/* allow-same-origin (without allow-scripts) lets us
                          measure the document for auto-height; the framed HTML
                          still can't run JS, and the CSP meta blocks remote
                          images so tracking pixels can't load. */}
                      <iframe
                        ref={iframeRef}
                        className="full-body-frame"
                        sandbox="allow-same-origin"
                        srcDoc={buildIframeDoc(fullBody.html, loadRemoteImages)}
                        title={t("detail.viewFull")}
                        onLoad={attachFrameObserver}
                      />
                    </>
                  ) : (
                    <pre className="full-body-text">{fullBody.text}</pre>
                  )
                ) : (
                  <>
                    <p className="detail-snippet">{email.body_snippet || t("misc.noSnippet")}</p>
                    {fullError && (
                      <p className="detail-full-error">{t("detail.fullError")}{fullError}</p>
                    )}
                  </>
                )}
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
                        <select
                          className="ai-correct-select"
                          value={email.category}
                          disabled={busy}
                          title={t("detail.correctCategory")}
                          onChange={(e) => handleCorrectCategory(e.target.value)}
                        >
                          {!categories.some((c) => c.name === email.category) && (
                            <option value={email.category}>{email.category}</option>
                          )}
                          {categories.map((c) => (
                            <option key={c.id} value={c.name}>
                              {c.label}
                            </option>
                          ))}
                        </select>
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
                        <button
                          type="button"
                          className={`tag ${email.is_advertisement ? "ad" : "not-ad"} ai-correct-toggle`}
                          disabled={busy}
                          title={t("detail.correctAd")}
                          onClick={handleToggleAd}
                        >
                          {email.is_advertisement ? t("misc.yes") : t("misc.no")}
                        </button>
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

              <div
                className={`detail-ai-replies${aiAvailable === false ? " disabled" : ""}`}
                title={aiAvailable === false ? t("draft.aiDisabled") : undefined}
              >
                <div
                  className="detail-ai-replies-head"
                  onClick={() => setDraftOpen((v) => !v)}
                >
                  <p className="detail-section-title">{t("draft.sectionTitle")}</p>
                  <span className="draft-chevron">{draftOpen ? "▾" : "▸"}</span>
                </div>
                {draftOpen && (
                  <div className="detail-ai-replies-body">
                    {aiAvailable === false ? (
                      <p className="hint">{t("draft.aiDisabled")}</p>
                    ) : draftLoading ? (
                      <p className="hint">{t("draft.generating")}</p>
                    ) : draftError ? (
                      <div className="draft-error">
                        <span>
                          {t("draft.failed")}
                          {draftError}
                        </span>
                        <button className="btn small ghost" onClick={handleGenerateDrafts}>
                          {t("draft.retry")}
                        </button>
                      </div>
                    ) : drafts === null ? (
                      <button className="btn small primary-soft" onClick={handleGenerateDrafts}>
                        {t("draft.generate")}
                      </button>
                    ) : (
                      <div className="draft-list">
                        {drafts.map((draft, i) => (
                          <div className="draft-item" key={i}>
                            {draft.style && <div className="draft-style">{draft.style}</div>}
                            <textarea
                              className="draft-body"
                              rows={4}
                              value={draftEdits[i] ?? ""}
                              onChange={(e) => {
                                const next = [...draftEdits];
                                next[i] = e.target.value;
                                setDraftEdits(next);
                              }}
                            />
                            <div className="draft-actions">
                              <button
                                className="btn small primary"
                                disabled={sendingIdx !== null}
                                onClick={() => handleSendDraft(i)}
                              >
                                {sendingIdx === i ? t("draft.sending") : t("draft.send")}
                              </button>
                              <button
                                className="btn small ghost"
                                onClick={() => copyBody(draftEdits[i] ?? "")}
                              >
                                {t("draft.copy")}
                              </button>
                              <button className="btn small info" onClick={openMailboxReply}>
                                {t("draft.openMailbox")}
                              </button>
                            </div>
                          </div>
                        ))}
                        <button className="btn small ghost draft-regenerate" onClick={handleGenerateDrafts}>
                          {t("draft.regenerate")}
                        </button>
                      </div>
                    )}
                  </div>
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
