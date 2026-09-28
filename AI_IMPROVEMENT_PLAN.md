# Mailigence AI 准确率改进计划

> 目标：解决邮件属性分析（分类/广告识别/优先级/摘要）与智能功能（问答/搜索）正确率差的问题。
> 方法论来源：Inbox Zero、Mail-0/Zero、Dify 官方邮件模板、LangChain/LangMem 的真实源码级调研（2026-09-28）。
> 原则：每一步独立可验证、可回滚；不新建 Python 环境；改动集中在 backend。

---

## 0. 现状根因（代码级证据）

| # | 根因 | 位置 |
|---|------|------|
| 1 | LLM 只见正文前 500 字符（`SNIPPET_MAX=500` 持久化，分析层再截 800 无意义）；QA 片段仅 200 字符 | `imap_client.py:39`、`ai_analyzer.py:163`、`chat_qa_service.py:35` |
| 2 | `raw_headers`（List-Unsubscribe / Precedence 等广告铁证）只喂给规则兜底，LLM 路径收不到 | `ai_analyzer.py:155-170`（`_analyze_with_llm` 签名无 headers） |
| 3 | 系统提示词约 20 行：无 few-shot、无负面示例、11 个类别零定义 | `ai_analyzer.py:51-88` |
| 4 | 用户在 UI 改分类/标广告后无任何记录，模型永远不会变聪明 | `api/emails.py` 无反馈端点 |
| 5 | 输出无 confidence，无法低置信兜底；`json.loads` 失败即整次作废 | `ai_analyzer.py:172` |
| 6 | 单阶段：无确定性预处理、无二次校验 | `analysis_service.py` |
| 7 | 每封邮件仅一个 1000 字符向量；检索只有 pg_trgm / 单向量 | `embedding_service.py:123-132` |

---

## P0 提示词与输入升级 ✅ 已实施（2026-09-28）

> 本阶段专门适配了本地 8B 小模型 / 最低配置（见 P0-5）。

### P0-1 评测基线 ✅
- `backend/tests/test_analyzer_eval.py`：30 封陷阱邮件 + 4 个解析层单元测试
- 规则基线实测：category 0.80 / 广告 F1 0.62（precision 仅 0.44——退订页脚误报是主要失分点）
- LLM 实测（可选）：`cd backend && RUN_AI_EVAL=1 python -m pytest tests/test_analyzer_eval.py -s`

### P0-2 提示词 v2 ✅
- 云端完整版：reasoning 前置 + confidence + 11 类 usecase 定义与反例 + 4 组 few-shot（含"订单带退订页脚非广告""账单含优惠字样"等陷阱示例）
- 输出 schema：`{reasoning, category, confidence, is_advertisement, priority_score, summary, suggested_action}`
- 动态类别约束：confidence<0.7 时不允许新建类别，回落 other（"unknown 必须罕见"）

### P0-3 输入升级 ✅
- user prompt 结构化：`发件人 / 批量信号头(List-Unsubscribe、Precedence、Auto-Submitted) / 主题 / 正文`
- 正文预算：云端 2500 字符、本地 1200 字符，`AI_MAX_BODY_CHARS` 可覆盖
- `analyze_email()` 新增 `sender` 参数，调用方 `analysis_service.py` 已传入

### P0-4 JSON 容错 ✅
- `_extract_json`：剥代码围栏、跳过前后缀文本、字符串感知的配平括号、尾逗号修复
- 解析失败自动重试一次（追加"只输出 JSON"指令），仍失败才降级规则路径
- 缺字段默认值兜底（category→other、priority→50、action→note）

### P0-5 本地 8B / 低配置适配 ✅
- Ollama/LM Studio 端点自动切换**精简提示词**（无 reasoning 字段、单行类别定义、1 组 few-shot，约 300 token，4k 上下文可容纳）
- `AI_NUM_CTX` 可显式设置 Ollama 上下文窗口（防止长 prompt 被静默截断后返回乱码 JSON）
- 本地模型正文自动减半（1200 字符），`think:false` 保持
- 所有解析均有默认值，弱模型部分字段缺失不会让分析作废

---

## P1 反馈闭环（模型随使用变准）✅ 已实施（2026-09-28）

1. **新表** `classification_feedback`（`models/classification_feedback.py`，`create_all` 自动建表）：
   `id, email_id(FK), sender, sender_email(索引,查询键), subject, was_category, corrected_category, was_ad, corrected_ad, created_at`
2. **API**：`PATCH /api/emails/{id}/classification`（body: `category` / `is_advertisement`）——写 email + upsert feedback（同一 email 以最后一次为准）；随后把**该发件人**未处理（未读未归档）邮件 `analyzed_at` 重置回分析队列并启动 AnalysisManager（上限 50 封）。
3. **注入 few-shot**（Inbox Zero 手法）：`analysis_service.load_sender_feedback()` 按 `sender_email` 查最近 5 条 → `analyze_email(feedback=...)` → `_render_feedback_block` 渲染进系统提示（云端完整版/本地精简版双档）。
4. **反馈优先**：有 feedback 时跳过 P2 确定性快判，人工修正永远优先于硬规则。
5. 低置信(<0.5)统计进批量分析返回值 `low_confidence`（AnalysisManager 状态可轮询），UI 详情页提供分类下拉修正 + 广告标记切换。

**验收（实测）**：`test_llm_feedback_follows`——无反馈时 qwen3.5:9b 将"【服务通报】9月服务使用简报"判为 newsletter，注入同发件人修正（→work）后跟随为 work，2/2 生效；PATCH 全链路（落库+重排队+重分析）验证通过。

---

## P2 两阶段确定性管线（省钱 + 稳定）✅ 已实施（2026-09-28）

1. **确定性快判**（`ai_analyzer._deterministic_result`，LLM 调用前，`AI_SKIP_DETERMINISTIC` 可关，默认开）：
   - 主题含明确促销词（优惠/限时/sale/deals…）**且**批量证据（批量发件人 local-part / List-Unsubscribe / Precedence: bulk）**且**无交易词（订单/账单/运单…否决）→ `marketing, conf 0.9`
   - 验证码关键词 + 4-8 位数字 → `notification, conf 0.9`
   - "Invitation:" / "Updated invitation:" 主题前缀 → `meeting, conf 0.95`
   - 陷阱防护：交易邮件（no-reply@ + 退订页脚）被交易词否决，绝不快判为广告——精度优先，快判结果模型无法申诉。
2. 非跳过场景把确定性证据作为 `确定性信号提示：…` 一行注入 prompt（`_deterministic_hints`），LLM 仍拥有最终裁决权。

### 评测集并入 P3 提分项 ✅（同日）

- QA 片段 200→600 字符、RETRIEVAL_LIMIT 15→12（`chat_qa_service.py`；三路 RRF 融合 `email_search.py` 此前已实现）
- 日程分析 body 300→800（`schedule_analyzer.py`）
- embedding 输入 1000→2000（`embedding_service.py`）

**实测对比（本地基准 qwen3.5:9b，30 封陷阱集）**：

| 阶段 | category_acc | 广告 F1 | action_acc | 备注 |
|------|-------------|---------|-----------|------|
| 规则基线 | 0.80 | 0.62 | 1.00 | 退订页脚误报 |
| P0 | 0.93 | 1.00 | 0.87 | 失分：personal→social、ambiguous→social |
| P1+P2 | **0.93** | **1.00** | **0.87** | 无回归；6/30 案例免 LLM（4 促销+验证码+日历），feedback 闭环实测生效 |

---

## P3 存储与检索升级（需要 DB 迁移，最后做）

> 剩余项：body_text 全文存储（较重，暂缓，待确认）与 `ai_max_body_chars` 生效于 body_text。
> 已并入 P2 实施：QA 片段/检索数、schedule 正文、embedding 输入（见上）。

---

## 实施顺序与工作量

| 步骤 | 文件 | 依赖 | 可独立验收 |
|------|------|------|-----------|
| 1. 评测集 | `tests/test_analyzer_eval.py`（新） | 无 | 出基线分 |
| 2. P0 提示词+JSON+headers | `ai_analyzer.py`、`analysis_service.py` | 1 | 评测分对比 |
| 3. P1 反馈闭环 | `models/`、`api/emails.py`、`ai_analyzer.py`、alembic | 2 | 修正→跟随验证 |
| 4. P2 确定性管线 | `analysis_service.py`、`ai_config.py` | 2 | 统计 LLM 调用下降 |
| 5. P3 存储+QA | `imap_client.py`、`ms_graph.py`、`chat_qa_service.py`、迁移 | 2 | QA 引用质量 |

**风险与对策**
- 本地小上下文模型（Ollama）：自适应截断 + `think:false` 保持不变。
- JSON mode 兼容性：部分 OpenAI 兼容网关不支持 `response_format` → 容错解析兜底（现状已如此）。
- 全量重分析成本：P0 上线后提示词变长约 3~4 倍 token，批量重分析前先小批量（50 封）试跑估费。
- 隐私：`store_full_body` 默认关闭，UI 明示。

## 效果预期（基于调研项目公开数据）
- Dify 模板：生产环境 85% 自动路由率（同等手法：分类器+验证器+置信度阈值）
- Inbox Zero：发件人 few-shot + 两阶段是其准确率核心，我们 P0+P1 即可覆盖这两项。
