import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

// ---------------- Types ----------------

export type Lang = "zh" | "en";
export type ThemeMode = "dark" | "light";
export type AccentColor = "amber" | "blue" | "green" | "purple" | "red";

export interface Settings {
  lang: Lang;
  theme: ThemeMode;
  accent: AccentColor;
}

// ---------------- Accent presets ----------------

export const ACCENT_PRESETS: {
  id: AccentColor;
  labelZh: string;
  labelEn: string;
  color: string;
}[] = [
  { id: "amber", labelZh: "琥珀", labelEn: "Amber", color: "#e8b04b" },
  { id: "blue", labelZh: "湖蓝", labelEn: "Blue", color: "#4a9eff" },
  { id: "green", labelZh: "翠绿", labelEn: "Green", color: "#5cb874" },
  { id: "purple", labelZh: "紫罗兰", labelEn: "Purple", color: "#a073d4" },
  { id: "red", labelZh: "珊瑚红", labelEn: "Red", color: "#e0725f" },
];

// ---------------- Translations ----------------

type Dict = Record<string, string>;

const zh: Dict = {
  // Brand
  "brand.sub": "邮件聚合分析",
  // Nav
  "nav.dashboard": "待处理",
  "nav.mailbox": "收件箱",
  "nav.accounts": "邮箱账户",
  "nav.ads": "广告管理",
  "nav.reports": "收件报告",
  "nav.replies": "回复追踪",
  "nav.settings": "设置",
  // Page titles
  "title.dashboard": "待处理邮件",
  "title.mailbox": "统一收件箱",
  "title.accounts": "邮箱账户",
  "title.ads": "广告管理",
  "title.reports": "收件报告",
  "title.replies": "回复追踪",
  "title.settings": "设置",
  // Page subs
  "sub.dashboard": "AI 分析待处理邮件，按优先级排序并提取日程安排。",
  "sub.mailbox": "跨平台邮件以统一格式呈现，底层协议差异不可见。",
  "sub.accounts": "接入 Gmail、Outlook、QQ、163 或任意 IMAP 邮箱。",
  "sub.ads": "查看广告邮件统计、批量清理，并管理被屏蔽的发件人。",
  "sub.reports": "按时间范围与账户查看收件统计、分类分布与发件人排行。",
  "sub.replies": "同步已发送邮件，追踪待回复邮件与会话线程。",
  "sub.settings": "调整外观主题、配色与界面语言。",
  // Categories
  "cat.all": "全部分类",
  "cat.work": "工作",
  "cat.meeting": "会议",
  "cat.finance": "财务账单",
  "cat.system": "系统通知",
  "cat.social": "社交",
  "cat.travel": "旅行",
  "cat.shopping": "购物",
  "cat.ad": "营销广告",
  "cat.newsletter": "订阅简报",
  "cat.personal": "个人",
  "cat.other": "其他",
  "cat.uncategorized": "未分类",
  // Common actions
  "action.addAccount": "+ 添加账户",
  "action.sync7": "同步最近 7 天",
  "action.syncing": "同步中…",
  "action.importHistory": "导入历史",
  "action.importing": "导入中…",
  "action.delete": "删除",
  "action.cancel": "取消",
  "action.refresh": "刷新",
  "action.refreshing": "刷新中…",
  "action.loading": "加载中…",
  "action.confirm": "确认",
  "action.complete": "完成",
  // IDLE
  "idle.listening": "实时监听",
  "idle.stopListening": "停止监听",
  "idle.startListening": "实时监听",
  "idle.stopListeningTitle": "停止实时监听",
  "idle.startListeningTitle": "开启实时监听（IMAP IDLE）",
  "idle.events": "IDLE推送",
  "idle.times": "次",
  // Settings page
  "settings.appearance": "外观",
  "settings.theme": "主题模式",
  "settings.theme.dark": "深色",
  "settings.theme.light": "浅色",
  "settings.accent": "主题色",
  "settings.language": "语言",
  "settings.language.zh": "中文",
  "settings.language.en": "English",
  // AI analysis
  "settings.ai": "AI 邮件分析",
  "settings.ai.mode": "分析模式",
  "settings.ai.mode.auto": "智能模式",
  "settings.ai.mode.autoDesc": "已配置 AI 时使用 AI 分析，失败或未配置时自动回退到规则",
  "settings.ai.mode.ai_only": "纯 AI 模式",
  "settings.ai.mode.ai_onlyDesc": "始终调用 AI，不使用规则分析",
  "settings.ai.mode.rules_only": "纯规则模式",
  "settings.ai.mode.rules_onlyDesc": "完全不调用 AI，仅用程序规则分析（零成本）",
  "settings.ai.provider": "接入方式",
  "settings.ai.provider.env": "环境变量（.env）",
  "settings.ai.provider.openai": "OpenAI 官方",
  "settings.ai.provider.deepseek": "DeepSeek",
  "settings.ai.provider.moonshot": "Moonshot (Kimi)",
  "settings.ai.provider.qwen": "通义千问 (DashScope)",
  "settings.ai.provider.glm": "智谱 GLM",
  "settings.ai.provider.ollama": "Ollama 本地模型",
  "settings.ai.provider.anthropic": "Anthropic Claude",
  "settings.ai.provider.custom": "自定义（OpenAI 兼容）",
  "settings.ai.providerHint": "选择预设自动填充地址；Ollama 需先在本机安装并拉取模型",
  "settings.ai.baseUrl": "接口地址 (Base URL)",
  "settings.ai.model": "模型名称",
  "settings.ai.apiKey": "API 密钥",
  "settings.ai.apiKeyHint": "密钥将加密保存到数据库；留空则保留现有配置，也可改用 .env 中的 AI_API_KEY",
  "settings.ai.clearKey": "清除已保存的密钥",
  "settings.ai.save": "保存 AI 设置",
  "settings.ai.saved": "已保存",
  "settings.ai.saveFailed": "保存失败：",
  "settings.ai.status": "当前状态",
  "settings.ai.active": "AI 分析已启用",
  "settings.ai.activeMode": "当前模式",
  "settings.ai.inactive": "当前使用规则分析（未配置 AI）",
  "settings.ai.keyFromEnv": "密钥来自 .env 环境变量",
  "settings.ai.keyFromDb": "密钥已在界面配置",
  "settings.ai.keyNotSet": "尚未配置 API 密钥",
  // Misc common
  "misc.loading": "加载中…",
  "misc.noAccounts": "尚未接入任何邮箱",
  "misc.noAccountsSub":
    "点击右上角「添加账户」，接入 Gmail、Outlook、QQ、163 或任意 IMAP 邮箱。",
  "misc.lastSync": "上次同步",
  "misc.notSynced": "尚未同步",
  "misc.confirmDelete": "确认删除该邮箱账户？相关邮件记录将一并移除。",
  "misc.encryptionWarn":
    "未配置加密密钥。请在 backend/.env 设置 CREDENTIAL_ENCRYPTION_KEY 后重启。",
  "misc.noSubject": "（无主题）",
  "misc.noSnippet": "（无正文预览）",
  // Account list
  "account.syncFailed": "同步失败：",
  "account.imported": "已导入",
  "account.count": "封",
  // Mailbox
  "mailbox.empty": "收件箱为空",
  "mailbox.emptySub":
    "先在「账户」页同步邮件，或调整筛选条件。多平台邮件会以统一格式汇总于此。",
  "mailbox.searchPlaceholder": "搜索邮件…",
  "mailbox.allAccounts": "全部账户",
  "mailbox.unread": "未读",
  "mailbox.all": "全部",
  "mailbox.priorityHigh": "高优先级",
  "mailbox.priorityMedium": "中优先级",
  "mailbox.priorityLow": "低优先级",
  "mailbox.selectAll": "全选",
  "mailbox.markRead": "标记已读",
  "mailbox.markUnread": "标记未读",
  // Ads
  "ads.title": "广告邮件",
  "ads.blockSender": "屏蔽发件人",
  "ads.unsubscribe": "退订",
  "ads.totalAds": "广告邮件总数",
  "ads.blockedSenders": "已屏蔽发件人",
  "ads.categoryDist": "分类分布",
  "ads.selectAll": "全选（",
  "ads.batchDelete": "批量删除",
  "ads.batchMarkRead": "批量标记已读",
  "ads.empty": "暂无广告邮件",
  "ads.emptySub":
    "AI 分析完成并标记为广告的邮件会汇总到这里，可批量清理或屏蔽发件人。",
  "ads.unsubOpened": "已在新窗口打开退订链接",
  "ads.unsubTriggered": "已触发邮件退订",
  "ads.unsubNotFound": "该邮件未提供退订链接",
  "ads.unblocked": "已解除屏蔽",
  "ads.blockedEmpty": "尚未屏蔽任何发件人",
  "ads.blockedEmptySub":
    "在广告邮件列表点击「屏蔽发件人」，被屏蔽的发件人会在此列出。",
  "ads.affected": "已",
  // Report
  "report.total": "邮件总数",
  "report.unread": "未读数",
  "report.ads": "广告数",
  "report.pending": "待回复",
  "report.empty": "该时间范围内暂无邮件",
  "report.emptySub": "切换时间范围或账户后查看收件统计。",
  "report.categoryDist": "分类分布",
  "report.dailyTrend": "每日趋势",
  "report.noData": "暂无数据",
  "report.topSenders": "发件人排行",
  "report.priorityDist": "优先级分布",
  "report.actionDist": "建议操作分布",
  "report.range.day": "今天",
  "report.range.week": "近 7 天",
  "report.range.month": "近 30 天",
  "report.allAccounts": "全部账户",
  // Priorities
  "priority.reply": "需要回复",
  "priority.review": "需要查看",
  "priority.notice": "仅需知晓",
  "priority.none": "无需处理",
  // Reply tracking
  "replies.inbox": "收件",
  "replies.sent": "已发送",
  "replies.noAccount": "请先添加邮箱账户",
  "replies.selectAccount": "请选择具体账户后再同步",
  "replies.syncFailed": "同步失败：",
  "replies.syncPartial": "部分账户同步失败：导入",
  "replies.syncDone": "同步完成：导入",
  "replies.syncing": "同步中…",
  "replies.syncAll": "同步全部账户已发送邮件",
  "replies.syncOne": "同步已发送邮件",
  "replies.allAccounts": "全部账户",
  "replies.pending": "待回复",
  "replies.currentFilter": "当前筛选",
  "replies.syncedAccounts": "已同步账户",
  "replies.confirmSyncAll": "将对每个账户依次同步",
  "replies.confirmSyncAllSub": "导入已发送邮件并匹配回复关系",
  "replies.empty": "暂无待回复邮件",
  "replies.emptySub":
    "所有需要回复的邮件都已处理完毕，或尚未同步已发送邮件。点击「同步已发送邮件」可重新匹配回复关系。",
  "replies.loadingThread": "加载线程中…",
  "replies.threadNotFound": "未找到该邮件的线程信息。",
  "replies.threadEmpty": "暂无回复记录 — 这封邮件尚未收到已发送的回复。",
  "replies.threadTitle": "会话线程",
  // Add account modal
  "modal.title": "添加邮箱账户",
  "modal.platform": "邮箱平台",
  "modal.oauthSupported": "（支持 OAuth2）",
  "modal.email": "邮箱地址",
  "modal.account": "账号",
  "modal.appPassword": "授权码 / 应用专用密码",
  "modal.appPasswordHint": "非登录密码，需在邮箱后台开启 IMAP 后生成",
  "modal.appPasswordHelp":
    "Gmail / QQ / 163 等均需使用「应用专用密码」或「授权码」，而非账号登录密码。",
  "modal.displayName": "显示名称（可选）",
  "modal.displayNamePlaceholder": "工作邮箱",
  "modal.imapServer": "IMAP 服务器",
  "modal.port": "端口",
  "modal.imapPreset": "· IMAP（已预置）",
  "modal.useOAuth": "改用 OAuth2 授权（Gmail / Outlook）",
  "modal.cancel": "取消",
  "modal.saving": "验证并保存中…",
  "modal.save": "验证并保存",
  // Import dialog
  "import.title": "导入历史邮件",
  "import.account": "账户：",
  "import.checking": "正在检查已有导入任务…",
  "import.selectRange": "选择导入范围",
  "import.days": "天",
  "import.custom": "自定义",
  "import.startDate": "起始日期（导入该日至今的邮件）",
  "import.asyncHint":
    "导入在后台异步执行，可关闭此弹窗后继续使用应用；进度可在账户卡片查看。",
  "import.largeRangeHint": "大范围导入（如 90 天）可能需要数分钟。",
  "import.rangeRecent": "范围：最近",
  "import.imported": "已导入",
  "import.cancelled": "已取消导入，已导入",
  "import.cancel": "取消",
  "import.later": "稍后再说",
  "import.starting": "启动中…",
  "import.start": "开始导入",
  "import.cancelImport": "取消导入",
  "import.complete": "完成",
  "import.background": "后台运行",
  "import.errorNoDate": "请选择起始日期。",
  "import.sincePrefix": "（自",
  "import.sinceSuffix": "起）",
  "import.viewInInbox": "，可在收件箱查看。",
  // Missing misc
  "misc.close": "关闭",
  "misc.unknownSender": "（未知发件人）",
  "misc.unknown": "（未知）",
  // Missing modal
  "modal.errorEmptyFields": "请填写邮箱地址与授权码 / 应用专用密码。",
  "modal.errorCustomImap": "自定义 IMAP 需要填写收件服务器地址。",
  // Missing mailbox
  "mailbox.loadMorePrefix": "加载更多（剩余 ",
  "mailbox.loadMoreSuffix": " 封）",
  "mailbox.ad": "广告",
  // Missing ads
  "ads.alreadyBlocked": "该发件人已被屏蔽",
  "ads.noCategoryData": "暂无分类数据",
  "ads.accountId": "账户 ID: ",
  "ads.blockedSince": "屏蔽于",
  "ads.removeBlock": "移除屏蔽",
  "ads.confirmBatchDelete": "确认删除所有选中广告邮件？此操作不可撤销。",
  // Missing report
  "report.dateRange": "至",
  // Missing replies
  "replies.account": "账户",
  "replies.viewThread": "查看线程 ›",
  "replies.threadTitlePrefix": "会话线程 · 共 ",
  "replies.threadTitleSuffix": " 封",
  "replies.recipients": "收件人",
  // Import status
  "import.status.pending": "等待中",
  "import.status.running": "导入中",
  "import.status.completed": "已完成",
  "import.status.failed": "失败",
  "import.status.cancelled": "已取消",
  "import.counting": "正在统计邮件数量…",
  // Detail panel
  "detail.loading": "加载中…",
  "detail.loadError": "无法加载邮件详情。",
  "detail.sender": "发件人",
  "detail.email": "邮箱",
  "detail.platform": "平台",
  "detail.time": "时间",
  "detail.recipients": "收件人",
  "detail.bodyPreview": "邮件正文预览",
  "detail.aiAnalysis": "AI 分析",
  "detail.category": "分类",
  "detail.priority": "优先级",
  "detail.ad": "广告",
  "detail.suggestion": "建议",
  "detail.summary": "摘要",
  "detail.notAnalyzed": "尚未分析。分析结果将在后台 AI 分析完成后显示。",
  "detail.adGovernance": "广告治理",
  "detail.blockSender": "屏蔽发件人",
  "detail.unsubscribe": "一键退订",
  "detail.close": "关闭",
  // Misc yes/no
  "misc.yes": "是",
  "misc.no": "否",
  // Ads extras
  "ads.blockedSender": "已屏蔽",
  "ads.confirmAction": "此操作不可撤销。",
  // Replies extras
  "replies.matched": "，匹配",
  // Dashboard
  "dash.urgent": "紧急",
  "dash.pending": "待处理",
  "dash.unread": "未读",
  "dash.today": "今日",
  "dash.refresh": "刷新",
  "dash.forceSync": "同步邮件并刷新",
  "dash.syncing": "正在同步…",
  "dash.lastUpdated": "更新于",
  "dash.dailyBrief": "今日概要",
  "dash.schedule": "日程安排",
  "dash.priorityQueue": "优先级队列",
  "dash.allDone": "所有邮件已处理完毕",
  "dash.min": "分钟",
  "dash.urgency.high": "紧急",
  "dash.urgency.medium": "中等",
  "dash.urgency.low": "低",
  "dash.type.meeting": "会议",
  "dash.type.deadline": "截止",
  "dash.type.appointment": "预约",
  "dash.type.reminder": "提醒",
  "dash.group.today": "今天",
  "dash.group.tomorrow": "明天",
  "dash.group.this_week": "本周",
  "dash.group.upcoming": "即将到来",
  "dash.handle": "标记已处理",
  "dash.noSchedule": "暂无日程",
};

const en: Dict = {
  "brand.sub": "Email Aggregation",
  "nav.mailbox": "Inbox",
  "nav.accounts": "Accounts",
  "nav.ads": "Ad Management",
  "nav.reports": "Reports",
  "nav.replies": "Reply Tracking",
  "nav.settings": "Settings",
  "title.mailbox": "Unified Inbox",
  "title.accounts": "Email Accounts",
  "title.ads": "Ad Management",
  "title.reports": "Inbox Reports",
  "title.replies": "Reply Tracking",
  "title.settings": "Settings",
  "sub.mailbox":
    "Cross-platform emails in a unified format — protocol differences invisible.",
  "sub.accounts":
    "Connect Gmail, Outlook, QQ, 163, or any IMAP mailbox.",
  "sub.ads":
    "View ad-mail stats, batch-clean, and manage blocked senders.",
  "sub.reports":
    "View receipt stats, category distribution, and sender rankings by range and account.",
  "sub.replies":
    "Sync sent mail, track pending replies and conversation threads.",
  "sub.settings": "Adjust appearance, accent color, and interface language.",
  "cat.all": "All categories",
  "cat.work": "Work",
  "cat.meeting": "Meeting",
  "cat.finance": "Finance",
  "cat.system": "System",
  "cat.social": "Social",
  "cat.travel": "Travel",
  "cat.shopping": "Shopping",
  "cat.ad": "Marketing",
  "cat.newsletter": "Newsletter",
  "cat.personal": "Personal",
  "cat.other": "Other",
  "cat.uncategorized": "Uncategorized",
  "action.addAccount": "+ Add Account",
  "action.sync7": "Sync last 7 days",
  "action.syncing": "Syncing…",
  "action.importHistory": "Import history",
  "action.importing": "Importing…",
  "action.delete": "Delete",
  "action.cancel": "Cancel",
  "action.refresh": "Refresh",
  "action.refreshing": "Refreshing…",
  "action.loading": "Loading…",
  "action.confirm": "Confirm",
  "action.complete": "Done",
  "idle.listening": "Live",
  "idle.stopListening": "Stop",
  "idle.startListening": "Live",
  "idle.stopListeningTitle": "Stop live monitoring",
  "idle.startListeningTitle": "Start live monitoring (IMAP IDLE)",
  "idle.events": "IDLE events",
  "idle.times": "",
  "settings.appearance": "Appearance",
  "settings.theme": "Theme mode",
  "settings.theme.dark": "Dark",
  "settings.theme.light": "Light",
  "settings.accent": "Accent color",
  "settings.language": "Language",
  "settings.language.zh": "中文",
  "settings.language.en": "English",
  // AI analysis
  "settings.ai": "AI Email Analysis",
  "settings.ai.mode": "Analysis mode",
  "settings.ai.mode.auto": "Smart mode",
  "settings.ai.mode.autoDesc": "Use AI when configured; fall back to rules on failure or when not set",
  "settings.ai.mode.ai_only": "AI only",
  "settings.ai.mode.ai_onlyDesc": "Always call the AI, never use rules",
  "settings.ai.mode.rules_only": "Rules only",
  "settings.ai.mode.rules_onlyDesc": "No AI at all — pure programmatic analysis (zero cost)",
  "settings.ai.provider": "Provider",
  "settings.ai.provider.env": "Environment (.env)",
  "settings.ai.provider.openai": "OpenAI",
  "settings.ai.provider.deepseek": "DeepSeek",
  "settings.ai.provider.moonshot": "Moonshot (Kimi)",
  "settings.ai.provider.qwen": "Qwen (DashScope)",
  "settings.ai.provider.glm": "Zhipu GLM",
  "settings.ai.provider.ollama": "Ollama (local)",
  "settings.ai.provider.anthropic": "Anthropic Claude",
  "settings.ai.provider.custom": "Custom (OpenAI-compatible)",
  "settings.ai.providerHint": "Picking a preset fills the URL; for Ollama, install it locally and pull a model first",
  "settings.ai.baseUrl": "Base URL",
  "settings.ai.model": "Model",
  "settings.ai.apiKey": "API Key",
  "settings.ai.apiKeyHint": "The key is encrypted and stored in the database; leave blank to keep the current one, or use AI_API_KEY in .env instead",
  "settings.ai.clearKey": "Clear the stored key",
  "settings.ai.save": "Save AI settings",
  "settings.ai.saved": "Saved",
  "settings.ai.saveFailed": "Save failed: ",
  "settings.ai.status": "Current status",
  "settings.ai.active": "AI analysis is enabled",
  "settings.ai.activeMode": "Current mode",
  "settings.ai.inactive": "Using rule-based analysis (AI not configured)",
  "settings.ai.keyFromEnv": "Key from .env environment",
  "settings.ai.keyFromDb": "Key stored via UI settings",
  "settings.ai.keyNotSet": "No API key configured",
  "misc.loading": "Loading…",
  "misc.noAccounts": "No email accounts connected",
  "misc.noAccountsSub":
    'Click "Add Account" to connect Gmail, Outlook, QQ, 163, or any IMAP mailbox.',
  "misc.lastSync": "Last sync",
  "misc.notSynced": "Not synced yet",
  "misc.confirmDelete":
    "Delete this email account? All related email records will be removed.",
  "misc.encryptionWarn":
    "Encryption key not configured. Set CREDENTIAL_ENCRYPTION_KEY in backend/.env and restart.",
  "misc.noSubject": "(no subject)",
  "misc.noSnippet": "(no preview)",
  "account.syncFailed": "Sync failed: ",
  "account.imported": "Imported",
  "account.count": "",
  "mailbox.empty": "Inbox is empty",
  "mailbox.emptySub":
    'Sync mail on the Accounts page first, or adjust filters. Cross-platform mail is aggregated here in a unified format.',
  "mailbox.searchPlaceholder": "Search mail…",
  "mailbox.allAccounts": "All accounts",
  "mailbox.unread": "Unread",
  "mailbox.all": "All",
  "mailbox.priorityHigh": "High priority",
  "mailbox.priorityMedium": "Medium priority",
  "mailbox.priorityLow": "Low priority",
  "mailbox.selectAll": "Select all",
  "mailbox.markRead": "Mark read",
  "mailbox.markUnread": "Mark unread",
  "ads.title": "Ad mail",
  "ads.blockSender": "Block sender",
  "ads.unsubscribe": "Unsubscribe",
  "ads.totalAds": "Total ad mail",
  "ads.blockedSenders": "Blocked senders",
  "ads.categoryDist": "Category distribution",
  "ads.selectAll": "Select all (",
  "ads.batchDelete": "Batch delete",
  "ads.batchMarkRead": "Batch mark read",
  "ads.empty": "No ad mail",
  "ads.emptySub":
    "Mail flagged as ads by AI analysis appears here. Batch-clean or block senders.",
  "ads.unsubOpened": "Unsubscribe link opened in a new window",
  "ads.unsubTriggered": "Unsubscribe email triggered",
  "ads.unsubNotFound": "This mail has no unsubscribe link",
  "ads.unblocked": "Unblocked",
  "ads.blockedEmpty": "No blocked senders",
  "ads.blockedEmptySub":
    'Click "Block sender" on an ad mail to add the sender here.',
  "ads.affected": "",
  "report.total": "Total mail",
  "report.unread": "Unread",
  "report.ads": "Ads",
  "report.pending": "Pending reply",
  "report.empty": "No mail in this time range",
  "report.emptySub": "Switch the time range or account to view stats.",
  "report.categoryDist": "Category distribution",
  "report.dailyTrend": "Daily trend",
  "report.noData": "No data",
  "report.topSenders": "Top senders",
  "report.priorityDist": "Priority distribution",
  "report.actionDist": "Action distribution",
  "report.range.day": "Today",
  "report.range.week": "Last 7 days",
  "report.range.month": "Last 30 days",
  "report.allAccounts": "All accounts",
  "priority.reply": "Needs reply",
  "priority.review": "Needs review",
  "priority.notice": "FYI",
  "priority.none": "No action",
  "replies.inbox": "Inbox",
  "replies.sent": "Sent",
  "replies.noAccount": "Please add an email account first",
  "replies.selectAccount": "Please select an account to sync",
  "replies.syncFailed": "Sync failed: ",
  "replies.syncPartial": "Some accounts failed: imported ",
  "replies.syncDone": "Sync complete: imported ",
  "replies.syncing": "Syncing…",
  "replies.syncAll": "Sync sent mail for all accounts",
  "replies.syncOne": "Sync sent mail",
  "replies.allAccounts": "All accounts",
  "replies.pending": "Pending reply",
  "replies.currentFilter": "Current filter",
  "replies.syncedAccounts": "Synced accounts",
  "replies.confirmSyncAll": "Will sync each account in sequence:",
  "replies.confirmSyncAllSub": "Import sent mail and match reply relationships",
  "replies.empty": "No pending replies",
  "replies.emptySub":
    'All mails needing a reply are handled, or sent mail has not been synced yet. Click "Sync sent mail" to re-match.',
  "replies.loadingThread": "Loading thread…",
  "replies.threadNotFound": "Thread information not found for this mail.",
  "replies.threadEmpty": "No replies yet — no sent reply has been matched to this mail.",
  "replies.threadTitle": "Conversation thread",
  "modal.title": "Add Email Account",
  "modal.platform": "Platform",
  "modal.oauthSupported": "(OAuth2 supported)",
  "modal.email": "Email address",
  "modal.account": "Account",
  "modal.appPassword": "App password / Authorization code",
  "modal.appPasswordHint":
    "Not your login password — generate it after enabling IMAP in your mailbox settings",
  "modal.appPasswordHelp":
    'Gmail / QQ / 163 etc. require an "app-specific password" or "authorization code", not your login password.',
  "modal.displayName": "Display name (optional)",
  "modal.displayNamePlaceholder": "Work email",
  "modal.imapServer": "IMAP server",
  "modal.port": "Port",
  "modal.imapPreset": "· IMAP (preset)",
  "modal.useOAuth": "Use OAuth2 instead (Gmail / Outlook)",
  "modal.cancel": "Cancel",
  "modal.saving": "Validating & saving…",
  "modal.save": "Validate & save",
  "import.title": "Import Historical Mail",
  "import.account": "Account: ",
  "import.checking": "Checking existing import jobs…",
  "import.selectRange": "Select import range",
  "import.days": "days",
  "import.custom": "Custom",
  "import.startDate": "Start date (import mail from this date to today)",
  "import.asyncHint":
    "Import runs in the background. You can close this dialog and continue using the app. Progress is shown on the account card.",
  "import.largeRangeHint": "Large-range imports (e.g. 90 days) may take several minutes.",
  "import.rangeRecent": "Range: last ",
  "import.imported": "Imported",
  "import.cancelled": "Import cancelled. Imported ",
  "import.cancel": "Cancel",
  "import.later": "Later",
  "import.starting": "Starting…",
  "import.start": "Start import",
  "import.cancelImport": "Cancel import",
  "import.complete": "Done",
  "import.background": "Background",
  "import.errorNoDate": "Please select a start date.",
  "import.sincePrefix": "(since ",
  "import.sinceSuffix": ")",
  "import.viewInInbox": ", viewable in inbox.",
  "misc.close": "Close",
  "misc.unknownSender": "(unknown sender)",
  "misc.unknown": "(unknown)",
  "modal.errorEmptyFields": "Please fill in email and app password / authorization code.",
  "modal.errorCustomImap": "Custom IMAP requires the receiving server address.",
  "mailbox.loadMorePrefix": "Load more (",
  "mailbox.loadMoreSuffix": " remaining)",
  "mailbox.ad": "Ad",
  "ads.alreadyBlocked": "This sender is already blocked",
  "ads.noCategoryData": "No category data",
  "ads.accountId": "Account ID: ",
  "ads.blockedSince": "Blocked since",
  "ads.removeBlock": "Remove block",
  "ads.confirmBatchDelete": "Delete all selected ad mail? This cannot be undone.",
  "report.dateRange": "to",
  "replies.account": "Account",
  "replies.viewThread": "View thread ›",
  "replies.threadTitlePrefix": "Thread · ",
  "replies.threadTitleSuffix": " messages",
  "replies.recipients": "Recipients",
  "import.status.pending": "Pending",
  "import.status.running": "Importing",
  "import.status.completed": "Completed",
  "import.status.failed": "Failed",
  "import.status.cancelled": "Cancelled",
  "import.counting": "Counting messages…",
  "detail.loading": "Loading…",
  "detail.loadError": "Failed to load email detail.",
  "detail.sender": "Sender",
  "detail.email": "Email",
  "detail.platform": "Platform",
  "detail.time": "Time",
  "detail.recipients": "Recipients",
  "detail.bodyPreview": "Body preview",
  "detail.aiAnalysis": "AI Analysis",
  "detail.category": "Category",
  "detail.priority": "Priority",
  "detail.ad": "Ad",
  "detail.suggestion": "Suggestion",
  "detail.summary": "Summary",
  "detail.notAnalyzed": "Not yet analyzed. Results will appear after AI analysis completes.",
  "detail.adGovernance": "Ad governance",
  "detail.blockSender": "Block sender",
  "detail.unsubscribe": "Unsubscribe",
  "detail.close": "Close",
  "misc.yes": "Yes",
  "misc.no": "No",
  "ads.blockedSender": "Blocked",
  "ads.confirmAction": "This cannot be undone.",
  "replies.matched": ", matched",
  // Dashboard
  "dash.urgent": "Urgent",
  "dash.pending": "Pending",
  "dash.unread": "Unread",
  "dash.today": "Today",
  "dash.refresh": "Refresh",
  "dash.forceSync": "Sync mail & refresh",
  "dash.syncing": "Syncing…",
  "dash.lastUpdated": "Updated",
  "dash.dailyBrief": "Daily brief",
  "dash.schedule": "Schedule",
  "dash.priorityQueue": "Priority queue",
  "dash.allDone": "All emails handled",
  "dash.min": "min",
  "dash.urgency.high": "High",
  "dash.urgency.medium": "Medium",
  "dash.urgency.low": "Low",
  "dash.type.meeting": "Meeting",
  "dash.type.deadline": "Deadline",
  "dash.type.appointment": "Appointment",
  "dash.type.reminder": "Reminder",
  "dash.group.today": "Today",
  "dash.group.tomorrow": "Tomorrow",
  "dash.group.this_week": "This week",
  "dash.group.upcoming": "Upcoming",
  "dash.handle": "Mark handled",
  "dash.noSchedule": "No schedule items",
};

const DICTS: Record<Lang, Dict> = { zh, en };

// ---------------- Context ----------------

interface I18nContextValue {
  lang: Lang;
  theme: ThemeMode;
  accent: AccentColor;
  setLang: (l: Lang) => void;
  setTheme: (t: ThemeMode) => void;
  setAccent: (a: AccentColor) => void;
  t: (key: string) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

const STORAGE_KEY = "emailui-settings";

function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        lang: parsed.lang ?? "zh",
        theme: parsed.theme ?? "dark",
        accent: parsed.accent ?? "amber",
      };
    }
  } catch {
    /* ignore */
  }
  return { lang: "zh", theme: "dark", accent: "amber" };
}

function saveSettings(s: Settings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

function applyTheme(theme: ThemeMode, accent: AccentColor) {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-accent", accent);
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(loadSettings);

  useEffect(() => {
    applyTheme(settings.theme, settings.accent);
    saveSettings(settings);
  }, [settings]);

  const value: I18nContextValue = {
    lang: settings.lang,
    theme: settings.theme,
    accent: settings.accent,
    setLang: (lang) => setSettings((s) => ({ ...s, lang })),
    setTheme: (theme) => setSettings((s) => ({ ...s, theme })),
    setAccent: (accent) => setSettings((s) => ({ ...s, accent })),
    t: (key: string) => DICTS[settings.lang][key] ?? key,
  };

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
