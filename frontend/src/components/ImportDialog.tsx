import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { ImportJob } from "../types";
import { ProgressBar } from "./ProgressBar";

interface Props {
  accountId: number;
  accountLabel: string;
  onClose: () => void;
  onCompleted: () => void;
}

type Phase = "select" | "progress";
type RangeChoice = 7 | 30 | 90 | "custom";

const POLL_MS = 1000;

export function ImportDialog({ accountId, accountLabel, onClose, onCompleted }: Props) {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>("select");
  const [choice, setChoice] = useState<RangeChoice>(30);
  const [customDate, setCustomDate] = useState<string>("");
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(true);
  const completedNotified = useRef(false);

  function maxCustomDate() {
    return new Date().toISOString().split("T")[0];
  }

  // On open: if an import is already running for this account, resume showing
  // its progress instead of offering to start a new one.
  useEffect(() => {
    let active = true;
    api
      .getLatestImportJob(accountId)
      .then((latest) => {
        if (!active) return;
        if (latest && (latest.status === "pending" || latest.status === "running")) {
          setJob(latest);
          setPhase("progress");
        }
      })
      .catch(() => {
        /* 404 = no prior job -> stay on select */
      })
      .finally(() => active && setChecking(false));
    return () => {
      active = false;
    };
  }, [accountId]);

  async function start() {
    setError("");
    if (choice === "custom" && !customDate) {
      setError(t("import.errorNoDate"));
      return;
    }
    setStarting(true);
    try {
      const payload =
        choice === "custom" ? { since: customDate } : { days: choice as number };
      const created = await api.startImport(accountId, payload);
      setJob(created);
      setPhase("progress");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  // Poll the running job until it reaches a terminal state.
  useEffect(() => {
    if (phase !== "progress" || !job) return;
    let active = true;

    const tick = async () => {
      try {
        const latest = await api.getImportJob(job.id);
        if (!active) return;
        setJob(latest);
        if (["completed", "failed", "cancelled"].includes(latest.status)) {
          if (latest.status === "completed" && !completedNotified.current) {
            completedNotified.current = true;
            onCompleted();
          }
          return false; // stop polling
        }
        return true; // keep polling
      } catch {
        return false;
      }
    };

    let timer: ReturnType<typeof setTimeout>;
    const loop = async () => {
      const keep = await tick();
      if (keep && active) {
        timer = setTimeout(loop, POLL_MS);
      }
    };
    loop();

    return () => {
      active = false;
      clearTimeout(timer!);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, job?.id]);

  async function cancel() {
    if (!job) return;
    try {
      await api.cancelImportJob(job.id);
    } catch {
      /* surface via next poll */
    }
  }

  const isTerminal = job && ["completed", "failed", "cancelled"].includes(job.status);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>{t("import.title")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("misc.close")}>
            ✕
          </button>
        </header>

        <div className="modal-body">
          <p className="dialog-account">
            <span className="dim">{t("import.account")}</span>
            <span className="mono">{accountLabel}</span>
          </p>

          {checking ? (
            <p className="hint">{t("import.checking")}</p>
          ) : null}

          {!checking && phase === "select" && (
            <>
              <p className="dialog-section-title">{t("import.selectRange")}</p>
              <div className="range-options">
                {([7, 30, 90] as RangeChoice[]).map((d) => (
                  <label key={d} className={`range-card ${choice === d ? "selected" : ""}`}>
                    <input
                      type="radio"
                      name="range"
                      checked={choice === d}
                      onChange={() => setChoice(d)}
                    />
                    <span className="range-days">{d}</span>
                    <span className="range-unit">{t("import.days")}</span>
                  </label>
                ))}
                <label className={`range-card ${choice === "custom" ? "selected" : ""}`}>
                  <input
                    type="radio"
                    name="range"
                    checked={choice === "custom"}
                    onChange={() => setChoice("custom")}
                  />
                  <span className="range-days">{t("import.custom")}</span>
                </label>
              </div>

              {choice === "custom" && (
                <label className="field">
                  <span>{t("import.startDate")}</span>
                  <input
                    type="date"
                    max={maxCustomDate()}
                    value={customDate}
                    onChange={(e) => setCustomDate(e.target.value)}
                  />
                </label>
              )}

              <p className="hint">
                {t("import.asyncHint")}
                {t("import.largeRangeHint")}
              </p>

              {error && <div className="alert error">{error}</div>}
            </>
          )}

          {phase === "progress" && job && (
            <div className="import-progress">
              <ProgressBar
                pct={job.progress_pct}
                status={job.status}
                processed={job.processed}
                total={job.total}
              />
              <p className="hint">
                {t("import.rangeRecent")}{job.range_days} {t("import.days")} {t("import.sincePrefix")} {job.since_date} {t("import.sinceSuffix")}
              </p>
              {job.status === "failed" && job.error && (
                <div className="alert error">{job.error}</div>
              )}
              {job.status === "completed" && (
                <div className="alert success">
                  {t("import.imported")} {job.processed} {t("account.count")}{t("import.viewInInbox")}
                </div>
              )}
              {job.status === "cancelled" && (
                <div className="alert">{t("import.cancelled")} {job.processed} {t("account.count")}</div>
              )}
            </div>
          )}
        </div>

        <footer className="modal-foot">
          {checking ? (
            <button className="btn ghost" onClick={onClose}>
              {t("import.cancel")}
            </button>
          ) : phase === "select" ? (
            <>
              <button className="btn ghost" onClick={onClose}>
                {t("import.later")}
              </button>
              <button className="btn primary" onClick={start} disabled={starting}>
                {starting ? t("import.starting") : t("import.start")}
              </button>
            </>
          ) : (
            <>
              {!isTerminal && (
                <button className="btn ghost" onClick={cancel}>
                  {t("import.cancelImport")}
                </button>
              )}
              <button className="btn primary" onClick={onClose}>
                {isTerminal ? t("import.complete") : t("import.background")}
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
