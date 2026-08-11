import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  ACCENT_PRESETS,
  useI18n,
  type AccentColor,
  type Lang,
  type ThemeMode,
} from "../i18n";
import type {
  AiMemory,
  AiProvider,
  AiSettings,
  AnalysisMode,
  EmailAccount,
  EmailCategory,
} from "../types";

// Preset swatches for category / account colors.
const COLOR_PRESETS = [
  "#4a9eff", "#14b8a6", "#5cb874", "#e8b04b", "#f97316",
  "#e0725f", "#ec4899", "#a073d4", "#6366f1", "#06b6d4", "#64748b", "#9ca3af",
];

interface AiPreset {
  id: string;
  provider: AiProvider;
  baseUrl: string;
  model: string;
}

const AI_PRESETS: AiPreset[] = [
  { id: "env", provider: "", baseUrl: "", model: "" },
  { id: "openai", provider: "openai", baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { id: "deepseek", provider: "openai", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { id: "moonshot", provider: "openai", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { id: "qwen", provider: "openai", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { id: "glm", provider: "openai", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
  { id: "ollama", provider: "openai", baseUrl: "http://localhost:11434/v1", model: "qwen2.5:7b" },
  { id: "anthropic", provider: "anthropic", baseUrl: "https://api.anthropic.com/v1", model: "claude-sonnet-4-20250514" },
  { id: "custom", provider: "openai", baseUrl: "", model: "" },
];

const MODE_KEYS: { mode: AnalysisMode; labelKey: string; descKey: string }[] = [
  { mode: "auto", labelKey: "settings.ai.mode.auto", descKey: "settings.ai.mode.autoDesc" },
  { mode: "ai_only", labelKey: "settings.ai.mode.ai_only", descKey: "settings.ai.mode.ai_onlyDesc" },
  { mode: "rules_only", labelKey: "settings.ai.mode.rules_only", descKey: "settings.ai.mode.rules_onlyDesc" },
];

export function SettingsView() {
  const { t, lang, theme, accent, customAccent, setLang, setTheme, setAccent, setCustomAccent } =
    useI18n();

  // ---- AI settings state ----
  const [mode, setMode] = useState<AnalysisMode>("auto");
  const [provider, setProvider] = useState<AiProvider>("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [presetId, setPresetId] = useState("env");
  const [status, setStatus] = useState<AiSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // ---- category management state ----
  const [categories, setCategories] = useState<EmailCategory[]>([]);
  const [catName, setCatName] = useState("");
  const [catLabel, setCatLabel] = useState("");
  const [catBusy, setCatBusy] = useState(false);
  const [catMsg, setCatMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [editingLabel, setEditingLabel] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");

  // ---- account color state ----
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountMsg, setAccountMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // ---- AI memory state ----
  const [memories, setMemories] = useState<AiMemory[]>([]);
  const [memDraft, setMemDraft] = useState("");
  const [memBusy, setMemBusy] = useState(false);
  const [memMsg, setMemMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const loadCategories = useCallback(async () => {
    try {
      setCategories(await api.listCategories());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  const loadSettings = useCallback(async () => {
    try {
      const s = await api.getAiSettings();
      setStatus(s);
      setMode(s.analysis_mode);
      setProvider(s.provider);
      setBaseUrl(s.base_url);
      setModel(s.model);
      // Match the current base URL to a preset, else custom.
      const match = AI_PRESETS.find((p) => p.baseUrl === s.base_url && p.provider === s.provider);
      setPresetId(match ? match.id : "custom");
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  // Load accounts for the color picker.
  const loadAccounts = useCallback(async () => {
    try {
      setAccounts(await api.listAccounts());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  // Load AI memory entries.
  const loadMemories = useCallback(async () => {
    try {
      setMemories(await api.listMemories());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadMemories();
  }, [loadMemories]);

  // ---- color handlers ----
  async function changeCategoryColor(id: number, color: string) {
    setCatBusy(true);
    setCatMsg(null);
    try {
      await api.updateCategory(id, { color });
      await loadCategories();
    } catch (e) {
      setCatMsg({
        ok: false,
        text: `${t("catmgmt.colorFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setCatBusy(false);
    }
  }

  async function changeAccountColor(id: number, color: string | null) {
    setAccountBusy(true);
    setAccountMsg(null);
    try {
      await api.updateAccount(id, { color });
      await loadAccounts();
    } catch (e) {
      setAccountMsg({
        ok: false,
        text: `${t("account.colorFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setAccountBusy(false);
    }
  }

  // ---- AI memory handlers ----
  async function sendMemory() {
    const text = memDraft.trim();
    if (!text || memBusy) return;
    setMemBusy(true);
    setMemMsg(null);
    try {
      const created = await api.createMemory(text);
      setMemDraft("");
      await loadMemories();
      setMemMsg({
        ok: true,
        text: `${t("mem.saved")}${created.length > 0 ? `（+${created.length}）` : ""}`,
      });
    } catch (e) {
      setMemMsg({
        ok: false,
        text: `${t("mem.failed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setMemBusy(false);
    }
  }

  async function removeMemory(id: number) {
    try {
      await api.deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch {
      /* ignore */
    }
  }

  function applyPreset(id: string) {
    const preset = AI_PRESETS.find((p) => p.id === id);
    if (!preset) return;
    setPresetId(id);
    setProvider(preset.provider);
    setBaseUrl(preset.baseUrl);
    setModel(preset.model);
    setApiKey("");
    setClearKey(false);
    setSaveMsg(null);
  }

  async function saveSettings() {
    setSaving(true);
    setSaveMsg(null);
    try {
      const s = await api.updateAiSettings({
        analysis_mode: mode,
        provider,
        base_url: baseUrl,
        model,
        api_key: apiKey,
        clear_api_key: clearKey,
      });
      setStatus(s);
      setApiKey("");
      setClearKey(false);
      setSaveMsg({ ok: true, text: t("settings.ai.saved") });
    } catch (e) {
      setSaveMsg({
        ok: false,
        text: `${t("settings.ai.saveFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setSaving(false);
    }
  }

  const active = status?.api_key_configured && status.analysis_mode !== "rules_only";

  // ---- category handlers ----
  async function addCategory() {
    const name = catName.trim();
    if (!name || catBusy) return;
    setCatBusy(true);
    setCatMsg(null);
    try {
      await api.createCategory({
        name,
        label: catLabel.trim() || name,
      });
      setCatName("");
      setCatLabel("");
      await loadCategories();
      setCatMsg({ ok: true, text: t("catmgmt.added") });
    } catch (e) {
      setCatMsg({
        ok: false,
        text: `${t("catmgmt.addFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setCatBusy(false);
    }
  }

  async function renameCategory(id: number) {
    const label = editValue.trim();
    if (!label || catBusy) return;
    setCatBusy(true);
    setCatMsg(null);
    try {
      await api.updateCategory(id, { label });
      setEditingLabel(null);
      await loadCategories();
    } catch (e) {
      setCatMsg({
        ok: false,
        text: `${t("catmgmt.renameFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setCatBusy(false);
    }
  }

  async function deleteCategory(id: number, label: string) {
    if (!window.confirm(t("catmgmt.confirmDelete") + `「${label}」？`)) return;
    setCatBusy(true);
    setCatMsg(null);
    try {
      await api.deleteCategory(id);
      await loadCategories();
      setCatMsg({ ok: true, text: t("catmgmt.deleted") });
    } catch (e) {
      setCatMsg({
        ok: false,
        text: `${t("catmgmt.deleteFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setCatBusy(false);
    }
  }

  return (
    <div className="settings-view">
      {/* AI analysis */}
      <section className="settings-section">
        <h3 className="settings-section-title">{t("settings.ai")}</h3>

        {/* Mode */}
        <div className="settings-row">
          <span className="settings-label">{t("settings.ai.mode")}</span>
          <div className="ai-mode-grid">
            {MODE_KEYS.map(({ mode: m, labelKey, descKey }) => (
              <button
                key={m}
                className={`ai-mode-card ${mode === m ? "active" : ""}`}
                onClick={() => setMode(m)}
              >
                <span className="ai-mode-name">{t(labelKey)}</span>
                <span className="ai-mode-desc">{t(descKey)}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Status */}
        <div className="settings-row">
          <span className="settings-label">{t("settings.ai.status")}</span>
          <div className="ai-status">
            <span className={`status-dot ${active ? "idle" : "error"}`} />
            <span className="ai-status-text">
              {active ? t("settings.ai.active") : t("settings.ai.inactive")}
            </span>
            {status && (
              <span className="ai-status-meta">
                {t("settings.ai.activeMode")}: {t(`settings.ai.mode.${status.analysis_mode}`)}
                {" · "}
                {status.api_key_configured
                  ? status.api_key_from_db
                    ? t("settings.ai.keyFromDb")
                    : t("settings.ai.keyFromEnv")
                  : t("settings.ai.keyNotSet")}
              </span>
            )}
          </div>
        </div>

        {/* Provider preset */}
        <div className="settings-row">
          <span className="settings-label">{t("settings.ai.provider")}</span>
          <div className="settings-field-group">
            <select
              className="settings-select"
              value={presetId}
              onChange={(e) => applyPreset(e.target.value)}
            >
              {AI_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {t(`settings.ai.provider.${p.id}`)}
                </option>
              ))}
            </select>
            <span className="settings-hint">{t("settings.ai.providerHint")}</span>
          </div>
        </div>

        {/* Connection form */}
        <div className="settings-row">
          <span className="settings-label">{t("settings.ai.baseUrl")}</span>
          <input
            className="settings-input mono"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            disabled={presetId === "env"}
          />
        </div>
        <div className="settings-row">
          <span className="settings-label">{t("settings.ai.model")}</span>
          <input
            className="settings-input mono"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gpt-4o-mini"
            disabled={presetId === "env"}
          />
        </div>
        <div className="settings-row">
          <span className="settings-label">{t("settings.ai.apiKey")}</span>
          <div className="settings-field-group">
            <input
              className="settings-input mono"
              type="password"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value);
                setClearKey(false);
              }}
              placeholder={status?.api_key_configured ? "••••••••" : ""}
              disabled={presetId === "env"}
            />
            <span className="settings-hint">{t("settings.ai.apiKeyHint")}</span>
            <label className="toggle">
              <input
                type="checkbox"
                checked={clearKey}
                onChange={(e) => {
                  setClearKey(e.target.checked);
                  if (e.target.checked) setApiKey("");
                }}
              />
              {t("settings.ai.clearKey")}
            </label>
          </div>
        </div>

        {/* Save */}
        <div className="settings-row">
          <span className="settings-label" />
          <div className="settings-field-group">
            <button className="btn primary" onClick={saveSettings} disabled={saving}>
              {saving ? "…" : t("settings.ai.save")}
            </button>
            {saveMsg && (
              <span className={`sync-result ${saveMsg.ok ? "" : "error"}`}>{saveMsg.text}</span>
            )}
          </div>
        </div>

        {/* AI Memory — hidden in pure-rule mode (needs an LLM) */}
        {mode !== "rules_only" && (
          <div className="mem-block">
            <span className="mem-title">{t("mem.title")}</span>
            <textarea
              className="settings-input mem-input"
              rows={3}
              value={memDraft}
              onChange={(e) => setMemDraft(e.target.value)}
              placeholder={t("mem.placeholder")}
            />
            <div className="mem-actions">
              <button
                className="btn small primary"
                onClick={sendMemory}
                disabled={memBusy || !memDraft.trim()}
              >
                {memBusy ? t("mem.distilling") : t("mem.send")}
              </button>
              {memMsg && (
                <span className={`sync-result ${memMsg.ok ? "" : "error"}`}>{memMsg.text}</span>
              )}
            </div>
            {memories.length > 0 && (
              <ul className="mem-list">
                {memories.map((m) => (
                  <li key={m.id} className="mem-item">
                    <span className="mem-item-text">{m.content}</span>
                    <button
                      className="btn small ghost"
                      onClick={() => removeMemory(m.id)}
                      title={t("action.delete")}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {/* Appearance */}
      <section className="settings-section">
        <h3 className="settings-section-title">{t("settings.appearance")}</h3>

        {/* Theme mode */}
        <div className="settings-row">
          <span className="settings-label">{t("settings.theme")}</span>
          <div className="seg-control">
            <button
              className={`seg-btn ${theme === "dark" ? "active" : ""}`}
              onClick={() => setTheme("dark" as ThemeMode)}
            >
              {t("settings.theme.dark")}
            </button>
            <button
              className={`seg-btn ${theme === "light" ? "active" : ""}`}
              onClick={() => setTheme("light" as ThemeMode)}
            >
              {t("settings.theme.light")}
            </button>
          </div>
        </div>

        {/* Accent color */}
        <div className="settings-row">
          <span className="settings-label">{t("settings.accent")}</span>
          <div className="accent-swatches">
            {ACCENT_PRESETS.map((p) =>
              p.id === "custom" ? (
                <label
                  key={p.id}
                  className={`accent-swatch accent-custom ${accent === "custom" ? "active" : ""}`}
                  style={{ background: accent === "custom" && customAccent ? customAccent : p.color }}
                  title={lang === "zh" ? p.labelZh : p.labelEn}
                >
                  <input
                    type="color"
                    className="accent-color-input"
                    value={accent === "custom" && customAccent ? customAccent : p.color}
                    onChange={(e) => setCustomAccent(e.target.value)}
                  />
                  {accent === "custom" && <span className="swatch-check">✓</span>}
                </label>
              ) : (
                <button
                  key={p.id}
                  className={`accent-swatch ${accent === p.id ? "active" : ""}`}
                  style={{ background: p.color }}
                  onClick={() => setAccent(p.id as AccentColor)}
                  title={lang === "zh" ? p.labelZh : p.labelEn}
                  aria-label={lang === "zh" ? p.labelZh : p.labelEn}
                >
                  {accent === p.id && <span className="swatch-check">✓</span>}
                </button>
              ),
            )}
          </div>
        </div>
      </section>

      {/* Language */}
      <section className="settings-section">
        <h3 className="settings-section-title">{t("settings.language")}</h3>
        <div className="settings-row">
          <span className="settings-label">{t("settings.language")}</span>
          <div className="seg-control">
            <button
              className={`seg-btn ${lang === "zh" ? "active" : ""}`}
              onClick={() => setLang("zh" as Lang)}
            >
              {t("settings.language.zh")}
            </button>
            <button
              className={`seg-btn ${lang === "en" ? "active" : ""}`}
              onClick={() => setLang("en" as Lang)}
            >
              {t("settings.language.en")}
            </button>
          </div>
        </div>
      </section>

      {/* Account colors */}
      <section className="settings-section">
        <h3 className="settings-section-title">{t("account.colorTitle")}</h3>
        {accounts.length === 0 ? (
          <span className="settings-hint">{t("misc.noAccounts")}</span>
        ) : (
          <div className="settings-row">
            <span className="settings-label">{t("account.colorTitle")}</span>
            <ul className="account-color-list">
              {accounts.map((a) => (
                <li key={a.id} className="account-color-item">
                  <span className="mono account-color-email">{a.email}</span>
                  <span className="color-swatches">
                    {COLOR_PRESETS.map((c) => (
                      <button
                        key={c}
                        className={`color-swatch ${a.color === c ? "active" : ""}`}
                        style={{ background: c }}
                        onClick={() => changeAccountColor(a.id, a.color === c ? null : c)}
                        disabled={accountBusy}
                        title={c}
                      />
                    ))}
                    <label
                      className="color-swatch color-swatch-custom"
                      style={{ background: a.color ?? "#9ca3af" }}
                      title={t("mem.customColor")}
                    >
                      <input
                        type="color"
                        className="accent-color-input"
                        value={a.color ?? "#9ca3af"}
                        onChange={(e) => changeAccountColor(a.id, e.target.value)}
                        disabled={accountBusy}
                      />
                    </label>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {accountMsg && (
          <div className="settings-row">
            <span className="settings-label" />
            <span className={`sync-result ${accountMsg.ok ? "" : "error"}`}>{accountMsg.text}</span>
          </div>
        )}
      </section>

      {/* Category management */}
      <section className="settings-section">
        <h3 className="settings-section-title">{t("catmgmt.title")}</h3>
        <div className="settings-row">
          <span className="settings-label" />
          <span className="settings-hint">{t("catmgmt.hint")}</span>
        </div>

        <div className="settings-row">
          <span className="settings-label">{t("catmgmt.new")}</span>
          <div className="settings-field-group cat-add-group">
            <input
              className="settings-input"
              placeholder={t("catmgmt.namePlaceholder")}
              value={catName}
              onChange={(e) => setCatName(e.target.value)}
            />
            <input
              className="settings-input"
              placeholder={t("catmgmt.labelPlaceholder")}
              value={catLabel}
              onChange={(e) => setCatLabel(e.target.value)}
            />
            <button className="btn primary" onClick={addCategory} disabled={catBusy || !catName.trim()}>
              {t("catmgmt.add")}
            </button>
          </div>
        </div>

        <div className="settings-row">
          <span className="settings-label">{t("catmgmt.list")}</span>
          <ul className="cat-list">
            {categories.map((c) => (
              <li key={c.id} className="cat-item">
                <label className="cat-color-wrap" title={t("catmgmt.colorHint")}>
                  <span
                    className="cat-swatch"
                    style={{ background: c.color ?? "#9ca3af" }}
                  />
                  <input
                    type="color"
                    className="cat-color-input"
                    value={c.color ?? "#9ca3af"}
                    onChange={(e) => changeCategoryColor(c.id, e.target.value)}
                    disabled={catBusy}
                  />
                </label>
                {editingLabel === c.id ? (
                  <input
                    className="settings-input cat-rename-input"
                    value={editValue}
                    autoFocus
                    onChange={(e) => setEditValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") renameCategory(c.id);
                      if (e.key === "Escape") setEditingLabel(null);
                    }}
                    onBlur={() => renameCategory(c.id)}
                  />
                ) : (
                  <span className="cat-name">
                    {c.label}
                    <span className="cat-name-key mono">({c.name})</span>
                    {c.is_system && (
                      <span className="cat-badge">{t("catmgmt.system")}</span>
                    )}
                  </span>
                )}
                <span className="cat-count mono">
                  {c.email_count} {t("account.count")}
                </span>
                <button
                  className="btn small ghost"
                  onClick={() => {
                    setEditingLabel(c.id);
                    setEditValue(c.label);
                  }}
                  disabled={catBusy}
                >
                  {t("catmgmt.rename")}
                </button>
                <button
                  className="btn small danger"
                  onClick={() => deleteCategory(c.id, c.label)}
                  disabled={catBusy}
                >
                  {t("action.delete")}
                </button>
              </li>
            ))}
          </ul>
        </div>

        {catMsg && (
          <div className="settings-row">
            <span className="settings-label" />
            <span className={`sync-result ${catMsg.ok ? "" : "error"}`}>{catMsg.text}</span>
          </div>
        )}
      </section>
    </div>
  );
}
