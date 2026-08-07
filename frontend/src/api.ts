import type {
  AdStats,
  AiSettings,
  AiSettingsInput,
  BatchAdAction,
  BatchAdActionResult,
  BlockedSender,
  DashboardSummary,
  EmailAccount,
  EmailListResponse,
  IdleStatus,
  ImportJob,
  ReportRange,
  ReportSummary,
  ScheduleResult,
  SyncResult,
  UnifiedEmail,
  UnsubscribeInfo,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`${resp.status}: ${detail}`);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<{ status: string; encryption_configured: boolean }>("/health"),

  listAccounts: () => request<EmailAccount[]>("/accounts"),

  testConnection: (payload: {
    auth_type: string;
    platform: string;
    email: string;
    credential?: string;
    imap_server?: string;
    imap_port: number;
    smtp_server?: string;
    smtp_port: number;
  }) =>
    request<{ ok: boolean }>("/accounts/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  createAccount: (payload: {
    auth_type: "app_password";
    platform: string;
    email: string;
    credential: string;
    display_name?: string;
    imap_server?: string;
    imap_port: number;
    smtp_server?: string;
    smtp_port: number;
  }) =>
    request<EmailAccount>("/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteAccount: (id: number) =>
    request<void>(`/accounts/${id}`, { method: "DELETE" }),

  syncAccount: (id: number, days = 7) =>
    request<SyncResult>(`/accounts/${id}/sync?days=${days}`, { method: "POST" }),

  startImport: (accountId: number, payload: { days?: number; since?: string }) =>
    request<ImportJob>(`/accounts/${accountId}/import`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getImportJob: (jobId: number) =>
    request<ImportJob>(`/import-jobs/${jobId}`),

  cancelImportJob: (jobId: number) =>
    request<{ ok: boolean; status: string }>(`/import-jobs/${jobId}/cancel`, {
      method: "POST",
    }),

  getLatestImportJob: (accountId: number) =>
    request<ImportJob>(`/accounts/${accountId}/import-jobs/latest`),

  oauthStart: (provider: string, platform?: string) =>
    request<{ authorization_url: string; state: string }>(
      `/oauth/start?provider=${provider}${platform ? `&platform=${platform}` : ""}`,
    ),

  listEmails: (params: {
    account_id?: number;
    platform?: string;
    category?: string;
    q?: string;
    unread_only?: boolean;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== false) qs.set(k, String(v));
    });
    return request<EmailListResponse>(`/emails?${qs.toString()}`);
  },

  getEmail: (id: number) => request<UnifiedEmail>(`/emails/${id}`),

  markEmailRead: (id: number) =>
    request<UnifiedEmail>(`/emails/${id}/read`, { method: "PATCH" }),

  // ---------------- Ad management ----------------

  getAdStats: () => request<AdStats>("/ads/stats"),

  batchAdAction: (action: BatchAdAction, email_ids: number[]) =>
    request<BatchAdActionResult>("/ads/batch", {
      method: "POST",
      body: JSON.stringify({ action, email_ids }),
    }),

  blockSenderByEmail: (email_id: number) =>
    request<BlockedSender>(`/ads/${email_id}/block`, { method: "POST" }),

  getUnsubscribeInfo: (email_id: number) =>
    request<UnsubscribeInfo>(`/ads/${email_id}/unsubscribe`),

  listBlockedSenders: (account_id?: number) => {
    const qs = new URLSearchParams();
    if (account_id !== undefined && account_id !== null) {
      qs.set("account_id", String(account_id));
    }
    const query = qs.toString();
    return request<BlockedSender[]>(
      `/blocked-senders${query ? `?${query}` : ""}`,
    );
  },

  addBlockedSender: (payload: {
    sender_email: string;
    sender_name?: string;
    reason?: string;
    account_id?: number | null;
  }) =>
    request<BlockedSender>("/blocked-senders", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  removeBlockedSender: (id: number) =>
    request<void>(`/blocked-senders/${id}`, { method: "DELETE" }),

  // ---------------- Inbox reports ----------------

  getReportSummary: (range: ReportRange, account_id?: number) => {
    const qs = new URLSearchParams({ range });
    if (account_id !== undefined && account_id !== null) {
      qs.set("account_id", String(account_id));
    }
    return request<ReportSummary>(`/reports/summary?${qs.toString()}`);
  },

  // ---------------- Reply tracking ----------------

  syncSentMail: (account_id: number) =>
    request<{ imported: number; matched: number; message?: string; error?: string }>(
      `/replies/${account_id}/sync-sent`,
      { method: "POST" },
    ),

  getPendingReplies: (account_id?: number) => {
    const qs = new URLSearchParams();
    if (account_id !== undefined && account_id !== null) {
      qs.set("account_id", String(account_id));
    }
    const query = qs.toString();
    return request<UnifiedEmail[]>(
      `/replies/pending${query ? `?${query}` : ""}`,
    );
  },

  getReplyThread: (email_id: number) =>
    request<UnifiedEmail[]>(`/replies/threads/${email_id}`),

  // ---------------- IDLE monitoring ----------------

  startIdle: (account_id: number) =>
    request<{ started: boolean; account_id: number }>(
      `/idle/${account_id}/start`,
      { method: "POST" },
    ),

  stopIdle: (account_id: number) =>
    request<{ stopped: boolean; account_id: number }>(
      `/idle/${account_id}/stop`,
      { method: "POST" },
    ),

  getIdleStatus: (account_id: number) =>
    request<IdleStatus>(`/idle/${account_id}/status`),

  getAllIdleStatus: () =>
    request<Record<string, IdleStatus>>("/idle/status"),

  // ---------------- Dashboard ----------------

  getDashboardSummary: () =>
    request<DashboardSummary>("/dashboard/summary"),

  getDashboardSchedule: () =>
    request<ScheduleResult>("/dashboard/schedule"),

  getDashboardPending: () =>
    request<UnifiedEmail[]>("/dashboard/pending"),

  syncAllAccounts: () =>
    request<{ synced: number; accounts: number; errors: unknown[] }>(
      "/dashboard/sync",
      { method: "POST" },
    ),

  handleEmail: (emailId: number) =>
    request<{ handled: boolean; email_id: number }>(
      `/dashboard/${emailId}/handle`,
      { method: "POST" },
    ),

  // ---------------- Settings ----------------

  getAiSettings: () => request<AiSettings>("/settings"),

  updateAiSettings: (payload: AiSettingsInput) =>
    request<AiSettings>("/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
};
