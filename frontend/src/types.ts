// Types mirror the backend Pydantic schemas (Stage 1 contract).

export type AuthType = "app_password" | "oauth_google" | "oauth_microsoft";
export type SyncStatus = "idle" | "syncing" | "error";
export type MailDirection = "inbox" | "sent";
export type ImportStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface EmailAccount {
  id: number;
  platform: string;
  email: string;
  display_name: string;
  auth_type: AuthType;
  imap_server: string;
  imap_port: number;
  smtp_server: string;
  smtp_port: number;
  sync_status: SyncStatus;
  last_synced_at: string | null;
  last_error: string;
  created_at: string;
}

export interface UnifiedEmail {
  id: number;
  account_id: number;
  platform: string;
  thread_id: string;
  direction: MailDirection;
  sender: string;
  sender_email: string;
  recipients: string[];
  subject: string;
  body_snippet: string;
  received_at: string | null;
  is_read: boolean;
  has_reply: boolean;
  is_archived: boolean;
  is_starred: boolean;
  category: string | null;
  is_advertisement: boolean | null;
  priority_score: number | null;
  summary: string | null;
  suggested_action: string | null;
  analyzed_at: string | null;
}

// ---------------- Dynamic email categories ----------------

export interface EmailCategory {
  id: number;
  name: string; // key stored on UnifiedEmail.category
  label: string; // display name
  color: string | null;
  is_system: boolean; // built-in seed vs AI/user-created
  email_count: number;
  created_at: string | null;
}

export interface CategoryCreateInput {
  name: string;
  label?: string;
  color?: string;
}

export interface CategoryUpdateInput {
  label?: string;
  color?: string;
}

export interface EmailListResponse {
  total: number;
  items: UnifiedEmail[];
}

export interface SyncResult {
  account_id: number;
  synced: number;
  status: SyncStatus;
  error: string;
}

export interface ImportJob {
  id: number;
  account_id: number;
  status: ImportStatus;
  range_days: number;
  since_date: string;
  total: number;
  processed: number;
  started_at: string | null;
  finished_at: string | null;
  error: string;
  created_at: string;
  progress_pct: number;
}

export interface ProviderPreset {
  platform: string;
  label: string;
  imap_server: string;
  imap_port: number;
  smtp_server: string;
  smtp_port: number;
  supports_oauth: boolean;
  // Default domain suffix(es) for the email input. Empty = custom/unknown.
  domains: string[];
}

// Hardcoded mirror of backend PROVIDER_PRESETS for the add-account form.
export const PROVIDER_PRESETS: ProviderPreset[] = [
  { platform: "gmail", label: "Gmail", imap_server: "imap.gmail.com", imap_port: 993, smtp_server: "smtp.gmail.com", smtp_port: 465, supports_oauth: true, domains: ["gmail.com"] },
  { platform: "outlook", label: "Outlook / Hotmail", imap_server: "outlook.office365.com", imap_port: 993, smtp_server: "smtp.office365.com", smtp_port: 587, supports_oauth: true, domains: ["outlook.com", "hotmail.com", "live.com"] },
  { platform: "qq", label: "QQ 邮箱", imap_server: "imap.qq.com", imap_port: 993, smtp_server: "smtp.qq.com", smtp_port: 465, supports_oauth: false, domains: ["qq.com", "vip.qq.com", "foxmail.com"] },
  { platform: "netease", label: "网易邮箱", imap_server: "", imap_port: 993, smtp_server: "", smtp_port: 465, supports_oauth: false, domains: ["163.com", "126.com", "188.com", "yeah.net"] },
  { platform: "yahoo", label: "Yahoo Mail", imap_server: "imap.mail.yahoo.com", imap_port: 993, smtp_server: "smtp.mail.yahoo.com", smtp_port: 465, supports_oauth: false, domains: ["yahoo.com", "ymail.com", "rocketmail.com"] },
  { platform: "icloud", label: "iCloud Mail", imap_server: "imap.mail.me.com", imap_port: 993, smtp_server: "smtp.mail.me.com", smtp_port: 465, supports_oauth: false, domains: ["icloud.com", "me.com", "mac.com"] },
  { platform: "aol", label: "AOL Mail", imap_server: "imap.aol.com", imap_port: 993, smtp_server: "smtp.aol.com", smtp_port: 465, supports_oauth: false, domains: ["aol.com"] },
  { platform: "zoho", label: "Zoho Mail", imap_server: "imap.zoho.com", imap_port: 993, smtp_server: "smtp.zoho.com", smtp_port: 465, supports_oauth: false, domains: ["zoho.com", "zohomail.com"] },
  { platform: "yandex", label: "Yandex Mail", imap_server: "imap.yandex.com", imap_port: 993, smtp_server: "smtp.yandex.com", smtp_port: 465, supports_oauth: false, domains: ["yandex.com", "yandex.ru", "yandex.ua"] },
  { platform: "imap", label: "自定义 IMAP", imap_server: "", imap_port: 993, smtp_server: "", smtp_port: 465, supports_oauth: false, domains: [] },
];

// NetEase per-domain IMAP/SMTP hosts (mirrors backend NETEASE_SERVER_MAP).
export const NETEASE_IMAP_MAP: Record<string, string> = {
  "163.com": "imap.163.com",
  "126.com": "imap.126.com",
  "188.com": "imap.188.com",
  "yeah.net": "imap.yeah.net",
};

export const PLATFORM_LABEL: Record<string, string> = Object.fromEntries(
  PROVIDER_PRESETS.map((p) => [p.platform, p.label]),
);

// ---------------- Ad management ----------------

export interface AdStats {
  total_ads: number;
  blocked_senders: number;
  ads_by_category: Record<string, number>;
}

export type BatchAdAction = "delete" | "mark_read";

export interface BatchAdActionRequest {
  action: BatchAdAction;
  email_ids: number[];
}

export interface BatchAdActionResult {
  affected: number;
  action: string;
}

export interface BlockedSender {
  id: number;
  account_id: number | null;
  sender_email: string;
  sender_name: string;
  reason: string;
  created_at: string;
}

export interface UnsubscribeInfo {
  email_id: number;
  has_unsubscribe: boolean;
  url: string | null;
  mailto: string | null;
}

// ---------------- Inbox reports ----------------

export type ReportRange = "day" | "week" | "month";

export interface TopSender {
  sender_email: string;
  sender_name: string;
  count: number;
}

export interface DailyTrendPoint {
  date: string; // YYYY-MM-DD
  count: number;
}

export interface ReportSummary {
  range: string; // "day" | "week" | "month"
  start_date: string; // YYYY-MM-DD
  end_date: string; // YYYY-MM-DD
  account_id: number | null;
  total: number;
  unread: number;
  ads: number;
  category_dist: Record<string, number>;
  top_senders: TopSender[];
  daily_trend: DailyTrendPoint[];
  priority_dist: { high: number; medium: number; low: number };
  action_dist: Record<string, number>;
}

// ---------------- IDLE monitoring ----------------

export interface IdleStatus {
  running: boolean;
  last_event_at: string | null;
  events: number;
  last_sync_count: number;
  error: string;
}

// ---------------- Dashboard ----------------

export interface DashboardSummary {
  pending_count: number;
  urgent_count: number;
  unread_count: number;
  today_count: number;
  last_mail_at: string | null;
}

export interface ScheduleItem {
  title: string;
  date: string;
  time: string;
  email_id: number;
  type: string; // meeting | deadline | appointment | reminder
  group?: string; // today | tomorrow | this_week | upcoming
}

export interface PriorityQueueItem {
  email_id: number;
  reason: string;
  urgency: "high" | "medium" | "low";
  estimated_minutes: number;
}

export interface ScheduleResult {
  schedule_items: ScheduleItem[];
  priority_queue: PriorityQueueItem[];
  daily_brief: string;
  source: "ai" | "rules";
}

// ---- AI settings (settings page) ----

export type AnalysisMode = "auto" | "ai_only" | "rules_only";
export type AiProvider = "" | "openai" | "anthropic";

export interface AiSettings {
  analysis_mode: AnalysisMode;
  provider: AiProvider;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  api_key_from_db: boolean;
  env_provider: string;
  env_base_url: string;
  env_model: string;
  env_key_configured: boolean;
}

export interface AiSettingsInput {
  analysis_mode: AnalysisMode;
  provider: AiProvider;
  base_url: string;
  model: string;
  api_key: string;
  clear_api_key: boolean;
}
