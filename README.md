# Disanchor · Cloud marketing worker

[한국어 안내](README.ko.md) · [Repository](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud) · [GitHub Actions](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud/actions)

A Python 3.12 / SQLite worker for a **seven-day, organic-first Disanchor experiment** on X and Threads. It prepares useful, account-specific content, records performance, and turns evidence into the next research and planning cycle. Disanchor is still being developed; drafts explain that naturally within the writing.

**This repository is private.** Open it while signed in as the owner or an invited collaborator. GitHub may show a 404 to an account without access. The repository contains code and deployment instructions; it does not mean a Cloud instance or a live campaign has been started.

## What is ready

| Area | Implemented / configured | Live verification |
| --- | --- | --- |
| Account-specific writing | Seven separate strategies; one selected X account and one Threads account | Authenticated read-only checks found seven connected channels; posting approval remains separate |
| Publishing | Official Upload-Post adapter, identity checks, exact-text approval, quotas and recovery | Disabled by default; no live posts were sent during preparation |
| Model routing | Research → planning → writing → execution review; 80% of the model allowance goes to the first two stages | Paid inference disabled until the Cloud runtime and provider caps are verified |
| Feedback and skill learning | Own-post feedback, account-specific aggregates and automatic versioned skill compilation; optional isolated Hermes native writer | No real audience sentiment or learned-skill effectiveness is claimed |
| Operations | Persistent SQLite, six bounded Hermes cron jobs, owner-only Telegram status | Cloud deployment, restart recovery and Telegram delivery still need verification |
| Tests and delivery | Offline test suite, preflight, reproducible source ZIP and GitHub workflow | [The earlier workflow rerun passed](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud/actions/runs/40078252934); check Actions for the latest commit |

## How it works

```text
Upload-Post observations → bounded feedback → research → planning → writing
                                  ↓                                 ↓
                          validated skill revisions          execution review
                                                                    ↓
                                                        owner-approved exact text
                                                                    ↓
                                                      deterministic posting guards
                                                                    ↓
                                                           Upload-Post schedule
```

Hermes runs the hosted scheduler. The worker owns state, account identity, policy, budget, publication and recovery checks. A model or a learned skill cannot grant itself publishing permission. Telegram is the main status interface; HTML files are optional review snapshots.

Feedback reads only locally verified published posts through the official API. Sentiment uses conservative English/Korean keyword labels on a bounded comment sample; missing, ambiguous or unsupported responses remain unknown. Likes are never treated as positive sentiment. Only validated aggregates reach planning or skill content; raw comments and author identities are not retained.

The learning compiler creates reusable, versioned evidence summaries without another model call. With explicit installed-runtime paths, it uses Hermes's native `skill_manage` create/edit tool inside an isolated private home; otherwise it produces the documented native-format artifact. These are measured lessons with provenance and rollback, not unrestricted model-written code or permission to alter safeguards. See [the auto-skill guide](docs/hermes-auto-skills.md) for activation and verification.

| Stage | Configured model through Hermes / OpenRouter | Share of the $5 model ceiling |
| --- | --- | --- |
| Research | `google/gemini-2.5-pro` | 40% / $2.00 |
| Planning | `google/gemini-2.5-pro` | 40% / $2.00 |
| Writing | `google/gemini-2.5-flash-lite` | 15% / $0.75 |
| Execution review | `google/gemini-2.5-flash-lite` | 5% / $0.25 |

The research stage uses bounded supplied evidence. The minimal inference runner has no fresh web-search tool. Models, runtime support and actual billing must be checked on the deployment host. See [model routing](docs/model-routing.md), [marketing feedback](docs/marketing-feedback.md) and [Hermes auto-skill learning](docs/hermes-auto-skills.md).

## Budget and safeguards

The approved **total operating budget is USD 20**, including Cloud infrastructure, models and other charged services. The $5 model and $2 external-tool ceilings are inside that total. They are ceilings, not spending targets or extra allowances. Fresh billing reconciliation is required; unknown charges stay unknown.

- At **$14 (70%)**, warn. At **$18 (90%)**, block new optional paid work and new publishing through the existing budget gate.
- Keep the 168-hour experiment boundary, ten-post total, five posts per platform and at most one post per platform per audience-local day.
- In the initial campaign, use useful observations and genuine questions. No promotional links, signup/profile prompts, engagement bait, unsolicited replies, DMs, email or paid ads.
- New model copy requires exact-text owner approval. A changed account, policy, prompt or binding invalidates the affected authorization.
- Preserve unknown submission outcomes and quota reservations. Reconcile them; never blindly retry or reset state to recover quota.
- A platform warning pauses work and revokes authorization. Multiple accounts do not multiply quotas or excuse duplicated posts.

The app's budget checks do not shut down a Cloud instance or impose a provider billing cap. Verify the current Cloud price, reconcile charges at least every 24 hours, and stop the instance after the experiment. These controls reduce avoidable risk; they cannot guarantee that a platform will never restrict an account.

## Try it locally without credentials

Run from the repository root with Python 3.12 or newer:

```sh
python3.12 -m venv .venv
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/cloud-preflight.py
.venv/bin/python -m gtm preview-library
.venv/bin/python -m gtm account-prompts
.venv/bin/python -m gtm --output reports/my-dry-run dry-run
.venv/bin/python scripts/package-cloud.py --output reports/disanchor-cloud.zip
.venv/bin/python scripts/build-review.py
```

The core worker and tests use the standard library. Only optional GA4 service-account access needs `requirements-live.txt`. Use a fresh output directory for each dry run. It uses mocks, sends no posts or Telegram messages, and creates labeled simulated reports. Open the generated `reports/review-index.html` locally to inspect candidates, source and evidence. Reports, databases, private account bindings and credentials are excluded from Git and the Cloud ZIP.

## Prepare Cloud deployment

1. Read [Cloud deployment](docs/cloud-deployment.md) and [Cloud operations](docs/cloud-operations.md). Verify the actual Hermes Cloud runtime, persistent disk and current price within the $20 total.
2. Install this source and a Python virtual environment on Cloud. Keep publishing, model routing and Telegram disabled for initial checks.
3. Put required credentials in owner-only `state/runtime.env` (directory `700`, file `600`). The source ZIP contains no credentials. The CLI reads this file only with an explicit `--env-file`; never shell-source it.
4. Copy `accounts.example.toml` to ignored `accounts.local.toml`, confirm native account IDs, select one X and one Threads target, and review their approaches. Instagram and LinkedIn are **strategy/draft only** in this worker.
5. Verify quota, fresh billing and the exact text before authorizing any submission. Verify provider-enforced output and spending caps before paid inference.
6. Install the six bounded no-agent cron jobs on Cloud, then verify restart recovery and unattended operation with the Mac off. Configure the dedicated Telegram bot and check actual delivery separately.

```sh
# Read-only account check; does not authorize or publish:
.venv/bin/python -m gtm --env-file state/runtime.env connection-check
# Inspect runtime flags without installing cron jobs:
bash scripts/hermes-install.sh --check
# Local status preview; does not send Telegram:
.venv/bin/python -m gtm status
```

The completed local connection check authenticated and matched the selected X/Threads native destinations with seven GET requests and zero posts. The provider did not supply an upload allowance, so remaining quota is unknown and the configured allowance remains zero. All local account bindings remain unreviewed for publication. These checks must be repeated on the deployment host.

## Documentation

| Need | Guide |
| --- | --- |
| Korean setup and daily commands | [한국어 운영 안내](README.ko.md) |
| Seven account approaches | [Account strategies](docs/account-strategies.md) |
| API connection, native identity and quota | [Connection checks](docs/upload-post-connection.md) · [Upload-Post contract](docs/upload-post.md) |
| Organic content and suspension-risk controls | [Posting policy](docs/posting-policy.md) |
| Performance, comments and sentiment boundaries | [Marketing feedback](docs/marketing-feedback.md) |
| Reusable Hermes learning with review gates | [Auto-skill workflow](docs/hermes-auto-skills.md) |
| Four-stage models and hard caps | [Model routing](docs/model-routing.md) |
| Hosted installation, recovery and budget | [Cloud deployment](docs/cloud-deployment.md) · [Cloud operations](docs/cloud-operations.md) |
| Owner-only status bot | [Telegram](docs/telegram.md) |
| GA4, consent and optional research contacts | [Analytics and leads](docs/analytics-leads.md) |
| Optional Clone sandbox and credentials | [Providers](docs/providers.md) |

The existing `disanchor.com` site has not been changed. Its source and consent flow must be reviewed before integrating the optional research form or analytics. No live result, viral outcome, automatic skill effectiveness or successful Cloud deployment should be inferred from mock data or a passing offline test.
