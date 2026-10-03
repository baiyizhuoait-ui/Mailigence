import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  ACCENT_PRESETS,
  useI18n,
  type AccentColor,
  type BootMode,
  type Lang,
  type ThemeMode,
} from "../i18n";
import type {
  AiMemory,
  AiSettings,
  AnalysisMode,
  EmailAccount,
  EmailCategory,
  ProbeContextResult,
  ProviderProfile,
  ProviderType,
} from "../types";

/** True when the base_url points at a local inference server (probe-able). */
function isLocalBaseUrl(url: string): boolean {
  const u = (url || "").toLowerCase();
  return (
    u.includes("localhost") ||
    u.includes("127.0.0.1") ||
    u.includes("[::1]") ||
    u.includes("0.0.0.0")
  );
}

// Preset swatches for category / account colors.
const COLOR_PRESETS = [
  "#4a9eff", "#14b8a6", "#5cb874", "#e8b04b", "#f97316",
  "#e0725f", "#ec4899", "#a073d4", "#6366f1", "#06b6d4", "#64748b", "#9ca3af",
];

const PROVIDER_TYPE_KEYS: Record<ProviderType, string> = {
  openai_compatible: "settings.profileType.openai_compatible",
  anthropic: "settings.profileType.anthropic",
  rules_only: "settings.profileType.rules_only",
};

const BOOT_MODES: BootMode[] = ["off", "simple", "full"];

const MODE_KEYS: { mode: AnalysisMode; labelKey: string; descKey: string }[] = [
  { mode: "auto", labelKey: "settings.ai.mode.auto", descKey: "settings.ai.mode.autoDesc" },
  { mode: "ai_only", labelKey: "settings.ai.mode.ai_only", descKey: "settings.ai.mode.ai_onlyDesc" },
  { mode: "rules_only", labelKey: "settings.ai.mode.rules_only", descKey: "settings.ai.mode.rules_onlyDesc" },
];

export function SettingsView() {
  const {
    t,
    lang,
    theme,
    accent,
    customAccent,
    bootMode,
    setLang,
    setTheme,
    setAccent,
    setCustomAccent,
    setBootMode,
  } = useI18n();

  // ---- AI settings state ----
  const [mode, setMode] = useState<AnalysisMode>("auto");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [status, setStatus] = useState<AiSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // ---- AI provider profiles (multi-config) ----
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [formProfileId, setFormProfileId] = useState<number | null>(null);
  const [formLabel, setFormLabel] = useState("");
  const [formType, setFormType] = useState<ProviderType>("openai_compatible");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formModel, setFormModel] = useState("");
  const [formApiKey, setFormApiKey] = useState("");
  const [formBusy, setFormBusy] = useState(false);
  const [formMsg, setFormMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [probeModels, setProbeModels] = useState<string[] | null>(null);
  const [probeLoading, setProbeLoading] = useState(false);
  const [probeErr, setProbeErr] = useState("");
  // Context-window (num_ctx) form state.
  const [formNumCtx, setFormNumCtx] = useState("0");
  const [ctxProbing, setCtxProbing] = useState(false);
  const [ctxResult, setCtxResult] = useState<ProbeContextResult | null>(null);
  const [ctxErr, setCtxErr] = useState("");
  const [testBusy, setTestBusy] = useState<number | null>(null);
  const [testMsg, setTestMsg] = useState<{ id: number; ok: boolean; text: string } | null>(null);
  const [deleteBusy, setDeleteBusy] = useState<number | null>(null);
  // Last-used base_url/model per provider_type — auto-fill when switching type.
  const lastByType = useRef<Partial<Record<ProviderType, { baseUrl: string; model: string }>>>({});

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
      setEmbeddingModel(s.embedding_model);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  // ---- provider profiles ----
  const loadProfiles = useCallback(async () => {
    try {
      setProfiles(await api.listProviderProfiles());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

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

  async function saveSettings() {
    setSaving(true);
    setSaveMsg(null);
    try {
      const s = await api.updateAiSettings({
        analysis_mode: mode,
        embedding_model: embeddingModel,
      });
      setStatus(s);
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

  // ---- provider profile handlers ----

  function resetForm() {
    setFormProfileId(null);
    setFormLabel("");
    setFormType("openai_compatible");
    setFormBaseUrl("");
    setFormModel("");
    setFormApiKey("");
    setFormNumCtx("0");
    setProbeModels(null);
    setProbeErr("");
    setCtxResult(null);
    setCtxErr("");
    setFormMsg(null);
  }

  function openNewForm() {
    resetForm();
    setFormOpen(true);
  }

  function openEditForm(p: ProviderProfile) {
    setFormProfileId(p.id);
    setFormLabel(p.label);
    setFormType(p.provider_type);
    setFormBaseUrl(p.base_url);
    setFormModel(p.model);
    setFormApiKey("");
    setFormNumCtx(String(p.num_ctx ?? 0));
    setProbeModels(null);
    setProbeErr("");
    setCtxResult(null);
    setCtxErr("");
    setFormMsg(null);
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    resetForm();
  }

  /** Switching provider_type re-fills last-used base_url/model for that type. */
  function onFormTypeChange(type: ProviderType) {
    setFormType(type);
    if (formProfileId === null) {
      // New profile: prefill from session memory, else from a saved profile of
      // that type (so switching back never requires re-typing).
      const remembered = lastByType.current[type];
      if (remembered) {
        setFormBaseUrl(remembered.baseUrl);
        setFormModel(remembered.model);
        return;
      }
      const saved = [...profiles].reverse().find((p) => p.provider_type === type);
      if (saved) {
        setFormBaseUrl(saved.base_url);
        setFormModel(saved.model);
      }
    }
  }

  async function saveProfileForm() {
    const label = formLabel.trim();
    if (!label) {
      setFormMsg({ ok: false, text: t("settings.profile.needLabel") });
      return;
    }
    setFormBusy(true);
    setFormMsg(null);
    try {
      const payload = {
        label,
        provider_type: formType,
        base_url: formBaseUrl,
        model: formModel,
        api_key: formApiKey,
        num_ctx: Math.max(parseInt(formNumCtx, 10) || 0, 0),
      };
      if (formProfileId === null) {
        await api.createProviderProfile(payload);
      } else {
        await api.updateProviderProfile(formProfileId, payload);
      }
      // Remember the connection values for this type (auto-fill later).
      lastByType.current[formType] = { baseUrl: formBaseUrl, model: formModel };
      await loadProfiles();
      await loadSettings();
      closeForm();
    } catch (e) {
      setFormMsg({
        ok: false,
        text: `${t("settings.profile.saveFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setFormBusy(false);
    }
  }

  /** Probe the local model's safe context window (real test requests). */
  async function probeFormContext() {
    if (ctxProbing) return;
    setCtxResult(null);
    setCtxErr("");
    if (!formModel.trim()) {
      setCtxErr(t("settings.profile.ctxProbeNeedModel"));
      return;
    }
    if (formProfileId === null) {
      setCtxErr(t("settings.profile.ctxProbeNeedSave"));
      return;
    }
    setCtxProbing(true);
    try {
      const result = await api.probeProviderContext(formProfileId);
      setCtxResult(result);
      // Fill the probed value in — still manually editable.
      setFormNumCtx(String(result.probed_num_ctx));
    } catch (e) {
      setCtxErr(
        `${t("settings.profile.ctxProbeFail")}${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setCtxProbing(false);
    }
  }

  async function activateProfile(id: number) {
    setTestBusy(id);
    setFormMsg(null);
    try {
      await api.activateProviderProfile(id);
      await loadProfiles();
      await loadSettings();
    } catch (e) {
      setFormMsg({
        ok: false,
        text: `${t("settings.profile.activateFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setTestBusy(null);
    }
  }

  async function deleteProfile(p: ProviderProfile) {
    if (!window.confirm(t("settings.profile.deleteConfirm"))) return;
    setDeleteBusy(p.id);
    try {
      await api.deleteProviderProfile(p.id);
      await loadProfiles();
      await loadSettings();
    } catch (e) {
      setFormMsg({
        ok: false,
        text: `${t("settings.profile.deleteFailed")}${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setDeleteBusy(null);
    }
  }

  async function testProfile(p: ProviderProfile) {
    setTestBusy(p.id);
    setTestMsg(null);
    try {
      const r = await api.testProviderConnection(p.id);
      setTestMsg({ id: p.id, ok: true, text: r.message });
    } catch (e) {
      const msg = e instanceof Error ? e.message.replace(/^\d{3}:\s*/, "") : String(e);
      setTestMsg({ id: p.id, ok: false, text: `${t("settings.profile.testFail")}${msg}` });
    } finally {
      setTestBusy(null);
    }
  }

  async function probeFormModels() {
    const base = formBaseUrl.trim();
    if (!base) {
      setProbeErr(t("settings.profile.probeNeedUrl"));
      setProbeModels(null);
      return;
    }
    setProbeLoading(true);
    setProbeErr("");
    try {
      const resp =
        formProfileId !== null
          ? await api.probeProviderModels(formProfileId)
          : await api.probeAdHocModels({
              provider_type: formType,
              base_url: base,
              api_key: formApiKey,
            });
      setProbeModels(resp.models);
      if (resp.models.length === 0) setProbeErr(t("settings.profile.probeEmpty"));
    } catch (e) {
      setProbeModels(null);
      setProbeErr(t("settings.profile.probeFail"));
    } finally {
      setProbeLoading(false);
    }
  }

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

        {/* Semantic search (embedding) — optional; greyed out when AI is off */}
        <div className={`settings-row${!active ? " disabled" : ""}`}>
          <span className="settings-label">{t("settings.ai.embeddingModel")}</span>
          <div className="settings-field-group">
            <input
              className="settings-input mono"
              value={embeddingModel}
              onChange={(e) => setEmbeddingModel(e.target.value)}
              placeholder={active ? "text-embedding-3-small" : ""}
              disabled={!active}
            />
            <span className="settings-hint">
              {t("settings.ai.embeddingHint")}
              {!active && <em> · {t("settings.ai.embeddingDisabled")}</em>}
            </span>
          </div>
        </div>

        {/* Global save (analysis mode + embedding model) */}
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

        {/* ---- AI 配置管理（多配置并存） ---- */}
        <div className="profile-block">
          <div className="profile-block-head">
            <span className="mem-title">{t("settings.profile.title")}</span>
            <button className="btn small primary-soft" onClick={openNewForm}>
              {t("settings.profile.new")}
            </button>
          </div>
          <p className="settings-hint">{t("settings.profile.sub")}</p>

          {profiles.length === 0 && !formOpen ? (
            <div className="profile-empty">
              <p>{t("settings.profile.empty")}</p>
              <button className="btn small primary-soft" onClick={openNewForm}>
                {t("settings.profile.new")}
              </button>
            </div>
          ) : (
            <div className="profile-list">
              {profiles.map((p) => (
                <div key={p.id} className={`profile-card${p.is_active ? " active" : ""}`}>
                  <div className="profile-card-main">
                    <div className="profile-card-title">
                      <span className="profile-label">{p.label}</span>
                      {p.is_active && <span className="profile-badge active">{t("settings.profile.active")}</span>}
                      <span className="profile-badge type">
                        {t(`settings.profileType.${p.provider_type}`)}
                      </span>
                      {p.api_key_configured && <span className="profile-badge key">API Key</span>}
                    </div>
                    <div className="profile-card-meta mono">
                      {p.base_url}
                      {p.model && ` · ${p.model}`}
                    </div>
                  </div>
                  <div className="profile-card-actions">
                    {!p.is_active && (
                      <button
                        className="btn small ghost"
                        onClick={() => activateProfile(p.id)}
                        disabled={testBusy !== null}
                      >
                        {t("settings.profile.activate")}
                      </button>
                    )}
                    <button
                      className="btn small ghost"
                      onClick={() => testProfile(p)}
                      disabled={testBusy !== null}
                    >
                      {testBusy === p.id ? "…" : t("settings.profile.test")}
                    </button>
                    <button className="btn small ghost" onClick={() => openEditForm(p)}>
                      {t("settings.profile.edit")}
                    </button>
                    <button
                      className="btn small ghost danger"
                      onClick={() => deleteProfile(p)}
                      disabled={deleteBusy === p.id}
                    >
                      {deleteBusy === p.id ? "…" : t("settings.profile.delete")}
                    </button>
                  </div>
                  {testMsg?.id === p.id && (
                    <span className={`sync-result ${testMsg.ok ? "" : "error"}`}>{testMsg.text}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {formOpen && (
            <div className="profile-form">
              <div className="settings-row">
                <span className="settings-label">{t("settings.profile.label")}</span>
                <input
                  className="settings-input"
                  value={formLabel}
                  onChange={(e) => setFormLabel(e.target.value)}
                  placeholder={t("settings.profile.labelPlaceholder")}
                />
              </div>
              <div className="settings-row">
                <span className="settings-label">{t("settings.profile.type")}</span>
                <select
                  className="settings-select"
                  value={formType}
                  onChange={(e) => onFormTypeChange(e.target.value as ProviderType)}
                >
                  {(Object.keys(PROVIDER_TYPE_KEYS) as ProviderType[]).map((k) => (
                    <option key={k} value={k}>
                      {t(PROVIDER_TYPE_KEYS[k])}
                    </option>
                  ))}
                </select>
              </div>
              <div className="settings-row">
                <span className="settings-label">{t("settings.ai.baseUrl")}</span>
                <input
                  className="settings-input mono"
                  value={formBaseUrl}
                  onChange={(e) => setFormBaseUrl(e.target.value)}
                  placeholder="https://api.openai.com/v1 或 http://localhost:1234/v1"
                />
              </div>
              <div className="settings-row">
                <span className="settings-label">{t("settings.ai.model")}</span>
                <div className="settings-field-group">
                  <input
                    className="settings-input mono"
                    value={formModel}
                    onChange={(e) => setFormModel(e.target.value)}
                    placeholder="deepseek-chat / qwen2.5:7b / 手动输入均可"
                  />
                  <span className="settings-hint">
                    <button
                      type="button"
                      className="btn small ghost"
                      onClick={probeFormModels}
                      disabled={probeLoading || formType === "rules_only"}
                    >
                      {probeLoading ? t("settings.profile.probeLoading") : t("settings.profile.probe")}
                    </button>
                  </span>
                  {probeModels && probeModels.length > 0 && (
                    <div className="probe-candidates">
                      <span className="settings-hint">{t("settings.profile.probeHint")}</span>
                      <div className="probe-candidate-row">
                        {probeModels.map((m) => (
                          <button
                            key={m}
                            type="button"
                            className="probe-candidate"
                            onClick={() => setFormModel(m)}
                          >
                            {m}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {probeErr && (
                    <span className="sync-result error">{t("settings.profile.probeFail")}</span>
                  )}
                </div>
              </div>
              {isLocalBaseUrl(formBaseUrl) && formType !== "rules_only" && (
                <div className="settings-row">
                  <span className="settings-label">{t("settings.profile.numCtx")}</span>
                  <div className="settings-field-group">
                    <input
                      className="settings-input mono"
                      type="number"
                      min={0}
                      step={1024}
                      value={formNumCtx}
                      onChange={(e) => setFormNumCtx(e.target.value)}
                      placeholder="0"
                    />
                    <span className="settings-hint">
                      <button
                        type="button"
                        className="btn small ghost"
                        onClick={probeFormContext}
                        disabled={ctxProbing || formProfileId === null || !formModel.trim()}
                      >
                        {ctxProbing
                          ? t("settings.profile.ctxProbing")
                          : t("settings.profile.ctxProbe")}
                      </button>
                      {t("settings.profile.numCtxHint")}
                    </span>
                    {ctxResult && (
                      <div className="probe-candidates">
                        <span className="settings-hint">
                          {ctxResult.model_max_context
                            ? t("settings.profile.ctxProbeOk", {
                                max: ctxResult.model_max_context,
                                probed: ctxResult.probed_num_ctx,
                              })
                            : t("settings.profile.ctxProbeOkNoMax", {
                                probed: ctxResult.probed_num_ctx,
                              })}
                        </span>
                        {ctxResult.model_max_context &&
                          ctxResult.probed_num_ctx < ctxResult.model_max_context && (
                            <span className="settings-hint">
                              {t("settings.profile.ctxVramHint")}
                            </span>
                          )}
                        <span className="settings-hint">
                          {t("settings.profile.ctxConcurrency", {
                            n: ctxResult.ai_concurrency,
                          })}
                        </span>
                      </div>
                    )}
                    {ctxErr && <span className="sync-result error">{ctxErr}</span>}
                  </div>
                </div>
              )}
              <div className="settings-row">
                <span className="settings-label">{t("settings.ai.apiKey")}</span>
                <div className="settings-field-group">
                  <input
                    className="settings-input mono"
                    type="password"
                    value={formApiKey}
                    onChange={(e) => setFormApiKey(e.target.value)}
                    placeholder={
                      formProfileId !== null
                        ? "••••••••"
                        : t("settings.profile.apiKeyPlaceholder")
                    }
                  />
                  <span className="settings-hint">{t("settings.ai.apiKeyHint")}</span>
                </div>
              </div>
              <div className="settings-row">
                <span className="settings-label" />
                <div className="settings-field-group profile-form-actions">
                  <button className="btn primary" onClick={saveProfileForm} disabled={formBusy}>
                    {formBusy ? "…" : t("settings.profile.save")}
                  </button>
                  <button className="btn small ghost" onClick={closeForm}>
                    {t("settings.profile.cancel")}
                  </button>
                  {formMsg && (
                    <span className={`sync-result ${formMsg.ok ? "" : "error"}`}>{formMsg.text}</span>
                  )}
                </div>
              </div>
            </div>
          )}
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

        {/* Boot animation */}
        <div className="settings-row">
          <span className="settings-label">{t("settings.boot")}</span>
          <div>
            <div className="seg-control">
              {BOOT_MODES.map((m) => (
                <button
                  key={m}
                  className={`seg-btn ${bootMode === m ? "active" : ""}`}
                  onClick={() => setBootMode(m)}
                >
                  {t(`settings.boot.${m}`)}
                </button>
              ))}
            </div>
            <div className="settings-hint">{t("settings.boot.hint")}</div>
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
