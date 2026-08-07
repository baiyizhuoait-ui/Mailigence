import { useI18n } from "../i18n";

interface ProgressBarProps {
  /** 0 - 100 */
  pct: number;
  status?: "pending" | "running" | "completed" | "failed" | "cancelled";
  processed?: number;
  total?: number;
}

const STATUS_LABEL: Record<string, string> = {
  pending: "import.status.pending",
  running: "import.status.running",
  completed: "import.status.completed",
  failed: "import.status.failed",
  cancelled: "import.status.cancelled",
};

export function ProgressBar({ pct, status = "running", processed, total }: ProgressBarProps) {
  const { t } = useI18n();
  const clamped = Math.max(0, Math.min(100, pct));
  const indeterminate = status === "pending" && (!total || total === 0);
  const showCounts = typeof processed === "number" && typeof total === "number" && total > 0;

  return (
    <div className="progress-wrap">
      <div className="progress-track">
        <div
          className={`progress-fill ${status} ${indeterminate ? "indeterminate" : ""}`}
          style={indeterminate ? undefined : { width: `${clamped}%` }}
        />
      </div>
      <div className="progress-meta">
        <span className={`progress-status ${status}`}>{t(STATUS_LABEL[status] ?? status)}</span>
        <span className="progress-counts mono">
          {showCounts
            ? `${processed} / ${total} · ${clamped.toFixed(1)}%`
            : indeterminate
              ? t("import.counting")
              : `${clamped.toFixed(1)}%`}
        </span>
      </div>
    </div>
  );
}
