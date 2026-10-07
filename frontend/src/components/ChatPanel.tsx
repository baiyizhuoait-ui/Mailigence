import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { ChatMessage, CitedEmail } from "../types";

interface ChatEntry {
  role: "user" | "assistant";
  content: string;
  cited: CitedEmail[];
  source?: string;
  tier?: "local" | "cloud";
}

interface ChatPanelProps {
  onOpenEmail: (id: number) => void;
}

// [ID:12] markers emitted by the backend answer become clickable chips.
const ID_TOKEN_RE = /\[ID:(\d+)\]/g;

function fmtDate(date: string | null): string {
  if (!date) return "";
  return new Date(date).toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// One cited email as a rich, obviously-clickable card: sender + unread dot +
// date on top, bold subject, one-line preview, then category/action chips.
function CitedCard({
  cited,
  openLabel,
  onOpen,
}: {
  cited: CitedEmail;
  openLabel: string;
  onOpen: (id: number) => void;
}) {
  const { t } = useI18n();
  const catKey = `cat.${cited.category}`;
  const catLabel = cited.category ? t(catKey) : "";
  const actionKey = cited.action ? `chat.action.${cited.action}` : "";
  const actionLabel =
    cited.action && cited.action !== "none" ? t(actionKey) : "";
  return (
    <button
      className="cited-card"
      title={openLabel}
      onClick={() => onOpen(cited.id)}
    >
      <div className="cited-top">
        <span className="cited-sender">
          {cited.sender || cited.sender_email || "—"}
        </span>
        {cited.unread && <span className="cited-unread" />}
        <span className="cited-date">{fmtDate(cited.date)}</span>
      </div>
      <div className="cited-subject">{cited.subject || t("chat.noSubject")}</div>
      {cited.preview && <div className="cited-preview">{cited.preview}</div>}
      <div className="cited-meta">
        {cited.category && (
          <span className="cited-chip">
            {catLabel === catKey ? cited.category : catLabel}
          </span>
        )}
        {actionLabel && (
          <span className={`cited-chip act-${cited.action}`}>
            {actionLabel === actionKey ? cited.action : actionLabel}
          </span>
        )}
        <span className="cited-id">ID:{cited.id}</span>
        <span className="cited-open">↗ {openLabel}</span>
      </div>
    </button>
  );
}

export function ChatPanel({ onOpenEmail }: ChatPanelProps) {
  const { t } = useI18n();
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastQuestion, setLastQuestion] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  // Keep the newest message in view.
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, loading]);

  const runChat = async (message: string, history: ChatMessage[]) => {
    setLoading(true);
    setError(null);
    // Placeholder assistant bubble that fills up as deltas stream in.
    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: "", cited: [] },
    ]);
    const patchLast = (patch: (m: ChatEntry) => ChatEntry) =>
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.role !== "assistant") return prev;
        return [...prev.slice(0, -1), patch(last)];
      });
    try {
      await api.chatStream(
        { message, history },
        {
          onDelta: (text) => patchLast((m) => ({ ...m, content: m.content + text })),
          onDone: (final) =>
            patchLast((m) => ({
              ...m,
              content: final.answer,
              cited: final.cited_emails,
              source: final.source,
              tier: final.tier,
            })),
        },
      );
    } catch (e) {
      // Drop the placeholder bubble if nothing streamed into it yet.
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        const empty =
          last && last.role === "assistant" && !last.content && !last.cited.length;
        return empty ? prev.slice(0, -1) : prev;
      });
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.replace(/^\d{3}:\s*/, ""));
    } finally {
      setLoading(false);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    // Last 10 messages (5 rounds); the backend trims further per model budget.
    const history: ChatMessage[] = messages
      .slice(-10)
      .map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, { role: "user", content: text, cited: [] }]);
    setInput("");
    setLastQuestion(text);
    await runChat(text, history);
  };

  const retry = async () => {
    if (!lastQuestion || loading) return;
    // The failed question is already the last message; exclude it from history
    // since it is re-sent as `message`.
    const last = messages[messages.length - 1];
    const base =
      last && last.role === "user" && last.content === lastQuestion
        ? messages.slice(0, -1)
        : messages;
    const history: ChatMessage[] = base
      .slice(-10)
      .map((m) => ({ role: m.role, content: m.content }));
    await runChat(lastQuestion, history);
  };

  // Render answer text, turning every [ID:xx] into a clickable inline chip.
  const renderContent = (content: string, citedById: Map<number, CitedEmail>) => {
    const parts = content.split(ID_TOKEN_RE);
    return parts.map((part, i) => {
      // Odd indices are the captured ID digits from the split.
      if (i % 2 === 1) {
        const id = Number(part);
        const cited = citedById.get(id);
        return (
          <button
            key={i}
            className="chat-id-chip"
            title={cited ? `${cited.sender} · ${cited.subject}` : undefined}
            onClick={() => onOpenEmail(id)}
          >
            #{id}
          </button>
        );
      }
      return <span key={i}>{part}</span>;
    });
  };

  const lastMsg = messages[messages.length - 1];
  const waitingFirst =
    loading && lastMsg?.role === "assistant" && lastMsg.content === "";

  return (
    <div className="chat-panel">
      <div className="detail-section-title">{t("chat.title")}</div>

      <div className="chat-list" ref={listRef}>
        {messages.map((m, i) => {
          const citedById = new Map(m.cited.map((c) => [c.id, c]));
          return (
            <div key={i} className={`chat-msg ${m.role}`}>
              <div>
                {m.role === "assistant"
                  ? renderContent(m.content, citedById)
                  : m.content}
              </div>
              {m.role === "assistant" && m.source && m.source !== "ai" && (
                <span className="chat-source-badge">
                  {t(`chat.source.${m.source}`)}
                </span>
              )}
              {m.role === "assistant" && m.tier && (
                <span className={`chat-tier-badge tier-${m.tier}`}>
                  {t(`chat.tier.${m.tier}`)}
                </span>
              )}
              {m.role === "assistant" && m.cited.length > 0 && (
                <div className="chat-cited">
                  <span className="chat-cited-label">{t("chat.cited")}</span>
                  {m.cited.map((c) => (
                    <CitedCard
                      key={c.id}
                      cited={c}
                      openLabel={t("chat.openEmail")}
                      onOpen={onOpenEmail}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {waitingFirst && <div className="chat-loading">{t("chat.loading")}</div>}
      </div>

      {error && (
        <div className="chat-error">
          <span>
            {t("chat.failed")}
            {error}
          </span>
          <button className="btn small primary-soft" onClick={retry}>
            {t("chat.retry")}
          </button>
        </div>
      )}

      <div className="chat-input-row">
        <input
          className="chat-input"
          value={input}
          placeholder={t("chat.placeholder")}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <button
          className="btn small primary-soft chat-send"
          onClick={send}
          disabled={loading}
        >
          {t("chat.send")}
        </button>
      </div>
    </div>
  );
}
