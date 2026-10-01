import { useCallback, useEffect, useRef, useState } from "react";
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
  const [active, setActive] = useState(-1);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const seq = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Deep-link support: ?q=... pre-fills and runs the search on load.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("q");
    if (q) setValue(q);
  }, []);

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
        setActive(-1);
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

  // Keep the keyboard-highlighted row visible while navigating.
  useEffect(() => {
    if (active < 0 || !listRef.current) return;
    listRef.current
      .querySelectorAll(".search-preview")
      [active]?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const goAll = useCallback(() => {
    const q = value.trim();
    if (!q) return;
    setOpen(false);
    inputRef.current?.blur();
    onOpenResults(q);
  }, [value, onOpenResults]);

  const openResult = useCallback(
    (index: number) => {
      if (!results || index < 0 || index >= results.length) return;
      setOpen(false);
      inputRef.current?.blur();
      onOpenResults(value.trim(), results[index].id);
    },
    [results, value, onOpenResults],
  );

  // Global shortcut: "/" or Ctrl/Cmd+K focuses the search box (Gmail-style),
  // unless the user is already typing in some other field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (typing) return;
      if (e.key === "/" || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k")) {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Close the dropdown on outside clicks.
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const onInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      if (open) {
        setOpen(false);
      } else {
        inputRef.current?.blur();
      }
      return;
    }
    const count = results?.length ?? 0;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open && count > 0) setOpen(true);
      setActive((prev) => (count === 0 ? -1 : (prev + 1) % count));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (count === 0) return;
      setActive((prev) => (prev <= 0 ? count - 1 : prev - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && count > 0) openResult(active);
      else goAll();
    }
  };

  const showDropdown = open && results !== null && value.trim().length > 0;

  return (
    <div
      className="search-bar"
      ref={boxRef}
      role="combobox"
      aria-expanded={showDropdown}
      aria-haspopup="listbox"
    >
      <span className="search-glyph" aria-hidden>
        ⌕
      </span>
      <input
        ref={inputRef}
        className="search-input"
        value={value}
        placeholder={t("search.placeholder")}
        onChange={(e) => {
          setValue(e.target.value);
          setActive(-1);
        }}
        onFocus={() => value.trim() && setOpen(true)}
        onKeyDown={onInputKeyDown}
        aria-label={t("search.placeholder")}
        aria-autocomplete="list"
        aria-controls="search-preview-list"
      />
      {value && (
        <button
          className="search-clear"
          aria-label="Clear"
          onClick={() => {
            setValue("");
            setActive(-1);
            inputRef.current?.focus();
          }}
        >
          ×
        </button>
      )}
      {!value && !loading && <span className="search-kbd-hint">{t("search.hint")}</span>}
      {loading && <span className="search-spinner" aria-hidden />}

      {showDropdown && (
        <div className="search-dropdown">
          {results!.length === 0 ? (
            <div className="search-dropdown-empty">{t("search.noResults")}</div>
          ) : (
            <ul
              className="search-dropdown-list"
              id="search-preview-list"
              role="listbox"
              ref={listRef}
            >
              {results!.map((r, i) => (
                <li key={r.id} role="option" aria-selected={i === active}>
                  <button
                    className={`search-preview ${i === active ? "active" : ""}`}
                    onClick={() => {
                      setOpen(false);
                      inputRef.current?.blur();
                      onOpenResults(value.trim(), r.id);
                    }}
                    onMouseEnter={() => setActive(i)}
                  >
                    <span className="search-preview-top">
                      <span className="search-preview-subject">
                        {r.subject || t("misc.noSubject")}
                      </span>
                      <span className="search-preview-date">
                        {r.received_at
                          ? new Date(r.received_at).toLocaleDateString("zh-CN")
                          : ""}
                      </span>
                    </span>
                    <span className="search-preview-meta">{r.sender || "—"}</span>
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
            <span className="search-dropdown-enter">Enter ↵</span>
          </button>
        </div>
      )}
    </div>
  );
}
