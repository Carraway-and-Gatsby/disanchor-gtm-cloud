# Disanchor: bounded seven-day GTM experiment

An executable Python 3.12 / SQLite worker for ten own-account posts on X and Threads. **Prepared for Cloud deployment; external publishing, paid inference and Telegram delivery remain disabled. Cloud deployment is not complete.** Demo campaign results are simulated. Authenticated read-only account checks, when available, are separate evidence and never establish publication or owner approval.

The user asked for reduced account suspension risk. The system uses official APIs, explicit account/content authorization, one post per platform per audience-local day, duplicate checks, durable quota reservations, account-error circuit breakers, and no unsolicited interaction. It cannot guarantee a platform will never restrict an account. See [posting policy](docs/posting-policy.md) before enabling anything.

The initial seven-day campaign focuses on useful workflow observations and natural questions. Every post weaves Disanchor's current development into the thought; it does not append a stock disclaimer or rotate footer variants. Neither platform's posts contain links, profile prompts, signup/waitlist requests, or sales CTAs; all ten candidates use `cta_type=none`. Organic views and meaningful responses guide early learning; visits and leads remain secondary observations. This is an attempt to earn voluntary sharing, not a promise of virality. A passive, manually configured X profile link can remain for attribution without being promoted in post text. Threads post-link attribution is unavailable in this link-free run. Policy version changes invalidate old publishing authorization; persisted drafts are revalidated before approval/submission and are never silently rewritten.

## Discovery and architecture

The provided workspace was empty: no app framework, landing-page source, database, CI, infrastructure configuration, existing analytics, or integrations could be reused. Public `disanchor.com` was inspected on 2026-10-24: no form was present, and its notice said it was not collecting applications/personal details or using analytics/advertising cookies. Its module bundle does not establish its source framework. No website source or deployment permission was available, so the existing site was not changed.

One Python process owns the SQLite writer lock. A rolling 24-hour queue prepares at most two model drafts per pipeline in research → planning → writing → execution order, with the reviewed seed library available as fallback. Hermes Cloud script-only cron runs the daily coordinator, collector, reconciler, watchdog, reference reporter and Telegram monitor. Telegram is the primary status surface; HTML is an optional review snapshot. Upload-Post performs text scheduling through the documented API. Clone supplies constrained next-prompt proposals; the worker admits a single safe publication-time adjustment and records other changes for review. GA4 supplies aggregate observations. The optional research form stores consented contacts in a separate private SQLite file and exposes no public lead-list API.

Dependencies: standard library for worker, publisher, Clone, state, reporting, mocks and tests. Optional `google-auth[requests]` handles renewable GA4 service-account credentials. Hermes is the hosted scheduler, not a dependency installed by this project. No new orchestration framework, browser worker, paid service or paid model is installed.

## Account strategies and private runtime data

[The account registry](accounts.example.toml) and [seven account prompts](docs/account-strategies.md) separate each profile/platform's approach. The proposed campaign targets are `disanchor_x` for X and `default_threads` for Threads. The other strategies do not increase this experiment's ten-post limit. Instagram and LinkedIn are strategy/draft preparation only; this worker's posting transport supports X and Threads.

The checked-in registry is a review template. Copy it to ignored `accounts.local.toml` on the deployment host and set `[accounts].registry_file` only after the native account IDs and current connection states are checked. `reviewed=true` records identity review, not posting approval. Changes to selected accounts, binding data or account prompts require fresh review; a profile display label is not a native posting ID. See [read-only connection checks](docs/upload-post-connection.md).

The explicitly approved Upload-Post key has been stored in the local owner-only `state/runtime.env`; its contents are not part of source, reports, the deployment ZIP or GitHub. The Cloud instance still needs its own private credential placement. The CLI loads that file only when explicitly passed as `--env-file state/runtime.env`; generated Cloud cron wrappers load the fixed private file. Neither `.env.example` nor the account registry stores credential values.

## Reproduce locally without credentials

```sh
python3.12 -m venv .venv
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m gtm preflight
.venv/bin/python -m gtm preview-library
.venv/bin/python -m gtm account-prompts
.venv/bin/python scripts/cloud-preflight.py
.venv/bin/python -m gtm --output reports/my-dry-run dry-run
.venv/bin/python scripts/build-review.py
.venv/bin/python scripts/package-cloud.py --output reports/disanchor-cloud.zip
```

Use a fresh output directory for each dry run. It never reads provider credentials or uses HTTP. It generates seven mock pipelines with 28 ordered stages, drafts with simulated exact-text approval, ten simulated scheduling decisions, synthetic analytics, a constrained Clone decision, daily reports and final exports. `routing_runs.json` preserves the stage evidence and `telegram-preview.txt` shows an unsent local summary. Mock schedules have no live URL, and the simulated run does not assert that a real post was published. Live and simulated SQLite files cannot be interchanged. Python 3.9 is insufficient; the inspected Mac's Python 3.12 was `/opt/homebrew/bin/python3.12`.

Open [the generated example dashboard](reports/demo/index.html) and [report](reports/demo/final_report.md). CSVs retain observation timestamps/provenance and NULL values. Never add cumulative snapshots across days. Summed native views are not unique reach. Website users are not additive across channels. Cost ratios remain NULL when billing or denominator coverage is incomplete.

The [review index](reports/review-index.html) links source, prompts, tests and evidence. [Content preview](reports/content-preview.html) is regenerated from the current validated seed library and example account strategies without reading runtime credentials or live state. The source-only [Cloud ZIP](reports/disanchor-cloud.zip) excludes all reports, private credentials, local bindings and databases; regenerate it after source changes. [Deployment preparation](docs/cloud-deployment.md) describes the reproducible manifest and CI checks.

## Deploy to Hermes Cloud

1. In the [official Cloud portal](https://portal.nousresearch.com/cloud), verify current Small size (requested 1 GB / 2 vCPU), running cost, stopped-storage cost and billing. This work has not purchased or provisioned an instance. Record the current quote; do not treat the $10 target as a provider spending cap.
2. Transfer this project to the instance's persistent workspace using the owner's available SSH/upload method. Run the local commands above there. No local Mac is required once the hosted gateway and scheduler are verified.
3. Record `hermes --version`, inspect cron help, and run `bash scripts/hermes-install.sh --check`. The inspected Mac version was v0.18.0. Cloud may differ; the installer validates required flags and an exact operator-approved version before creating jobs.
4. Configure `config.toml` and the private `accounts.local.toml` target bindings. Keep posting disabled while testing. Put `state/` at mode 700 and `state/runtime.env` at mode 600; see `.env.example` and [provider guide](docs/providers.md). The local private key is not transferred by a source ZIP. CLI and cron wrappers parse literal `KEY=value` without shell evaluation; never paste secrets into prompts or reports.
5. Verify one writer, persistent SQLite recovery after a Cloud restart, clock/timezone, account identity, webhook-free status reads and available memory. If Small is insufficient, stop and seek an explicit budget decision; no upgrade is automatic.
6. Initialize and start with an explicit offset-bearing timestamp. Start early enough in the audience day for both first-day slots. The database fixes end time to start + 168 real hours, including DST changes.

```sh
.venv/bin/python -m gtm init
.venv/bin/python -m gtm start --at 'REPLACE_WITH_EXPLICIT_ISO_TIMESTAMP_AND_OFFSET'
.venv/bin/python -m gtm inspect
.venv/bin/python -m gtm daily
.venv/bin/python -m gtm collect
.venv/bin/python -m gtm report
```

The initial `daily` creates DRAFTs; publishing is blocked. Setting `auto_publish=true` alone does not authorize posting.

7. Install bounded no-agent cron using the exact reviewed runtime version, **on Cloud only**:

```sh
export GTM_HOSTED_INSTANCE_CONFIRMED=yes
export GTM_EXPECTED_HERMES_VERSION='REPLACE_WITH_EXACT_REVIEWED_VERSION_LINE'
bash scripts/hermes-install.sh --install
hermes cron list
hermes cron status
```

Save the six returned IDs. Run the first daily/collect/report manually; interval jobs wait for their first interval. Telegram uses a separate five-minute polling job with at most one outbound message per tick. No job creates other schedules. Every new submission and model stage checks end_at, even if the cron scheduler wakes late. The watchdog finalizes at its first wake after the boundary; it does not guarantee exact-to-the-second data collection. Provider posts are scheduled only before end_at. After finalization, remove only these recorded cron IDs. Verify unattended execution with the Mac off before claiming deployment complete. See [Cloud operations](docs/cloud-operations.md).

## Connect accounts and enable real publishing

Read [the provider contract](docs/upload-post.md), [connection guide](docs/upload-post-connection.md) and [posting policy](docs/posting-policy.md). Use the existing Upload-Post profiles and confirm each selected platform's native destination ID. The read-only check uses GET requests; it does not connect/disconnect accounts, start OAuth, post, change a plan or grant publishing approval.

```sh
.venv/bin/python -m gtm account-prompts
.venv/bin/python -m gtm --env-file state/runtime.env connection-check
# This prepared checkout also has a private, check-only configuration:
.venv/bin/python -m gtm --config state/connection-check.toml --env-file state/runtime.env connection-check
```

The normalized [connection report](reports/upload-post-connection.json), when present, distinguishes the API profile, native account ID, public handle, reauthentication state and quota evidence. A stored key is not proof of successful authentication. A successful inventory read is not proof that every selected destination is ready; `needs_binding` or `blocked` must be resolved before enabling publishing. Profile-count limits are not upload quotas. Do not place raw provider output or credential values in reports.

The completed local check authenticated successfully and found seven connected profile/platform combinations. It made seven GET requests, matched both selected native destinations (`disanchor_x` on X and `default_threads` on Threads, both `disanchor_com`) and sent zero posts. The private `accounts.local.toml` holds the observed IDs and handles; every entry remains `reviewed=false`. The provider did not return an upload allowance, so remaining quota is unknown. These results establish read-only connectivity, not publication readiness. The private check-only configuration and local bindings are excluded from the source ZIP and GitHub.

Manually configure and verify this X profile link:

`https://disanchor.com/?utm_source=x&utm_medium=organic_social&utm_campaign=disanchor_gtm_7d&utm_content=x_profile`

Review the selected entries in `accounts.local.toml`, confirm their native IDs and mark only those bindings reviewed. Select one target per platform under `[accounts]`; X and Threads may use different Upload-Post profiles. Set `account_reviewed=true`, `x_profile_url`, and a conservative `account_remaining_quota` after deducting all other uploads. A provider-reported smaller allowance wins. If the remaining quota is absent, only the configured allowance can be consumed; default zero blocks everything. The quota is shared by the API account and is not multiplied by the number of profiles. Failed/ambiguous/cancelled jobs retain reservations. Never reset state to regain quota.

`[publishing].account_user` is the local owner-review label repeated in `authorize --account-user`; it does not route a post. The selected registry entries supply each platform's actual Upload-Post profile and native destination. Keep that label, the platform-specific binding review and exact-content approval distinct.

Set `infrastructure_hourly_usd` from actual Cloud billing, `billing_verified=true`, and `reconciled_at` to a recent UTC timestamp only after reviewing accrued/pending charges. Billing evidence expires after 24 hours. Unknown/delayed bills must be recorded as pending, not zero. If daily billing cannot be reconciled, new publishing intentionally stops; already approved provider schedules may remain. A seven-day unattended claim therefore requires an authenticated billing reconciliation mechanism, which this checkout cannot verify.

After reviewing the exact account bindings, all ten candidate texts and the current policy, use the `authorize` CLI's explicit review arguments. Inspect its help on the installed version before enabling `dry_run=false` and `auto_publish=true`:

```sh
.venv/bin/python -m gtm authorize --help
# Previously created drafts still require their exact-content approval:
.venv/bin/python -m gtm approve x_01
.venv/bin/python -m gtm approve threads_01
.venv/bin/python -m gtm daily
```

This authorizes only reviewed seed copy and bounded scheduling. Text changes require fresh approval. Most configuration changes invalidate authorization. Natural-language model output cannot execute commands or publish. There are no DM, email, reply, like, follow/unfollow, ad, purchase or credit-recharge actions.

## Analytics, leads, Clone and models

[Analytics/lead integration](docs/analytics-leads.md) covers GA4, consent, exact event semantics, the optional research form, validation, retention and authentication. The public site currently says it does not collect applications or use analytics, so attaching this form/tracker requires updating that notice and its consent flow in the actual source. `generate_lead` fires only after a new valid server insert and analytics consent. Email addresses remain in the private lead database; no email is sent. Stored leads are syntax-validated research contacts, not verified mailbox ownership or qualified leads.

[Provider guide](docs/providers.md) covers Clone sandbox keys, usage checks, official schemas and failure handling. Only sandbox mode is accepted; no production billing is enabled. Predictions can abstain, expire or fail. The worker records a deterministic fallback without fabricating results. Post outcome metrics are retained separately; no causal improvement is inferred from one suggested change.

The [four-stage route](docs/model-routing.md) is implemented in `gtm/routing.py` and configured under `[routing]`:

| Stage | Pinned model through Hermes/OpenRouter | Share of $5 model allowance |
| --- | --- | --- |
| Research | `google/gemini-2.5-pro` | 40% / $2 |
| Planning | `google/gemini-2.5-pro` | 40% / $2 |
| Writing | `google/gemini-2.5-flash-lite` | 15% / $0.75 |
| Execution review | `google/gemini-2.5-flash-lite` | 5% / $0.25 |

Research analyzes bounded supplied evidence and aggregate observations; it has no fresh web-search tool. The host reserves costs before each call, records unresolved outcomes without automatic retries, enforces daily/lifetime limits, and bounds subprocess output to 64 KiB with a 90-second deadline. The execution model only requests review or rejects content. Generated drafts always require exact-text owner approval; a seed-library approval never transfers to new copy.

Live Hermes inference remains **blocked** by default. The installed runtime lacks required structured CLI flags, and current upstream documentation removed the old hard output-token controls. Before enabling `[routing].enabled`, verify the exact Cloud runtime, provider-enforced output/spending caps and fresh billing. Setting verification flags is not a substitute for evidence. Legacy `model.enabled=true` is rejected. `route` runs only the proposal pipeline; `daily` calls it before normal posting guards when enabled.

[Telegram operations](docs/telegram.md) use a dedicated owner-only Bot API client with `/status`, `/budget`, `/posts` and `/help`. Configure its three private environment values and set `[telegram].enabled=true` only after checking the recipient. State changes, budget/account issues, stage outcomes and one daily KST summary use a durable outbox. Unknown delivery is not blindly retried. `status` prints a local preview without sending anything; `telegram` performs one polling/delivery tick. No Telegram input can approve, publish, run a model, execute a shell or change settings.

## Operate and recover

```sh
.venv/bin/python -m gtm inspect
.venv/bin/python -m gtm status
.venv/bin/python -m gtm pause
.venv/bin/python -m gtm resume
.venv/bin/python -m gtm incident --reason platform_warning
# Only after owner resolves the platform issue; renew authorization afterward:
.venv/bin/python -m gtm resume --clear-incident
.venv/bin/python -m gtm reconcile
.venv/bin/python -m gtm watchdog
.venv/bin/python -m gtm stop
.venv/bin/python -m gtm report
.venv/bin/python -m gtm record-cost --key invoice-001 --category infrastructure --classification confirmed --amount 1.25
# Unknown charge: omit --amount. It blocks new optional paid/publishing operations.
.venv/bin/python -m gtm record-cost --key pending-001 --category external --classification pending
```

Pause/stop revoke work and attempt cancellation; they cannot guarantee cancellation of provider jobs already running or unavailable. Inspect unresolved jobs in Upload-Post before concluding external work stopped. A timeout first persists UNKNOWN and spends its local quota; reconciliation uses provider IDs/history and never blindly resubmits. If an ambiguous future schedule is absent from history, inspect the provider's pending list/dashboard. No post is deleted by the worker. Restart from the same database; never create a replacement experiment to bypass uncertainty. SQLite backups must use its backup API or a stopped/checkpointed writer, not a copy of a live main file alone.

Costs are append-only. `record-cost` takes incremental non-overlapping entries with unique keys. To reconcile a pending/estimated/recorded item, append a known replacement with `--supersedes ORIGINAL_KEY`; both records remain immutable, and calculations use only the replacement. For example, `record-cost --key settled-001 --supersedes pending-001 --category external --classification confirmed --amount 0.25`. Final confirmed charges cannot be overwritten. Never add the same charge twice without a reconciliation link. This is an operator-driven billing reconciliation workflow; automatic provider billing access remains unverified. Infrastructure is projected for the full 168 hours, warnings begin at 70%, and new optional work stops at 90%. Existing approved schedules and deterministic reports can continue as appropriate.

For native manual insights fallback use `import-metrics --file observation.json`. The exact JSON keys are `post_id`, `observed_at`, `period_start`, `period_end`, and `metrics`; timestamps need offsets. Metrics may contain nonnegative `views`, `impressions`, `likes`, `replies`, `reposts`, `quotes`, `link_clicks`, `followers` or null. Only already verified published posts can receive a manual import; provenance is `MANUAL platform insights`.

At 168 hours, the worker stops creating submissions, cancels remaining pending jobs where possible, finalizes available metrics and exports reports. GA4 and native analytics can still arrive later, so final observations may be incomplete. The Cloud instance is left unchanged. After checking outstanding jobs and saving reports, stop the instance using the official portal to avoid running charges; stopped storage may still cost money. No automatic instance deletion is implemented.

## Acceptance status

Implemented and locally testable: durable state/recovery, API adapters and mocks, per-account strategy registry and seven prompts, read-only connection inspection, native-destination checks, review/authorization gates, quotas, runtime/DST boundaries, conservative costs, four-stage model routing, read-only Telegram monitoring, deterministic collection and reports, research-form backend and consented frontend, Clone sandbox feedback, six-job no-agent cron installer, offline CI and source-only reproducible packaging.

Required before launch: Cloud access and current Small quote; remote Python/Hermes compatibility; secure credential placement; reviewed native account bindings and fresh quota evidence; explicit content/publishing authorization; provider-enforced model spending/output caps and billing reconciliation; actual Telegram recipient/delivery; and persistence plus unattended scheduling verified with the Mac off. The latest read-only connection report is the authority for what account inspection actually established. GA4, site integration and Clone sandbox remain optional, separately unverified integrations. No artifact here establishes that the campaign is live.
