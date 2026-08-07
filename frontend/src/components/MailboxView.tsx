import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { EmailDetailPanel } from "./EmailDetailPanel";
import { useI18n } from "../i18n";
import {
  PLATFORM_LABEL,
  type EmailAccount,
  type UnifiedEmail,
} from "../types";

interface Props {
  accounts: EmailAccount[];
  onRefreshAccounts: () => void;
}

const PAGE_SIZE = 50;

function priorityClass(score: number | null): string {
  if (score === null) return "";
  if (score >= 80) return "priority-high";
  if (score >= 50) return "priority-mid";
  return "priority-low";
}

export function MailboxView({ accounts, onRefreshAccounts }: Props) {
  const { t } = useI18n();
  const [emails, setEmails] = useState<UnifiedEmail[]>([]);
  const [total, setTotal] = useState(0);
  const [accountId, setAccountId] = useState<number | undefined>(undefined);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [category, setCategory] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);
  const offsetRef = useRef(0);

  const CATEGORY_OPTIONS = useMemo(
    () => [
      { value: "", label: t("cat.all") },
      { value: "work", label: t("cat.work") },
      { value: "meeting", label: t("cat.meeting") },
      { value: "finance", label: t("cat.finance") },
      { value: "notification", label: t("cat.system") },
      { value: "social", label: t("cat.social") },
      { value: "travel", label: t("cat.travel") },
      { value: "shopping", label: t("cat.shopping") },
      { value: "marketing", label: t("cat.ad") },
      { value: "newsletter", label: t("cat.newsletter") },
      { value: "personal", label: t("cat.personal") },
      { value: "other", label: t("cat.other") },
    ],
    [t],
  );

  const CATEGORY_LABELS = useMemo<Record<string, string>>(
    () => ({
      work: t("cat.work"),
      meeting: t("cat.meeting"),
      finance: t("cat.finance"),
      notification: t("cat.system"),
      social: t("cat.social"),
      travel: t("cat.travel"),
      shopping: t("cat.shopping"),
      marketing: t("cat.ad"),
      newsletter: t("cat.newsletter"),
      personal: t("cat.personal"),
      other: t("cat.other"),
    }),
    [t],
  );

  // Debounce search input — avoids hammering the API on every keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Load emails when any filter changes (resets to first page).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    offsetRef.current = 0;
    api
      .listEmails({
        account_id: accountId,
        category: category || undefined,
        unread_only: unreadOnly,
        q: debouncedQuery || undefined,
        limit: PAGE_SIZE,
        offset: 0,
      })
      .then((res) => {
        if (cancelled) return;
        setEmails(res.items);
        setTotal(res.total);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [accountId, unreadOnly, category, debouncedQuery]);

  const loadMore = useCallback(async () => {
    if (loadingMore || emails.length >= total) return;
    setLoadingMore(true);
    const newOffset = offsetRef.current + PAGE_SIZE;
    try {
      const res = await api.listEmails({
        account_id: accountId,
        category: category || undefined,
        unread_only: unreadOnly,
        q: debouncedQuery || undefined,
        limit: PAGE_SIZE,
        offset: newOffset,
      });
      setEmails((prev) => [...prev, ...res.items]);
      offsetRef.current = newOffset;
    } catch {
      /* surfaced on next interaction */
    } finally {
      setLoadingMore(false);
    }
  }, [accountId, category, unreadOnly, debouncedQuery, emails.length, total, loadingMore]);

  async function refresh() {
    if (accountId) {
      await api.syncAccount(accountId, 7).catch(() => {});
    } else {
      await Promise.all(accounts.map((a) => api.syncAccount(a.id, 7).catch(() => {})));
    }
    onRefreshAccounts();
    setLoading(true);
    api
      .listEmails({
        account_id: accountId,
        category: category || undefined,
        unread_only: unreadOnly,
        q: debouncedQuery || undefined,
        limit: PAGE_SIZE,
        offset: 0,
      })
      .then((res) => {
        setEmails(res.items);
        setTotal(res.total);
        offsetRef.current = 0;
      })
      .finally(() => setLoading(false));
  }

  function handleReadChange(id: number) {
    setEmails((prev) =>
      prev.map((e) => (e.id === id ? { ...e, is_read: true } : e)),
    );
  }

  const hasMore = emails.length < total;

  return (
    <div className="mailbox">
      <div className="mailbox-toolbar">
        <div className="filter-group">
          <input
            type="text"
            className="search-input"
            placeholder={t("mailbox.searchPlaceholder")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <select
            value={accountId ?? ""}
            onChange={(e) =>
              setAccountId(e.target.value ? Number(e.target.value) : undefined)
            }
          >
            <option value="">{t("mailbox.allAccounts")}</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {PLATFORM_LABEL[a.platform] ?? a.platform} · {a.email}
              </option>
            ))}
          </select>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORY_OPTIONS.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
          <label className="toggle">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.target.checked)}
            />
            <span>{t("mailbox.unread")}</span>
          </label>
        </div>
        <div className="filter-group right">
          <span className="count mono">{total} {t("account.count")}</span>
          <button className="btn small ghost" onClick={refresh} disabled={loading}>
            {loading ? t("action.refreshing") : t("action.refresh")}
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}

      {emails.length === 0 && !loading ? (
        <div className="empty-state">
          <p className="empty-title">{t("mailbox.empty")}</p>
          <p className="empty-sub">
            {t("mailbox.emptySub")}
          </p>
        </div>
      ) : (
        <>
          <ul className="email-list">
            {emails.map((m) => (
              <li
                key={m.id}
                className={`email-row ${m.is_read ? "read" : "unread"}`}
                onClick={() => setSelectedEmailId(m.id)}
              >
                <div className="email-row-main">
                  <div className="email-row-top">
                    <span className={`platform-tag ${m.platform}`}>
                      {PLATFORM_LABEL[m.platform] ?? m.platform}
                    </span>
                    <span className="email-sender">{m.sender || m.sender_email || t("misc.unknownSender")}</span>
                    <span className="email-time mono">
                      {m.received_at ? new Date(m.received_at).toLocaleString("zh-CN") : ""}
                    </span>
                  </div>
                  <div className="email-subject">{m.subject || t("misc.noSubject")}</div>
                  <div className="email-snippet">{m.body_snippet}</div>
                </div>
                {(m.category || m.is_advertisement || m.priority_score !== null) && (
                  <div className="email-row-tags">
                    {m.category && (
                      <span className={`tag category ${m.category}`}>
                        {CATEGORY_LABELS[m.category] ?? m.category}
                      </span>
                    )}
                    {m.is_advertisement && <span className="tag ad">{t("mailbox.ad")}</span>}
                    {m.priority_score !== null && m.priority_score !== undefined && (
                      <span className={`tag priority ${priorityClass(m.priority_score)}`}>
                        P{m.priority_score}
                      </span>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {hasMore && (
            <div className="load-more">
              <button
                className="btn small ghost"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? t("misc.loading") : `${t("mailbox.loadMorePrefix")}${total - emails.length}${t("mailbox.loadMoreSuffix")}`}
              </button>
            </div>
          )}
        </>
      )}

      {selectedEmailId !== null && (
        <EmailDetailPanel
          emailId={selectedEmailId}
          onClose={() => setSelectedEmailId(null)}
          onReadChange={handleReadChange}
        />
      )}
    </div>
  );
}
