import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { ChatMessage, CitedEmail } from "../types";

interface ChatEntry {
  role: "user" | "assistant";
  content: string;
  cited: CitedEmail[];
  source?: string;
}

interface ChatPanelProps {
  onOpenEmail: (id: number) => void;
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
    try {
      const res = await api.chat({ message, history });
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.answer,
          cited: res.cited_emails,
          source: res.source,
        },
      ]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.replace(/^\d{3}:\s*/, ""));
    } finally {
      setLoading(false);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    // History = the latest 5 user/assistant messages so far.
    const history: ChatMessage[] = messages
      .slice(-5)
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
      .slice(-5)
      .map((m) => ({ role: m.role, content: m.content }));
    await runChat(lastQuestion, history);
  };

  return (
    <div className="chat-panel">
      <div className="detail-section-title">{t("chat.title")}</div>

      <div className="chat-list" ref={listRef}>
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}`}>
            <div>{m.content}</div>
            {m.role === "assistant" && m.source && m.source !== "ai" && (
              <span className="chat-source-badge">
                {t(`chat.source.${m.source}`)}
              </span>
            )}
            {m.role === "assistant" && m.cited.length > 0 && (
              <div className="chat-cited">
                <span className="chat-cited-label">{t("chat.cited")}</span>
                {m.cited.map((c) => (
                  <button
                    key={c.id}
                    className="cited-card"
                    onClick={() => onOpenEmail(c.id)}
                  >
                    <span>{c.subject}</span>
                    <span>{c.sender}</span>
                    {c.date && (
                      <span>{new Date(c.date).toLocaleString("zh-CN")}</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        {loading && <div className="chat-loading">{t("chat.loading")}</div>}
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
