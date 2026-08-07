import { useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import { NETEASE_IMAP_MAP, PROVIDER_PRESETS, type EmailAccount } from "../types";

interface Props {
  onClose: () => void;
  onCreated: (account: EmailAccount) => void;
}

type Step = "form" | "testing" | "error";

export function AddAccountModal({ onClose, onCreated }: Props) {
  const { t } = useI18n();
  const [platform, setPlatform] = useState("gmail");
  const [emailLocal, setEmailLocal] = useState("");
  const [emailDomain, setEmailDomain] = useState("gmail.com");
  const [credential, setCredential] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [imapServer, setImapServer] = useState("");
  const [imapPort, setImapPort] = useState(993);
  // SMTP only matters from Stage 5 (sending); kept for the create payload.
  const [smtpServer] = useState("");
  const [smtpPort, setSmtpPort] = useState(465);
  const [step, setStep] = useState<Step>("form");
  const [error, setError] = useState("");

  const preset = PROVIDER_PRESETS.find((p) => p.platform === platform)!;
  const isCustom = platform === "imap";
  // For preset platforms the email is local@domain; for custom IMAP the user
  // types the full address into emailLocal.
  const email = isCustom ? emailLocal : `${emailLocal}@${emailDomain}`;

  const effectiveImap = isCustom
    ? imapServer
    : platform === "netease"
      ? NETEASE_IMAP_MAP[emailDomain] || ""
      : preset.imap_server;
  const effectiveSmtp = isCustom ? smtpServer : preset.smtp_server;

  function onPlatformChange(p: string) {
    setPlatform(p);
    const ps = PROVIDER_PRESETS.find((x) => x.platform === p)!;
    setImapPort(ps.imap_port);
    setSmtpPort(ps.smtp_port);
    if (ps.domains.length > 0) {
      // Coming from custom IMAP (emailLocal may contain a full address): keep
      // only the part before '@' so it becomes the local part for the new domain.
      const local = emailLocal.split("@")[0];
      setEmailLocal(local);
      setEmailDomain(ps.domains.includes(emailDomain) ? emailDomain : ps.domains[0]);
    } else {
      // Going to custom IMAP: stitch local@domain into a full address.
      if (emailDomain && emailLocal) {
        setEmailLocal(`${emailLocal}@${emailDomain}`);
      }
      setEmailDomain("");
    }
  }

  async function handleTest() {
    setError("");
    if (!email || !credential) {
      setError(t("modal.errorEmptyFields"));
      return;
    }
    if (isCustom && !imapServer) {
      setError(t("modal.errorCustomImap"));
      return;
    }
    setStep("testing");
    try {
      await api.testConnection({
        auth_type: "app_password",
        platform,
        email,
        credential,
        imap_server: effectiveImap,
        imap_port: imapPort,
        smtp_server: effectiveSmtp,
        smtp_port: smtpPort,
      });
      // Passed -> create
      const account = await api.createAccount({
        auth_type: "app_password",
        platform,
        email,
        credential,
        display_name: displayName,
        imap_server: effectiveImap,
        imap_port: imapPort,
        smtp_server: effectiveSmtp,
        smtp_port: smtpPort,
      });
      onCreated(account);
    } catch (e) {
      setStep("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleOAuth() {
    setError("");
    try {
      const provider = platform === "outlook" ? "microsoft" : "google";
      const { authorization_url } = await api.oauthStart(provider, platform);
      window.location.href = authorization_url;
    } catch (e) {
      setStep("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>{t("modal.title")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("misc.close")}>
            ✕
          </button>
        </header>

        <div className="modal-body">
          <label className="field">
            <span>{t("modal.platform")}</span>
            <select
              value={platform}
              onChange={(e) => onPlatformChange(e.target.value)}
            >
              {PROVIDER_PRESETS.map((p) => (
                <option key={p.platform} value={p.platform}>
                  {p.label}
                  {p.supports_oauth ? t("modal.oauthSupported") : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>{t("modal.email")}</span>
            {preset.domains.length > 0 ? (
              <div className="email-split">
                <input
                  type="text"
                  value={emailLocal}
                  onChange={(e) => setEmailLocal(e.target.value.replace(/@.*/, ""))}
                  placeholder={t("modal.account")}
                  autoCapitalize="none"
                  autoCorrect="off"
                />
                <span className="email-at">@</span>
                {preset.domains.length === 1 ? (
                  <span className="email-domain-fixed mono">{preset.domains[0]}</span>
                ) : (
                  <select
                    className="email-domain-select"
                    value={emailDomain}
                    onChange={(e) => setEmailDomain(e.target.value)}
                  >
                    {preset.domains.map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                )}
              </div>
            ) : (
              <input
                type="email"
                value={emailLocal}
                onChange={(e) => setEmailLocal(e.target.value)}
                placeholder="you@example.com"
                autoCapitalize="none"
                autoCorrect="off"
              />
            )}
          </label>

          <label className="field">
            <span>{t("modal.appPassword")}</span>
            <input
              type="password"
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
              placeholder={t("modal.appPasswordHint")}
            />
            <small className="hint">
              {t("modal.appPasswordHelp")}
            </small>
          </label>

          <label className="field">
            <span>{t("modal.displayName")}</span>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t("modal.displayNamePlaceholder")}
            />
          </label>

          {isCustom && (
            <div className="field-row">
              <label className="field">
                <span>{t("modal.imapServer")}</span>
                <input value={imapServer} onChange={(e) => setImapServer(e.target.value)} />
              </label>
              <label className="field narrow">
                <span>{t("modal.port")}</span>
                <input
                  type="number"
                  value={imapPort}
                  onChange={(e) => setImapPort(Number(e.target.value))}
                />
              </label>
            </div>
          )}

          {!isCustom && effectiveImap && (
            <p className="preset-line">
              <span className="mono">{effectiveImap}:{imapPort}</span>
              <span className="dim">{t("modal.imapPreset")}</span>
            </p>
          )}

          {preset.supports_oauth && (
            <button className="btn ghost full" onClick={handleOAuth} disabled={step === "testing"}>
              {t("modal.useOAuth")}
            </button>
          )}

          {step === "error" && error && (
            <div className="alert error">{error}</div>
          )}
        </div>

        <footer className="modal-foot">
          <button className="btn ghost" onClick={onClose}>
            {t("modal.cancel")}
          </button>
          <button className="btn primary" onClick={handleTest} disabled={step === "testing"}>
            {step === "testing" ? t("modal.saving") : t("modal.save")}
          </button>
        </footer>
      </div>
    </div>
  );
}
