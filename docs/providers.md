# Verified provider contracts and deployment boundaries

Checked 2026-10-24. Documentation checks and local mocks do not establish authenticated integration, hosted deployment or publication. No paid model requests, app purchases, account changes or cron installs were performed.

## Hermes Cloud and runtime

Use the [official Hermes Cloud portal](https://portal.nousresearch.com/cloud). Select Small and verify its deployment-screen memory/CPU and current charge before provisioning. The requested target is 1 GB RAM and 2 vCPU. The public pricing page currently sends exact sizes/prices to the authenticated portal; do not promote older indexed Small prices to a current quote. Record the actual daily running and stopped-storage prices in configuration and the ledger. Billing arrives in arrears, potentially 1–2 days later; model/tool costs are separate. Unknown bills remain pending. A $10 experiment target is not a provider-enforced spending guarantee.

Cloud preserves the workspace between sessions. Put the checkout, configuration, SQLite state and reports in the instance's persistent workspace, then restart once and verify the same experiment and post IDs. This is a deployment acceptance test, not a result established on this Mac. Keep one worker process and avoid continuous browsers. Measure resident memory during an actual hosted smoke test; do not silently upgrade the instance if Small is insufficient.

Local read-only inspection found `Hermes Agent v0.18.0 (2026.7.1) · upstream 1c473bc6 · local 7203898c (+1 carried commit)`, using Python 3.11.15. `hermes cron create --help` supports `--no-agent`, `--script`, `--deliver`, `--name`, `--repeat` and `--workdir`. That installed version does **not** expose the latest documented cron `--model`, `--provider`, `--paused` or chat `--format` / `--query-file` flags. The cloud version remains unverified. The worker uses Python 3.12+ independently of Hermes's interpreter.

[Script-only cron](https://hermes-agent.nousresearch.com/docs/guides/cron-script-only) runs scripts under the Hermes scripts directory. The scheduler does not start a Hermes LLM session, and provider credentials are not automatically inherited. The daily script can still run the explicitly enabled four-stage Hermes route and can call Clone within its verified sandbox allowance; no-agent is not a guarantee that every API called by a script is free. `scripts/hermes-install.sh` validates the actual CLI before installing. It defaults to `--check` and creates nothing. Installation requires an explicit hosted-instance acknowledgement and an exact expected version line. It creates six local-delivery no-agent jobs: daily orchestration (24h), metrics (8h), reconciliation (4h), watchdog (1h), reference report (24h), and Telegram monitoring (5m). Daily orchestration calls the route in research → planning → writing → execution order before normal draft handling. Telegram has a dedicated bounded Bot API poller; local scheduler delivery prevents forwarding the same summary through Hermes again. All jobs invoke this worker's fixed CLI commands; none execute a model's text. Run the first jobs manually after recording experiment start because interval jobs wait until their first interval.

```sh
bash scripts/hermes-install.sh --check
# On the hosted instance, after reviewing its help output and persistent paths:
export GTM_HOSTED_INSTANCE_CONFIRMED=yes
export GTM_EXPECTED_HERMES_VERSION='EXACT reviewed first line from hermes --version'
export GTM_PYTHON="$PWD/.venv/bin/python"
bash scripts/hermes-install.sh --install
hermes cron list
hermes cron status
```

Keep the returned IDs. Use the documented `hermes cron pause JOB_ID`, `resume JOB_ID`, `run JOB_ID` and `remove JOB_ID` commands after checking that version's help. A partial install can leave some jobs: inspect and remove only the IDs created for this experiment, then remove only its six wrappers before reinstalling. The installer refuses an existing experiment name to prevent duplicates. It never edits Hermes's cron database directly. Gateway/scheduler availability on Cloud must be verified from the hosted dashboard and `hermes cron status`.

Store API secrets as literal, unquoted `KEY=value` lines in `state/runtime.env`, chmod 600. The wrapper accepts only documented worker credential names, including `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `TELEGRAM_USER_ID`, and does not execute the file as shell. Do not put model tokens there; Hermes manages its configured provider credentials. Do not print the file. For renewable GA4 credentials, install `requirements-live.txt`, set `GOOGLE_APPLICATION_CREDENTIALS` to a private service-account JSON file, and grant its principal read access to the configured property. The CLI selects the lazy refreshing service-account adapter; credentials and API requests remain unverified until an authenticated Cloud collection succeeds. A manually supplied `GA4_ACCESS_TOKEN` remains an alternative but expires and needs replacement. Credential errors produce missing analytics and stop additional report calls in that collection.

At experiment end the worker refuses new work. Reconcile or cancel external jobs and export final metrics before removing these cron jobs. Ending the experiment does not stop Cloud billing. Use the owner's Cloud instance controls to stop the instance when finished; stopping retains storage charges. No cloud-stop API or automatic cloud deletion has been assumed or implemented.

## Four-stage Hermes model routing

The Cloud worker persists and orders research → planning → writing → execution. The default provider is OpenRouter through Hermes, with pinned `google/gemini-2.5-pro` for research/planning and `google/gemini-2.5-flash-lite` for writing/execution. Research and planning each reserve 40% of the model budget; writing receives 15% and execution 5%. At the configured $5 model allowance these are $2 / $2 / $0.75 / $0.25 lifetime stage limits, with independent per-call caps, input/output bounds, a maximum of two pipelines and eight stage calls per audience day. These are allocation ceilings, not a claim that a model has been authenticated or a current price quote. See [model-routing.md](model-routing.md) for stage contracts and provider references.

Research works from bounded reviewed evidence and stored aggregate observations. Unknown facts stay unknown. Planning produces one constrained angle; writing produces policy-checked candidate copy; execution can return only `review` or `reject`. No stage receives shell, publishing, credential-management, or approval authority. The Cloud host performs final policy, duplicate, authorization, quota and budget checks. Generated copy requires owner review and exact-text approval. The developmental-product disclosure and seven-day nonadvertising policy apply to all routes.

The [current CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) documents structured one-shot output; [`clarify` toolset](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference) is the sole minimal toolset used by the adapter. Each stage pins provider/model, disables customizations/MCP/hooks through safe mode, passes context on stdin and limits the agent to one turn. Any tool use, malformed output, mismatched token usage or nonzero exit is rejected. Model text never becomes a shell command.

The [current provider documentation](https://hermes-agent.nousresearch.com/docs/integrations/providers) removes the old `HERMES_MAX_TOKENS` and `model.max_tokens` controls. Prompting for short output is not a hard cap. Live routing therefore defaults off until the exact hosted runtime, supported CLI flags, provider-side output/spending caps and current billing are verified. `preflight` reports all four configured routes, budgets and local runtime readiness; it does not make model calls or verify provider/model account access. The locally inspected runtime fails the structured-output requirement. Post-response token validation cannot undo charges. Reserve each stage before invoking it; ambiguous results block further paid work and remain pending for reconciliation. A reservation is an upper bound, not a confirmed bill.

```sh
python -m gtm preflight
python -m gtm --state state/gtm.sqlite route
python -m gtm --state state/gtm.sqlite status
```

`route` proposes drafts without publishing or granting approval. `status` generates a local aggregate preview and sends nothing. The seven-day offline demonstration uses `MockStageRunner`, explicit simulated owner approvals and a separate simulation DB; it never authenticates a provider or Telegram account.

## Telegram as the operations surface

Telegram is the primary operator status channel; HTML remains a reference for reviewing content and results. `telegram` is a no-op while `[telegram].enabled=false`. Once enabled, the numeric private chat ID and sender user ID must both match the configured owner. Only `/status`, `/budget`, `/posts`, and `/help` are accepted. Input never reaches a model or shell. Messages show aggregate status, posting OFF/ON, stage completion, budget and unresolved external jobs; they omit draft bodies, personal data, prompts and raw provider errors.

Meaningful transitions, one daily KST summary and experiment completion use a durable outbox. Definitive 429 responses respect `retry_after`; uncertain delivery is never blindly repeated. The five-minute cron has 2017 bounded executions for the 168-hour window and a final check. Runtime expiry stops command polling; it does not stop Cloud billing. Existing webhooks are never removed automatically. Provider credentials, actual Telegram delivery and hosted scheduler behavior remain unverified. See [telegram.md](telegram.md) and [cloud-operations.md](cloud-operations.md) for setup, recovery and hosting acceptance checks.

## Clone sandbox

Contracts were checked directly against the [official API reference](https://clone.is/docs/api-reference), [OpenAPI 3.1 schema](https://clone.is/api-platform/openapi.json) (API version 0.1.0), [reliability rules](https://clone.is/docs/reliability), and [tagged SDK v0.8.0 expiry checks](https://github.com/cloneisyou/clone-sdk/blob/v0.8.0/src/controller.ts). This implementation uses only backend HTTPS at `https://api.clone.is`:

| Method and path | Implemented behavior |
| --- | --- |
| POST `/v1/predictions` | `next_prompt`, empty draft, stable request/session/context identities, agent-origin aggregate context, strict response identity and Unix-seconds expiry checks |
| POST `/v1/prediction-events` | Stable event IDs, text collection off, automated submissions marked `agent`, outcomes only after explicit observation |
| GET `/v1/usage` | Verify sandbox plan, remaining allowance, prediction/reserved units; refuse unknown usage or paid plans |

Create a free sandbox app in the [Clone console](https://clone.is/developer/apps), obtain its server-only app key, and set `CLONE_APP_KEY` and a stable operator-derived `CLONE_USER_ID`. Basic product-context prediction needs no connected Clone user profile. Enable only the sandbox and set an explicit positive monthly prediction-unit limit within the account's remaining allowance. Paid mode is refused and no billing or recharge is enabled. Use a dedicated sandbox app to avoid another process racing its allowance; the worker must serialize requests.

The exact request body deterministically identifies one prediction across restarts. A timeout returns unknown with that same request ID; it is never automatically retried under a new ID. Keep the recorded context/body until reconciliation. HTTP failures, abstention, expiry, truncation, identity mismatch and unsupported suggestions all select the deterministic fallback. Untrusted completions cannot become executable tasks: only one enumerated hypothesis/hook/topic/CTA or HH:MM time is admitted; the host still validates slot bounds and content. Raw free-text next prompts are discarded. No lead contacts are sent. Acceptance by the automation is logged as agent submission, never invented human preference feedback.

External-access blockers: sandbox credentials and actual usage are not verified; live Clone request/feedback round trips were not run. `MockClone` and `MockStageRunner` are labeled simulated and never use a network. Tests must establish offline behavior separately from later authenticated acceptance tests.
