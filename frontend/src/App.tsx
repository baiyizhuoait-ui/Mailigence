import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AddAccountModal } from "./components/AddAccountModal";
import { AccountList } from "./components/AccountList";
import { AdManagementView } from "./components/AdManagementView";
import { DashboardView } from "./components/DashboardView";
import { ImportDialog } from "./components/ImportDialog";
import { MailboxView } from "./components/MailboxView";
import { ReplyTrackingView } from "./components/ReplyTrackingView";
import { ReportView } from "./components/ReportView";
import { SettingsView } from "./components/SettingsView";
import { useI18n } from "./i18n";
import { PLATFORM_LABEL, type EmailAccount } from "./types";

type View = "dashboard" | "mailbox" | "accounts" | "ads" | "reports" | "replies" | "settings";

export default function App() {
  const { t } = useI18n();
  const [view, setView] = useState<View>("dashboard");
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [importTarget, setImportTarget] = useState<EmailAccount | null>(null);
  const [health, setHealth] = useState<{
    encryption_configured: boolean;
    oauth_google_configured: boolean;
    oauth_microsoft_configured: boolean;
  } | null>(null);
  const [loadingAccounts, setLoadingAccounts] = useState(true);
  /** One-time OAuth callback result banner (success / error / duplicate). */
  const [oauthNotice, setOauthNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const refreshAccounts = useCallback(async () => {
    setLoadingAccounts(true);
    try {
      setAccounts(await api.listAccounts());
    } catch {
      /* surfaced in mailbox error state */
    } finally {
      setLoadingAccounts(false);
    }
  }, []);

  useEffect(() => {
    refreshAccounts();
    api.health().then((h) =>
      setHealth({
        encryption_configured: h.encryption_configured,
        oauth_google_configured: h.oauth_google_configured,
        oauth_microsoft_configured: h.oauth_microsoft_configured,
      }),
    );
  }, [refreshAccounts]);

  // Surface the OAuth callback result (success / duplicate / error) that the
  // backend redirects back with, then clean the URL so a refresh doesn't
  // re-show it.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.get("added") && !params.get("oauth_error") && !params.get("oauth_status")) {
      return;
    }
    let notice: { ok: boolean; text: string } | null = null;
    if (params.get("added")) {
      notice = { ok: true, text: t("oauth.added") };
    } else if (params.get("oauth_status") === "exists") {
      notice = { ok: true, text: t("oauth.exists") };
    } else if (params.get("oauth_status") === "stale") {
      notice = { ok: true, text: t("oauth.stale") };
    } else {
      const code = params.get("oauth_error") ?? "unknown";
      const key = `oauth.err.${code}`;
      const mapped = t(key, { code });
      const text = mapped === key ? t("oauth.err.generic", { code }) : mapped;
      const detail = params.get("oauth_detail");
      notice = { ok: false, text: detail ? `${text}：${detail}` : text };
    }
    setOauthNotice(notice);
    setView("accounts");
    window.history.replaceState(null, "", window.location.pathname);
  }, [t]);

  const accountCount = accounts.length;

  const navItems: { key: View; label: string; count?: number }[] = [
    { key: "dashboard", label: t("nav.dashboard") },
    { key: "mailbox", label: t("nav.mailbox"), count: accountCount },
    { key: "accounts", label: t("nav.accounts"), count: accountCount },
    { key: "ads", label: t("nav.ads") },
    { key: "reports", label: t("nav.reports") },
    { key: "replies", label: t("nav.replies") },
  ];

  const titles: Record<View, string> = {
    dashboard: t("title.dashboard"),
    mailbox: t("title.mailbox"),
    accounts: t("title.accounts"),
    ads: t("title.ads"),
    reports: t("title.reports"),
    replies: t("title.replies"),
    settings: t("title.settings"),
  };

  const subs: Record<View, string> = {
    dashboard: t("sub.dashboard"),
    mailbox: t("sub.mailbox"),
    accounts: t("sub.accounts"),
    ads: t("sub.ads"),
    reports: t("sub.reports"),
    replies: t("sub.replies"),
    settings: t("sub.settings"),
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">✉</span>
          <div>
            <div className="brand-name">Mailigence</div>
            <div className="brand-sub">{t("brand.sub")}</div>
          </div>
        </div>

        <nav className="nav">
          {navItems.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${view === item.key ? "active" : ""}`}
              onClick={() => setView(item.key)}
            >
              <span className="nav-label">{item.label}</span>
              {item.count !== undefined && (
                <span className="nav-count mono">{item.count}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          {health && !health.encryption_configured && (
            <div className="warn-card">
              {t("misc.encryptionWarn")}
            </div>
          )}
          <button
            className={`nav-item ${view === "settings" ? "active" : ""}`}
            onClick={() => setView("settings")}
          >
            <span className="nav-label">{t("nav.settings")}</span>
          </button>
        </div>
      </aside>

      <main className="content">
        <header className="topbar">
          <div>
            <h1 className="page-title">{titles[view]}</h1>
            <p className="page-sub">{subs[view]}</p>
          </div>
          {view !== "dashboard" && view !== "ads" && view !== "reports" && view !== "replies" && view !== "settings" && (
            <button className="btn primary" onClick={() => setShowAdd(true)}>
              {t("action.addAccount")}
            </button>
          )}
        </header>

        {view === "dashboard" ? (
          loadingAccounts ? (
            <div className="loading">{t("misc.loading")}</div>
          ) : (
            <DashboardView />
          )
        ) : view === "accounts" ? (
          loadingAccounts ? (
            <div className="loading">{t("misc.loading")}</div>
          ) : (
            <AccountList
              accounts={accounts}
              onSynced={refreshAccounts}
              onDeleted={refreshAccounts}
              onImport={(a) => setImportTarget(a)}
              autoStartIdle
              notice={oauthNotice}
              onDismissNotice={() => setOauthNotice(null)}
            />
          )
        ) : view === "ads" ? (
          <AdManagementView />
        ) : view === "reports" ? (
          <ReportView accounts={accounts} />
        ) : view === "replies" ? (
          <ReplyTrackingView accounts={accounts} />
        ) : view === "settings" ? (
          <SettingsView />
        ) : loadingAccounts ? (
          <div className="loading">{t("misc.loading")}</div>
        ) : (
          <MailboxView accounts={accounts} onRefreshAccounts={refreshAccounts} />
        )}
      </main>

      {showAdd && (
        <AddAccountModal
          onClose={() => setShowAdd(false)}
          oauthConfig={{
            google: health?.oauth_google_configured ?? false,
            microsoft: health?.oauth_microsoft_configured ?? false,
          }}
          onCreated={async (account) => {
            setShowAdd(false);
            await refreshAccounts();
            // First-connection flow: prompt historical import right away.
            setView("accounts");
            setImportTarget(account);
          }}
        />
      )}

      {importTarget && (
        <ImportDialog
          accountId={importTarget.id}
          accountLabel={
            (PLATFORM_LABEL[importTarget.platform] ?? importTarget.platform) +
            " · " +
            importTarget.email
          }
          onClose={() => setImportTarget(null)}
          onCompleted={refreshAccounts}
        />
      )}
    </div>
  );
}
