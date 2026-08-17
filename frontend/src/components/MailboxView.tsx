import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { api } from "../api";
import { EmailDetailPanel } from "./EmailDetailPanel";
import { useI18n } from "../i18n";
import {
  PLATFORM_LABEL,
  type EmailAccount,
  type EmailCategory,
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
  const [starredOnly, setStarredOnly] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [category, setCategory] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [categories, setCategories] = useState<EmailCategory[]>([]);
  const offsetRef = useRef(0);

  // account_id -> custom color (as configured in Settings). Lets the mailbox
  // platform tag use the same color the user picked for each account.
  const accountColorMap = useMemo(() => {
    const map: Record<number, string> = {};
    for (const a of accounts) {
      if (a.color) map[a.id] = a.color;
    }
    return map;
  }, [accounts]);

  // Dynamic category list — populated by the AI as it discovers new categories
  // and by the user via Settings → category management.
  useEffect(() => {
    api.listCategories().then(setCategories).catch(() => {});
  }, []);

  const CATEGORY_OPTIONS = useMemo(
    () => [
      { value: "", label: t("cat.all") },
      ...categories.map((c) => ({ value: c.name, label: c.label })),
    ],
    [categories, t],
  );

  const CATEGORY_LABELS = useMemo<Record<string, string>>(
    () => Object.fromEntries(categories.map((c) => [c.name, c.label])),
    [categories],
  );

  // Debounce search input — avoids hammering the API on every keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  const loadPage = useCallback(
    (offset: number, append: boolean) => {
      setLoading(true);
      setError("");
      api
        .listEmails({
          account_id: accountId,
          category: category || undefined,
          unread_only: unreadOnly,
          starred_only: starredOnly,
          archived: showArchived,
          q: debouncedQuery || undefined,
          limit: PAGE_SIZE,
          offset,
        })
        .then((res) => {
          setEmails((prev) => (append ? [...prev, ...res.items] : res.items));
          setTotal(res.total);
          offsetRef.current = offset;
        })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setLoading(false));
    },
    [accountId, unreadOnly, starredOnly, showArchived, category, debouncedQuery],
  );

  // Load emails when any filter changes (resets to first page).
  useEffect(() => {
    setSelected(new Set());
    loadPage(0, false);
  }, [loadPage]);

  const loadMore = useCallback(async () => {
    if (loadingMore || emails.length >= total) return;
    setLoadingMore(true);
    try {
      await loadPage(offsetRef.current + PAGE_SIZE, true);
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, emails.length, total, loadPage]);

  async function refresh() {
    if (accountId) {
      await api.syncAccount(accountId, 7).catch(() => {});
    } else {
      await Promise.all(accounts.map((a) => api.syncAccount(a.id, 7).catch(() => {})));
    }
    onRefreshAccounts();
    setSelected(new Set());
    loadPage(0, false);
  }

  function handleReadChange(id: number) {
    setEmails((prev) =>
      prev.map((e) => (e.id === id ? { ...e, is_read: true } : e)),
    );
  }

  // ---- selection helpers ----
  const allSelected = emails.length > 0 && emails.every((e) => selected.has(e.id));
  const someSelected = !allSelected && emails.some((e) => selected.has(e.id));

  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        emails.forEach((e) => next.delete(e.id));
      } else {
        emails.forEach((e) => next.add(e.id));
      }
      return next;
    });
  }

  async function doBatch(action: string) {
    if (selected.size === 0) return;
    setBatchBusy(true);
    try {
      await api.batchEmailAction([...selected], action);
      setSelected(new Set());
      loadPage(0, false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
    }
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
          <label className="toggle">
            <input
              type="checkbox"
              checked={starredOnly}
              onChange={(e) => setStarredOnly(e.target.checked)}
            />
            <span>★</span>
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            <span>{t("mailbox.archived")}</span>
          </label>
        </div>
        <div className="filter-group right">
          <span className="count mono">{total} {t("account.count")}</span>
          <button className="btn small ghost" onClick={refresh} disabled={loading}>
            {loading ? t("action.refreshing") : t("action.refresh")}
          </button>
        </div>
      </div>

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="batch-bar">
          <label className="toggle batch-check">
            <input
              type="checkbox"
              checked={allSelected}
              ref={(el) => {
                if (el) el.indeterminate = someSelected;
              }}
              onChange={toggleSelectAll}
            />
          </label>
          <span className="batch-count">{t("mailbox.selected", { n: selected.size })}</span>
          <button className="btn small ghost" onClick={() => doBatch("read")} disabled={batchBusy}>
            {t("batch.read")}
          </button>
          <button className="btn small ghost" onClick={() => doBatch("unread")} disabled={batchBusy}>
            {t("batch.unread")}
          </button>
          {!showArchived ? (
            <button className="btn small ghost" onClick={() => doBatch("archive")} disabled={batchBusy}>
              {t("batch.archive")}
            </button>
          ) : (
            <button className="btn small ghost" onClick={() => doBatch("unarchive")} disabled={batchBusy}>
              {t("batch.unarchive")}
            </button>
          )}
          <button className="btn small ghost" onClick={() => doBatch("star")} disabled={batchBusy}>
            ★ {t("batch.star")}
          </button>
          <button className="btn small ghost" onClick={() => doBatch("unstar")} disabled={batchBusy}>
            {t("batch.unstar")}
          </button>
          <button
            className="btn small danger"
            onClick={() => {
              if (confirm(t("batch.confirmDelete"))) doBatch("delete");
            }}
            disabled={batchBusy}
          >
            {t("batch.delete")}
          </button>
        </div>
      )}

      {error && <div className="alert error">{error}</div>}

      {emails.length === 0 && !loading ? (
        <div className="empty-state">
          <p className="empty-title">{showArchived ? t("mailbox.emptyArchived") : t("mailbox.empty")}</p>
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
                className={`email-row ${m.is_read ? "read" : "unread"} ${selected.has(m.id) ? "selected" : ""}`}
                onClick={() => setSelectedEmailId(m.id)}
              >
                <div
                  className="email-row-check"
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleSelect(m.id);
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(m.id)}
                    readOnly
                    onChange={() => {}}
                  />
                </div>
                {m.is_starred && <span className="email-star">★</span>}
                <div className="email-row-main">
                  <div className="email-row-top">
                    <span
                      className={`platform-tag ${m.platform}`}
                      style={
                        accountColorMap[m.account_id]
                          ? ({ "--tag-color": accountColorMap[m.account_id] } as CSSProperties)
                          : undefined
                      }
                    >
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
