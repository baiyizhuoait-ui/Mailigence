import { useState, useEffect, useCallback } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import {
  PLATFORM_LABEL,
  type EmailAccount,
  type IdleStatus,
  type SyncResult,
} from "../types";

interface Props {
  accounts: EmailAccount[];
  onSynced: () => void;
  onDeleted: () => void;
  onImport: (account: EmailAccount) => void;
  /** When true, auto-start IDLE for all accounts (set by parent on first load). */
  autoStartIdle?: boolean;
  /** One-time OAuth callback result banner shown above the list. */
  notice?: { ok: boolean; text: string } | null;
  onDismissNotice?: () => void;
}

export function AccountList({
  accounts,
  onSynced,
  onDeleted,
  onImport,
  autoStartIdle,
  notice,
  onDismissNotice,
}: Props) {
  const { t } = useI18n();
  const [busy, setBusy] = useState<number | null>(null);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [idleMap, setIdleMap] = useState<Record<number, IdleStatus>>({});
  const [idleBusy, setIdleBusy] = useState<number | null>(null);

  // Fetch IDLE status for all accounts.
  const refreshIdle = useCallback(async () => {
    try {
      const all = await api.getAllIdleStatus();
      const map: Record<number, IdleStatus> = {};
      for (const [k, v] of Object.entries(all)) {
        map[Number(k)] = v;
      }
      setIdleMap(map);
    } catch {
      /* ignore */
    }
  }, []);

  // Poll IDLE status every 15s.
  useEffect(() => {
    refreshIdle();
    const timer = setInterval(refreshIdle, 15_000);
    return () => clearInterval(timer);
  }, [refreshIdle]);

  // Auto-start IDLE for all accounts on first load.
  useEffect(() => {
    if (!autoStartIdle) return;
    for (const a of accounts) {
      api.startIdle(a.id).catch(() => {});
    }
    refreshIdle();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStartIdle]);

  async function toggleIdle(id: number) {
    setIdleBusy(id);
    try {
      const st = idleMap[id];
      if (st?.running) {
        await api.stopIdle(id);
      } else {
        await api.startIdle(id);
      }
      await refreshIdle();
    } catch {
      /* ignore */
    } finally {
      setIdleBusy(null);
    }
  }

  async function sync(id: number) {
    setBusy(id);
    setResult(null);
    try {
      const res = await api.syncAccount(id, 7);
      setResult(res);
      onSynced();
    } catch (e) {
      setResult({
        account_id: id,
        synced: 0,
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      });
      onSynced();
    } finally {
      setBusy(null);
    }
  }

  async function remove(id: number) {
    if (!confirm(t("misc.confirmDelete"))) return;
    await api.deleteAccount(id);
    onDeleted();
  }

  if (accounts.length === 0) {
    return (
      <div className="empty-state">
        {notice && (
          <div className={`sync-result ${notice.ok ? "" : "error"} oauth-notice`}>
            <span>{notice.text}</span>
            {onDismissNotice && (
              <button className="icon-btn" onClick={onDismissNotice} aria-label="close">
                ×
              </button>
            )}
          </div>
        )}
        <p className="empty-title">{t("misc.noAccounts")}</p>
        <p className="empty-sub">
          {t("misc.noAccountsSub")}
        </p>
      </div>
    );
  }

  return (
    <div className="account-list">
      {notice && (
        <div className={`sync-result ${notice.ok ? "" : "error"} oauth-notice`}>
          <span>{notice.text}</span>
          {onDismissNotice && (
            <button className="icon-btn" onClick={onDismissNotice} aria-label="close">
              ×
            </button>
          )}
        </div>
      )}
      {accounts.map((a) => {
        const idle = idleMap[a.id];
        const idleRunning = idle?.running ?? false;
        return (
          <div
            key={a.id}
            className="account-card"
            style={{ borderLeftColor: a.color || undefined, borderLeftStyle: "solid" }}
          >
            <div className="account-card-head">
              <span className={`platform-badge ${a.platform}`}>
                {PLATFORM_LABEL[a.platform] ?? a.platform}
              </span>
              <span className={`status-dot ${a.sync_status}`} title={a.sync_status} />
              {idleRunning && (
                <span className="idle-badge" title={t("idle.listening")}>
                  {t("idle.listening")}
                </span>
              )}
            </div>
            <div className="account-email mono">{a.email}</div>
            {a.display_name && <div className="account-name">{a.display_name}</div>}
            <div className="account-meta">
              {a.last_synced_at
                ? `${t("misc.lastSync")} ${new Date(a.last_synced_at).toLocaleString("zh-CN")}`
                : t("misc.notSynced")}
              {idle && idle.events > 0 && ` · ${t("idle.events")} ${idle.events} ${t("idle.times")}`}
            </div>
            {idle?.error &&
              (idle.error === "polling_only" ? (
                <div className="account-meta">· {t("idle.pollingOnly")}</div>
              ) : (
                <div className="account-error">⚠ IDLE: {idle.error}</div>
              ))}
            {a.last_error && <div className="account-error">⚠ {a.last_error}</div>}
            <div className="account-actions">
              <button
                className="btn small primary"
                disabled={busy === a.id || a.sync_status === "syncing"}
                onClick={() => sync(a.id)}
              >
                {busy === a.id ? t("action.syncing") : t("action.sync7")}
              </button>
              <button
                className="btn small ghost"
                onClick={() => onImport(a)}
                disabled={a.sync_status === "syncing"}
              >
                {a.sync_status === "syncing" ? t("action.importing") : t("action.importHistory")}
              </button>
              {idle?.error === "polling_only" ? (
                <button
                  className="btn small ghost"
                  disabled
                  title={t("idle.pollingOnly")}
                >
                  {t("idle.pollingSync")}
                </button>
              ) : (
                <button
                  className={`btn small ${idleRunning ? "ghost" : "outline"}`}
                  disabled={idleBusy === a.id}
                  onClick={() => toggleIdle(a.id)}
                  title={
                    idleRunning
                      ? t("idle.stopListeningTitle")
                      : t("idle.startListeningTitle")
                  }
                >
                  {idleBusy === a.id
                    ? "…"
                    : idleRunning
                      ? t("idle.stopListening")
                      : t("idle.listening")}
                </button>
              )}
              <button className="btn small ghost" onClick={() => remove(a.id)}>
                {t("action.delete")}
              </button>
            </div>
            {result && result.account_id === a.id && (
              <div className={`sync-result ${result.status}`}>
                {result.status === "error"
                  ? `${t("account.syncFailed")}${result.error}`
                  : `${t("account.imported")} ${result.synced} ${t("account.count")}`}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
