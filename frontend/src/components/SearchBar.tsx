import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { SearchResult } from "../types";

interface Props {
  /** Jump to the full results page (optionally straight into an email). */
  onOpenResults: (q: string, emailId?: number) => void;
}

const DEBOUNCE_MS = 300;
const PREVIEW_LIMIT = 6;

export function SearchBar({ onOpenResults }: Props) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const seq = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);

  // 300ms debounce, ignoring stale responses via a sequence counter.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const query = value.trim();
    if (!query) {
      setResults(null);
      setOpen(false);
      setLoading(false);
      return;
    }
    timer.current = setTimeout(async () => {
      const id = ++seq.current;
      setLoading(true);
      try {
        const resp = await api.search({ q: query, limit: PREVIEW_LIMIT });
        if (id !== seq.current) return; // superseded by a newer keystroke
        setResults(resp.results);
        setOpen(true);
      } catch {
        if (id !== seq.current) return;
        setResults([]);
        setOpen(true);
      } finally {
        if (id === seq.current) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value]);

  // Close the dropdown on outside clicks / Escape.
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const goAll = () => {
    const q = value.trim();
    if (!q) return;
    setOpen(false);
    onOpenResults(q);
  };

  return (
    <div className="search-bar" ref={boxRef}>
      <input
        className="search-input"
        value={value}
        placeholder={t("search.placeholder")}
        onChange={(e) => setValue(e.target.value)}
        onFocus={() => value.trim() && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") goAll();
        }}
      />
      {loading && <span className="search-spinner" aria-hidden />}

      {open && results !== null && value.trim() && (
        <div className="search-dropdown">
          {results.length === 0 ? (
            <div className="search-dropdown-empty">{t("search.noResults")}</div>
          ) : (
            <ul className="search-dropdown-list">
              {results.map((r) => (
                <li key={r.id}>
                  <button
                    className="search-preview"
                    onClick={() => {
                      setOpen(false);
                      onOpenResults(value.trim(), r.id);
                    }}
                  >
                    <span className="search-preview-subject">
                      {r.subject || t("misc.noSubject")}
                    </span>
                    <span className="search-preview-meta">
                      {r.sender || "—"}
                      {r.received_at
                        ? ` · ${new Date(r.received_at).toLocaleDateString("zh-CN")}`
                        : ""}
                    </span>
                    <span
                      className="search-preview-snippet"
                      dangerouslySetInnerHTML={{ __html: r.snippet_html }}
                    />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button className="search-dropdown-all" onClick={goAll}>
            {t("search.allResults")}
          </button>
        </div>
      )}
    </div>
  );
}
