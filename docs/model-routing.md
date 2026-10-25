# Cloud model routing

The Cloud worker follows **research → planning → writing → execution review**. Research and planning receive 80% of the model allowance; compact drafting and a final policy check receive the rest. Actual posting remains a deterministic worker operation after account authorization, content approval, budget/quota checks and platform checks. The execution model cannot publish or authorize a post.

This is the configured design, not evidence of a live deployment. Paid inference remains disabled until the hosted runtime, provider credentials, billing controls and hard output limits pass the existing gates. No paid model request or provider account change was made while preparing this design.

## Routes and resource allocation

| Stage | Hermes provider / model | Seven-day model allowance | Result |
| --- | --- | --- | --- |
| Research | `openrouter` / `google/gemini-2.5-pro` | 40% / $2.00 | Evidence-linked findings, missing information and a candidate angle |
| Planning | `openrouter` / `google/gemini-2.5-pro` | 40% / $2.00 | One hypothesis, hook, angle and supporting evidence IDs |
| Writing | `openrouter` / `google/gemini-2.5-flash-lite` | 15% / $0.75 | At most two slot-specific drafts |
| Execution review | `openrouter` / `google/gemini-2.5-flash-lite` | 5% / $0.25 | A request for human review, or rejection |

These percentages allocate spending ceilings, not an instruction to consume the whole allowance. The total model allowance remains $5 for seven days, inside the approved $20 total operating budget. Infrastructure and other services also count against that total; warnings begin at $14 and the existing optional-work gate stops new paid/publishing work at $18. These application checks do not impose a Cloud provider billing cap or shut down its instance. Stage settings are under `[routing.research]`, `[routing.planning]`, `[routing.writing]` and `[routing.execution]` in [config.toml](../config.toml). Each has an explicit provider/model, `budget_share`, input/output bounds and `max_cost_per_call_usd`. Changing the chat's default model must not silently change these routes.

Two paid jobs per day now mean **two complete pipelines**, each with at most one call per stage: at most eight stage calls per day. No parallel model fan-out or automatic retry is needed for ten planned posts. Failed, ambiguous or unaffordable work must stop instead of recursively delegating or selecting a more expensive fallback. The stage budget and experiment-wide admission checks can stop work before this count is reached.

The selected model IDs and listed text rates were checked against the official OpenRouter catalog on **2026-10-24**: [Gemini 2.5 Pro](https://openrouter.ai/google/gemini-2.5-pro) lists $1.25 input / $10 output per million tokens; [Gemini 2.5 Flash Lite](https://openrouter.ai/google/gemini-2.5-flash-lite) lists $0.10 / $0.40. These rates support this cost/quality choice; they are neither a performance guarantee nor a permanent quote. Recheck availability and the actual endpoint's billing before deployment. The chosen models are stable catalog IDs rather than an automatic “latest” alias.

## What each stage can know and do

Research receives only bounded, supplied evidence and aggregate observations. It can compare sources, identify uncertainty and select a grounded angle. The minimal runner has no search tools: **it does not perform fresh web research**. An operator must supply reviewed source contents and IDs when new external evidence is needed. A source URL alone is not proof of its content. Missing evidence remains missing, and model-generated claims never become source evidence for later stages.

Planning turns the research into a small, testable communication choice. Writing receives the plan and requested slots. It must preserve the development disclosure, `cta="none"`, channel character limits and initial seven-day ban on promotional links, signup/profile prompts, engagement bait and repetitive advertising. Execution review only returns an enumerated `review` or `reject` decision. A successful model check still requires the host's validation and the owner's exact-content approval.

The stage prompts are [research](../prompts/research.md), [planning](../prompts/planning.md), [writing policy](../prompts/content-system.md) and [execution review](../prompts/execution.md). Analytics, comments, source excerpts, Clone suggestions and intermediate model output are untrusted data. They cannot change model routes, privileges, spending ceilings or publication authorization. No model receives lead contacts, account secrets, shell, publisher, messaging, browser, cron or delegation tools.

## Hermes runtime contract

Account prompts now include persona hypotheses, good/bad examples and evidence-gated tone guidance. [Shared feedback rules](../prompts/account-feedback.md) accompany account context in every stage. Examples are never a posting queue. Writing/review input ceilings are 9,000 (using the existing conservative UTF-8 byte admission check); research/planning remain 12,000/10,000. This accommodates explicit examples without truncation. The shared aggregate privacy limit stays 6,000 characters. Models, output limits, call counts, 80/20 spending split, per-call cost caps and USD 20 total remain unchanged; paid routing and publishing remain OFF. Offline fixture tests do not attest real model compliance.

The adapter selects both provider and model on every one-shot invocation. It uses stdin for the prompt, structured JSONL output, safe mode, the minimal `clarify` toolset and one tool-calling iteration. Any tool request is rejected. The host parses the terminal result and validates the application JSON itself; a structured stream is not a guarantee that the generated JSON follows the stage schema. See the official [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) and [toolsets reference](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference).

The inspected local v0.18 runtime lacks the required `--query-file` and `--format` contract. Check the **actual Cloud runtime** before enabling inference; local mock success cannot establish compatibility. Keep exact-version matching and supported-flag checks. Safe mode deliberately ignores user config, rules/memory, plugins, hooks and MCP customizations, so a user-config override is not proof that a cap reaches the provider.

Subprocesses receive an explicit environment before startup. Upload-Post, Telegram, Clone and analytics credentials from the GTM worker are removed; a model invocation may inherit only its selected OpenRouter credential plus required operating-system and reviewed Hermes profile paths. The native skill writer receives no provider credential. This is a process-inheritance boundary, not an OS sandbox: Hermes may still read its own profile, source-checkout or machine-managed `.env` files, even in safe mode. Verify those separate credential sources on Cloud before setting `runtime_contract_verified` or enabling paid routing. Keep publishing and analytics keys outside the Hermes runtime's credential files.

하위 프로세스를 시작하기 전에 환경변수를 제한해, GTM의 게시·Telegram·Clone·분석 키가 모델이나 스킬 실행기에 상속되지 않게 합니다. 다만 Hermes가 자체 프로필·소스 폴더·시스템 관리 `.env`를 읽는 경로까지 차단하는 OS 샌드박스는 아닙니다. Cloud의 별도 자격증명 파일에 게시·분석 키가 섞여 있지 않은지 확인한 뒤에만 `runtime_contract_verified`와 유료 라우팅을 켜야 합니다. 현재 유료 경로는 계속 비활성입니다.

Hermes's current [provider documentation](https://hermes-agent.nousresearch.com/docs/integrations/providers) says legacy `model.max_tokens`, `HERMES_MAX_TOKENS`, provider output-cap settings and `model_overrides.*.*.max_output_tokens` are no longer read. A prompt length request, subprocess timeout or post-response token check cannot prevent an already incurred bill. A valid deployment must demonstrate a provider-enforced output cap for the exact invocation, including reasoning tokens; this is currently unresolved, so the paid path stays blocked.

OpenRouter supports a per-key USD limit with `limit_reset=null` for a non-resetting allowance and a UTC expiry. A dedicated key limited to the experiment allowance is an appropriate deployment control, subject to verified remaining credit and billing behavior. This project does not create or recharge that key. See the official [API-key contract](https://openrouter.ai/docs/api/api-reference/api-keys/create-keys). [Reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) count as paid output, including hidden thinking; visible draft length is not total billed output. Unknown settlement must remain pending in the ledger.

## Cloud operation and Telegram

Persistent SQLite state, scheduling, validation and report generation belong on Cloud. Telegram is the operational status destination; the HTML dashboard and content previews remain useful for inspection and approval without being a running control plane. Routine status formatting does not need another LLM. The [Telegram operations guide](telegram.md) covers the owner-only read commands and notification setup.

The installed jobs use Hermes [script-only cron](https://hermes-agent.nousresearch.com/docs/guides/cron-script-only) with `--deliver local`. A dedicated Bot API client performs Telegram polling and delivery; do not also forward scheduler stdout to Telegram. Check the installed CLI, bot credentials and exact recipient before delivery, and verify receipt separately from job execution. Use deterministic aggregate messages for stage state, blocked work, pending approval, posting outcomes and costs. Do not send raw evidence, lead contacts, credentials or full model transcripts.

Live deployment acceptance still requires an actual Cloud restart/recovery check, provider cap verification, authenticated model responses and a confirmed Telegram delivery. Until then, disabled or blocked statuses are the correct operational report.
