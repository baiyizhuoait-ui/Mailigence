import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { PLATFORM_LABEL, type AdStats, type BlockedSender, type EmailCategory, type UnifiedEmail } from "../types";
import { useI18n } from "../i18n";

const AD_LIST_LIMIT = 500;

type Tab = "ads" | "blocked";

interface Toast {
  kind: "success" | "error" | "info";
  text: string;
}

export function AdManagementView() {
  const { t } = useI18n();
  const [stats, setStats] = useState<AdStats | null>(null);
  const [ads, setAds] = useState<UnifiedEmail[]>([]);
  const [blocked, setBlocked] = useState<BlockedSender[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);
  const [tab, setTab] = useState<Tab>("ads");
  const [categories, setCategories] = useState<EmailCategory[]>([]);

  // Dynamic category labels (name -> display label) from the category table.
  const catLabel = useCallback(
    (name: string) => {
      const hit = categories.find((c) => c.name === name);
      return hit?.label ?? name;
    },
    [categories],
  );

  useEffect(() => {
    api.listCategories().then(setCategories).catch(() => {});
  }, []);

  const showToast = useCallback((kind: Toast["kind"], text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast(null), 2600);
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [statsRes, emailsRes, blockedRes] = await Promise.all([
        api.getAdStats().catch((e) => {
          throw e;
        }),
        api.listEmails({ limit: AD_LIST_LIMIT, offset: 0 }).catch((e) => {
          throw e;
        }),
        api.listBlockedSenders().catch((e) => {
          throw e;
        }),
      ]);
      setStats(statsRes);
      setAds(emailsRes.items.filter((m) => m.is_advertisement === true));
      setBlocked(blockedRes);
      setSelectedIds(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const allSelected = ads.length > 0 && ads.every((m) => selectedIds.has(m.id));
  const someSelected = ads.some((m) => selectedIds.has(m.id)) && !allSelected;

  function toggleOne(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(ads.map((m) => m.id)));
    }
  }

  async function runBatchAction(action: "delete" | "mark_read") {
    const ids = Array.from(selectedIds);
    const scope = ids.length > 0 ? `${ids.length} ${t("account.count")}` : t("mailbox.all");
    const verb = action === "delete" ? t("action.delete") : t("mailbox.markRead");
    if (ids.length === 0 && ads.length === 0) return;
    if (
      ids.length === 0 &&
      action === "delete" &&
      !window.confirm(`${t("action.confirm")}${verb}${t("mailbox.all")}${t("ads.title")}（${scope}）？${t("ads.confirmAction")}`)
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api.batchAdAction(action, ids);
      showToast("success", `${t("ads.affected")}${verb} ${res.affected} ${t("account.count")}${t("ads.title")}`);
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleBlock(emailId: number, senderLabel: string) {
    setBusy(true);
    setError("");
    try {
      await api.blockSenderByEmail(emailId);
      showToast("success", `${t("ads.blockedSenders")} ${senderLabel}`);
      await loadAll();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.startsWith("409")) {
        showToast("info", t("ads.alreadyBlocked"));
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleUnsubscribe(emailId: number) {
    setBusy(true);
    setError("");
    try {
      const info = await api.getUnsubscribeInfo(emailId);
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
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveBlocked(id: number, senderEmail: string) {
    setBusy(true);
    setError("");
    try {
      await api.removeBlockedSender(id);
      showToast("success", `${t("ads.unblocked")} ${senderEmail}`);
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ad-management">
      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}

      <div className="ad-toolbar">
        <div className="ad-tabs">
          <button
            className={`ad-tab ${tab === "ads" ? "active" : ""}`}
            onClick={() => setTab("ads")}
          >
            {t("ads.title")}
            <span className="mono">{stats?.total_ads ?? 0}</span>
          </button>
          <button
            className={`ad-tab ${tab === "blocked" ? "active" : ""}`}
            onClick={() => setTab("blocked")}
          >
            {t("ads.blockSender")}
            <span className="mono">{stats?.blocked_senders ?? 0}</span>
          </button>
        </div>
        <button className="btn small ghost" onClick={loadAll} disabled={loading || busy}>
          {loading ? t("action.refreshing") : t("action.refresh")}
        </button>
      </div>

      {error && <div className="alert error">{error}</div>}

      <section className="ad-stats-grid">
        <div className="ad-stat-card">
          <span className="ad-stat-label">{t("ads.totalAds")}</span>
          <span className="ad-stat-value mono">{stats?.total_ads ?? "—"}</span>
        </div>
        <div className="ad-stat-card">
          <span className="ad-stat-label">{t("ads.blockedSenders")}</span>
          <span className="ad-stat-value mono">{stats?.blocked_senders ?? "—"}</span>
        </div>
        <div className="ad-stat-card ad-stat-card-wide">
          <span className="ad-stat-label">{t("ads.categoryDist")}</span>
          <div className="ad-stat-tags">
            {stats && Object.entries(stats.ads_by_category).length > 0 ? (
              Object.entries(stats.ads_by_category)
                .sort((a, b) => b[1] - a[1])
                .map(([cat, count]) => (
                  <span key={cat} className={`tag category ${cat}`}>
                    {catLabel(cat)} · {count}
                  </span>
                ))
            ) : (
              <span className="hint">{t("ads.noCategoryData")}</span>
            )}
          </div>
        </div>
      </section>

      {tab === "ads" ? (
        <section className="ad-section">
          <div className="ad-batch-bar">
            <label className="toggle">
              <input
                type="checkbox"
                checked={allSelected}
                ref={(el) => {
                  if (el) el.indeterminate = someSelected;
                }}
                onChange={toggleAll}
                disabled={ads.length === 0}
              />
              <span>{t("ads.selectAll")}{selectedIds.size}/{ads.length}）</span>
            </label>
            <div className="ad-batch-actions">
              <button
                className="btn small danger"
                onClick={() => runBatchAction("delete")}
                disabled={busy || ads.length === 0}
              >
                {t("ads.batchDelete")}
              </button>
              <button
                className="btn small primary-soft"
                onClick={() => runBatchAction("mark_read")}
                disabled={busy || ads.length === 0}
              >
                {t("ads.batchMarkRead")}
              </button>
            </div>
          </div>

          {loading ? (
            <div className="loading">{t("misc.loading")}</div>
          ) : ads.length === 0 ? (
            <div className="empty-state">
              <p className="empty-title">{t("ads.empty")}</p>
              <p className="empty-sub">
                {t("ads.emptySub")}
              </p>
            </div>
          ) : (
            <ul className="ad-list">
              {ads.map((m) => {
                const senderLabel = m.sender || m.sender_email || t("misc.unknownSender");
                const checked = selectedIds.has(m.id);
                return (
                  <li key={m.id} className={`ad-row ${checked ? "selected" : ""}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleOne(m.id)}
                      className="ad-check"
                    />
                    <div className="ad-row-main">
                      <div className="ad-row-top">
                        <span className={`platform-tag ${m.platform}`}>
                          {PLATFORM_LABEL[m.platform] ?? m.platform}
                        </span>
                        <span className="email-sender">{senderLabel}</span>
                        <span className="email-time mono">
                          {m.received_at
                            ? new Date(m.received_at).toLocaleString("zh-CN")
                            : ""}
                        </span>
                      </div>
                      <div className="email-subject">{m.subject || t("misc.noSubject")}</div>
                      {m.category && (
                        <span className={`tag category ${m.category}`}>
                          {catLabel(m.category)}
                        </span>
                      )}
                    </div>
                    <div className="ad-row-actions">
                      <button
                        className="btn small warn"
                        onClick={() => handleBlock(m.id, senderLabel)}
                        disabled={busy}
                      >
                        {t("ads.blockSender")}
                      </button>
                      <button
                        className="btn small info"
                        onClick={() => handleUnsubscribe(m.id)}
                        disabled={busy}
                      >
                        {t("ads.unsubscribe")}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ) : (
        <section className="ad-section">
          {loading ? (
            <div className="loading">{t("misc.loading")}</div>
          ) : blocked.length === 0 ? (
            <div className="empty-state">
              <p className="empty-title">{t("ads.blockedEmpty")}</p>
              <p className="empty-sub">
                {t("ads.blockedEmptySub")}
              </p>
            </div>
          ) : (
            <ul className="blocked-list">
              {blocked.map((b) => (
                <li key={b.id} className="blocked-row">
                  <div className="blocked-row-main">
                    <div className="blocked-row-top">
                      <span className="blocked-name">
                        {b.sender_name || b.sender_email}
                      </span>
                      <span className="blocked-email mono">{b.sender_email}</span>
                    </div>
                    <div className="blocked-row-meta">
                      {b.reason && (
                        <span className="tag category other">{b.reason}</span>
                      )}
                      {b.account_id !== null && b.account_id !== undefined && (
                        <span className="hint">{t("ads.accountId")}{b.account_id}</span>
                      )}
                      {b.created_at && (
                        <span className="email-time mono">
                          {t("ads.blockedSince")} {new Date(b.created_at).toLocaleString("zh-CN")}
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    className="btn small ghost"
                    onClick={() => handleRemoveBlocked(b.id, b.sender_email)}
                    disabled={busy}
                  >
                    {t("ads.removeBlock")}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
