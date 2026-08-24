import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { EmailDetailPanel } from "./EmailDetailPanel";
import { useI18n } from "../i18n";
import type {
  EmailAccount,
  EmailCategory,
  SearchParams,
  SearchResult,
} from "../types";

interface Props {
  accounts: EmailAccount[];
  initialQ: string;
  /** Open this email's detail panel right after the page loads. */
  openEmailId: number | null;
}

const PAGE_SIZE = 20;

export function SearchResults({ accounts, initialQ, openEmailId }: Props) {
  const { t } = useI18n();
  const [q, setQ] = useState(initialQ);
  const [accountId, setAccountId] = useState<number | undefined>(undefined);
  const [category, setCategory] = useState<string>("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(openEmailId);
  const [categories, setCategories] = useState<EmailCategory[]>([]);
  const seq = useRef(0);
  const qTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    api.listCategories().then(setCategories).catch(() => {});
  }, []);

  const filters = useMemo<SearchParams>(() => {
    const params: SearchParams = { q: q.trim(), limit: PAGE_SIZE, offset: 0 };
    if (accountId !== undefined) params.account_id = accountId;
    if (category) params.category = category;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    return params;
  }, [q, accountId, category, dateFrom, dateTo]);

  const runSearch = useCallback(
    async (params: SearchParams, append: boolean) => {
      const id = ++seq.current;
      if (append) setLoadingMore(true);
      else setLoading(true);
      setError("");
      try {
        const resp = await api.search(params);
        if (id !== seq.current) return;
        setTotal(resp.total);
        setResults((prev) => (append ? [...prev, ...resp.results] : resp.results));
      } catch (e) {
        if (id !== seq.current) return;
        setError(e instanceof Error ? e.message.replace(/^\d{3}:\s*/, "") : String(e));
      } finally {
        if (id === seq.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [],
  );

  // Debounce the query input; run immediately for filter changes.
  useEffect(() => {
    if (qTimer.current) clearTimeout(qTimer.current);
    if (!filters.q) {
      setResults([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    qTimer.current = setTimeout(() => runSearch(filters, false), 250);
    return () => {
      if (qTimer.current) clearTimeout(qTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, runSearch]);

  const loadMore = () => {
    if (loading || loadingMore || results.length >= total) return;
    runSearch({ ...filters, offset: results.length }, true);
  };

  const accountOptions = useMemo(
    () => [
      { value: "", label: t("search.allAccounts") },
      ...accounts.map((a) => ({
        value: String(a.id),
        label: a.display_name || a.email,
      })),
    ],
    [accounts, t],
  );

  const categoryOptions = useMemo(
    () => [
      { value: "", label: t("cat.all") },
      ...categories.map((c) => ({ value: c.name, label: c.label })),
    ],
    [categories, t],
  );

  return (
    <div className="search-page">
      <aside className="search-filters">
        <div className="search-filter-group">
          <label className="search-filter-label">{t("search.filterAccount")}</label>
          <select
            className="search-filter-select"
            value={accountId ?? ""}
            onChange={(e) =>
              setAccountId(e.target.value ? Number(e.target.value) : undefined)
            }
          >
            {accountOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="search-filter-group">
          <label className="search-filter-label">{t("search.filterCategory")}</label>
          <select
            className="search-filter-select"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {categoryOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="search-filter-group">
          <label className="search-filter-label">{t("search.filterDateFrom")}</label>
          <input
            type="date"
            className="search-filter-input"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </div>

        <div className="search-filter-group">
          <label className="search-filter-label">{t("search.filterDateTo")}</label>
          <input
            type="date"
            className="search-filter-input"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </div>
      </aside>

      <div className="search-results">
        <div className="search-results-input-row">
          <input
            className="search-results-input"
            value={q}
            placeholder={t("search.placeholder")}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                if (qTimer.current) clearTimeout(qTimer.current);
                runSearch(filters, false);
              }
            }}
          />
        </div>

        {!loading && !error && total > 0 && (
          <p className="search-results-count">{t("search.results", { total })}</p>
        )}

        {loading ? (
          <div className="loading">{t("search.loading")}</div>
        ) : error ? (
          <div className="search-error">
            <span>
              {t("search.error")}
              {error}
            </span>
            <button
              className="btn small ghost"
              onClick={() => runSearch(filters, false)}
            >
              {t("search.retry")}
            </button>
          </div>
        ) : q.trim() && !error && total === 0 ? (
          <div className="search-empty">
            <p className="search-empty-icon">🔍</p>
            <p>{t("search.noResults")}</p>
          </div>
        ) : q.trim() ? (
          <>
            <ul className="search-result-list">
              {results.map((r) => (
                <li key={r.id}>
                  <button className="search-result-card" onClick={() => setSelectedEmailId(r.id)}>
                    <span className="search-result-subject">
                      {r.subject || t("misc.noSubject")}
                    </span>
                    <span className="search-result-meta">
                      {r.sender || "—"}
                      {r.received_at
                        ? ` · ${new Date(r.received_at).toLocaleString("zh-CN")}`
                        : ""}
                      {r.category ? ` · ${r.category}` : ""}
                    </span>
                    <span
                      className="search-result-snippet"
                      dangerouslySetInnerHTML={{ __html: r.snippet_html }}
                    />
                  </button>
                </li>
              ))}
            </ul>
            {results.length < total && (
              <div className="search-load-more">
                <button className="btn small ghost" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? t("search.loading") : t("search.loadMore")}
                </button>
              </div>
            )}
          </>
        ) : null}
      </div>

      {selectedEmailId !== null && (
        <EmailDetailPanel
          emailId={selectedEmailId}
          onClose={() => setSelectedEmailId(null)}
          onReadChange={() => {}}
        />
      )}
    </div>
  );
}
