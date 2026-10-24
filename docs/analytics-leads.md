# Analytics and research-interest attachment

This is a tested local integration package, not a deployment to disanchor.com. No authenticated GA4 request, real lead submission, email or production change was made. Account suspension cannot be ruled out by implementation alone; this package does not bypass platform controls or turn contact consent into permission for automated outreach.

## Public site inspection — 2026-10-24

The live [Disanchor introduction page](https://disanchor.com/) was opened in a browser. It describes a personal Windows cloud PC in development, with planned dedicated Windows/macOS clients subject to validation. The early-access section says applications are not open and no personal details are requested. Its privacy section says there is no application collection, advertising or analytics cookies. The inspected DOM contained zero forms and one JavaScript module, `/assets/index-DxulzBQ6.js`, with no generator metadata. A bundled module alone does not establish a framework. Network-level analytics absence was not proven; the finding is the site's visible statement and DOM at inspection time.

The workspace did not contain the live site's source or deployment configuration. The optional form must remain a local preview until the owner updates that public privacy promise and provides the source/deployment attachment point. Do not announce a working signup, guaranteed features, confirmed prices or early-access availability.

## GA4 reader

`gtm.analytics.GA4Client(property_id, access_token, transport=None, include_content=False, key_event_registered=False, token_provider=None)` exposes `collect(start_date, end_date, campaign)`. Dates are inclusive ISO dates interpreted in the GA4 property's timezone. The campaign is a non-personal lowercase slug. The default transport calls only the official HTTPS Data API `runReport` endpoint, with a 20-second timeout, response-size bound and redirects rejected. A manually supplied OAuth token must be refreshed by its caller. For unattended collection, the service-account factory below refreshes credentials automatically. Use a secret manager and never paste credentials into config artifacts or logs.

```python
from gtm.analytics import GA4Client, MockGA4

# Real collection only after the property and access are configured by its owner.
# Install optional google-auth[requests]>=2,<3; GOOGLE_APPLICATION_CREDENTIALS
# points to an owner-provisioned private service-account JSON file.
client = GA4Client.from_service_account("123456789", include_content=True)
snapshot = client.collect("2026-10-01", "2026-10-24", "disanchor_gtm_7d")

# Offline workflow checks are explicitly synthetic.
fixture = MockGA4().collect("2026-10-01", "2026-10-24", "disanchor_gtm_7d")
assert fixture["mode"] == "mock"
```

`from_service_account(property_id, credentials_file=None, transport=None, **options)` uses `GOOGLE_APPLICATION_CREDENTIALS` when the filename is omitted. It requests only the `analytics.readonly` scope; the service account must already have property read access. Construction parses the owner-provided file but makes no network request. Google's `before_request` refreshes the token when needed before reporting, with a 20-second token transport timeout. No key or token enters worker state. Missing optional dependencies or bad configuration produce safe errors. A refresh failure or first HTTP 401/403/429 blocks all remaining calls in that collection. The worker controls later collection timing. This is implemented authentication plumbing, not a claim that credentials or property access have been supplied. See [Google's credential interface](https://google-auth.readthedocs.io/en/latest/reference/google.auth.credentials.html).

Output contains `source`, `mode`, `campaign`, `period`, `totals`, `channels`, optional `content`, `missing`, and `warnings`. `totals.users` is GA4 `activeUsers`; the other metrics are `sessions` and `engagedSessions`. Totals have their own query: users across channels/content overlap and must never be added to derive a unique total. Channel rows use `sessionSource` and `sessionMedium`, retaining only `x` or `threads` with `organic_social` so the two experiment channels stay separate. Optional content uses `sessionManualAdContent` (`utm_content`), accepting `x_profile`, `threads_01` through `threads_05`, and approved-format `c_...` identifiers. Other labels are omitted with a warning. Never use names, email addresses or user identifiers in campaign/content identifiers. [Google's dimension and metric schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema) defines these fields.

The collected event counts are `page_view`, `cta_click`, `lead_form_start`, `view_research_form`, and `generate_lead`. Unknown/restricted/missing metrics are `null`, with safe reason codes. Failed/empty reports never become zero. An event absent from an otherwise complete event report is zero; if thresholding, sampling or truncation is reported, absent values remain unknown. Present values are reported with quality warnings. Pagination is bounded to four 25,000-row pages; an incomplete report is unavailable, not silently complete. The client does not log API response bodies. See [runReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport) and [response metadata](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData).

`key_event_registered=True` is an explicit opt-in only after the owner verifies `generate_lead` was registered as a key event for the relevant period. It queries native `sessionKeyEventRate:generate_lead` separately and returns the fraction as `key_event_session_rate`. Default output is `null` with a registration-not-verified reason. Failed/invalid/rate-limited queries remain unknown. Registration does not retroactively convert earlier data. `key_event_sessions` always stays `null`: neither `eventCount` nor multiplying a rounded rate by sessions establishes an independently observed session count. The [per-key-event metric schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema#key_event_rate_metrics_for_each_key_event) describes the registered-event metric.

GA4 reporting identity can include modeled data; `users` is the API's measured aggregate, not a verified person count. Consent, blockers and reporting delay can make GA4 lower or different from local stored opt-ins. Compare the same period/timezone and label differences; never manufacture reconciliation values. A token failure produces missing reasons for each report and should be escalated for operator attention, not repeatedly retried to evade a limit.

## Optional lead sidecar

`gtm.leads.LeadStore` owns a separate SQLite database. It stores a normalized email, a broad interest category, consent version, timestamp and analytics-consent flag. Registered campaign attribution is stored only with separate analytics consent. It never sends email, subscribes anyone to marketing, verifies email ownership or publishes contact data. Deduplication uses a keyed email digest and a database unique constraint; duplicate requests do not overwrite consent. The raw email and its keyed digest are both personal data. Put this database and its backup policy outside worker state, reports, version control and general exports.

`LeadApp` requires an exact HTTPS origin, real privacy-contact email, consent version and an explicitly created store. There is no default public server. It serves only `/research-interest`, `/tracking.js`, and `POST /api/research-interest`; no public lead list or aggregate route exists. POST requires matching Origin and Host, JSON, current explicit research consent, valid bounded fields and a body of at most 4096 bytes. It uses a honeypot and a shared SQLite request counter (five attempts per TCP client per ten minutes), storing only short-lived keyed address digests. It ignores all forwarded-IP headers. A trusted reverse proxy must enforce additional per-client limits; otherwise all proxy traffic safely shares one limit. Configure request/body timeouts and size limits at that proxy, and ensure direct backend access is blocked.

The form returns `{"ok":true,"new":true}` only after a new database insertion. Duplicate and honeypot requests return `new:false`. This distinction can reveal that an email was previously submitted; rate limits reduce enumeration but do not eliminate it. If a stronger privacy threat model requires identical public responses, replace this contract with a private server event ledger before public launch. Until ownership verification and qualification exist, count only **unverified, deduplicated research opt-ins**, never qualified leads.

`store.aggregate(start_date, end_date, campaign=None)` is a local Python method returning retained opt-in counts and channel aggregates, with `source="lead_store"`, `mode="observed"`, inclusive UTC period and `qualified_leads=null`/`not_assessed`. Campaign-filtered totals exclude opt-ins without attribution. Aggregate counts are retention-scoped; expired/deleted contacts no longer contribute, and re-entry after deletion/expiry may count again. `store.prune()` must run daily even when there is no traffic; backups must expire on the same schedule. `store.delete_by_email(email)` supports an operator-handled deletion request. There is no automatic deletion scheduler in this module.

The worker should use the separate read-only `aggregate_leads(path, start_at, end_at, campaign=None, retention_days=30, as_of=None)` function. It requires an existing database and timezone-aware timestamps, opens SQLite read-only, selects `[start_at, end_at)` in UTC, and never creates tables, changes permissions, reads contact columns or requires the deduplication secret. Stored timestamps have one-second resolution, recorded in output. Supply the store's actual retention setting. The result includes the exact period, `observed_at`, source and retention scope; missing data must remain `null` at the caller. Simulation must never read a real lead database. The date-based instance method remains available for standalone daily checks, but cannot establish an exact 168-hour experiment window.

### Local startup

Run from the repository root with Python 3.12. This temporary preview uses synthetic data, no analytics, a placeholder contact and a throwaway secret. The server binds only to loopback, and local mode rejects non-loopback clients. Close it with Ctrl-C. The standard-library WSGI server is for local checking only.

```python
import tempfile
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler
from gtm.leads import LeadApp, LeadStore

class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass  # Do not log request IP, form values or email.

with tempfile.TemporaryDirectory(prefix="disanchor-preview-") as directory:
    store = LeadStore(Path(directory) / "leads.sqlite3",
                      secret=b"local-preview-only-secret-32-bytes")
    app = LeadApp(store, origin="http://127.0.0.1:8081",
                  privacy_contact="privacy@example.com",
                  consent_version="preview-v1", allow_local_http=True)
    with make_server("127.0.0.1", 8081, app, handler_class=Quiet) as server:
        print("http://127.0.0.1:8081/research-interest")
        server.serve_forever()
```

### Production construction and blockers

The constructor below is an attachment example, not a launch instruction. `persistent_secret` must be stable and at least 32 random bytes, supplied from a secret manager; changing it invalidates deduplication/deletion lookup. The database file is mode 0600. Production still requires a private parent directory, encrypted storage, restricted operator access, bounded backups and a real supported WSGI host. SQLite is suitable only for the intended low-volume single-host sidecar; no multi-host coordination is provided.

```python
store = LeadStore(private_database_path, secret=persistent_secret, retention_days=30)
app = LeadApp(
    store, origin="https://disanchor.com",
    privacy_contact=approved_privacy_contact,
    consent_version=approved_notice_version,
    content_registry={
        "x_profile": {"campaign": "disanchor_gtm_7d", "channel": "x"},
        **{f"threads_{n:02}": {"campaign": "disanchor_gtm_7d", "channel": "threads"}
           for n in range(1, 6)},
    },
)
```

Before attaching it, confirm all of the following: the actual site source and deployment owner; an updated approved privacy notice including controller/contact, purpose, retention, hosting handling and any analytics transfer; a working withdrawal channel and daily retention job; encrypted private storage and backups; trusted TLS/proxy/Host configuration, no body or PII access logs, timeout/abuse monitoring; and a separate optional analytics-consent UI. The constructor rejects plaintext public origins. Do not casually trust arbitrary `X-Forwarded-Proto` or `X-Forwarded-For`; configure the WSGI server's scheme only through its verified proxy mechanism.

## Browser events and consent

`web/tracking.js` loads no Google tag, creates no cookies and sends no analytics by default. It sends only the form's explicit JSON submission to the same origin. `DisanchorTracking.configure({hasAnalyticsConsent, emit})` lets an approved site integration supply a live consent getter and emitter. The getter is checked again at emission, so withdrawal stops subsequent calls. No historical unconsented event is replayed. `cta_click` marks the CTA; `lead_form_start` marks the first consented focus; `generate_lead` fires only for a newly stored response with consent both at submission and response time. A duplicate, rejected request, uncertain network result, or mere click cannot fire it. A lost response can undercount GA4; the server aggregate remains the independent measure.

The emitter receives only the event name, constant method/page fields and approved content ID. Email, interest, identifiers, query strings and referrers are excluded. Approved IDs come from the server registry, not arbitrary UTM values. The standalone CSP deliberately permits only same-origin connections/scripts; production analytics requires a reviewed CSP change and CMP integration. Configure the property to disable enhanced form measurement/user-provided data, avoid automatic PII-bearing page URLs and avoid a second automatic `generate_lead` trigger. Set consent defaults before any Google configuration, and use basic consent blocking so no Google script or ping loads before consent. Google's [consent guide](https://developers.google.com/tag-platform/security/guides/consent) describes command ordering; its [PII guidance](https://support.google.com/analytics/answer/6366371) requires keeping identifying data out of analytics. This scaffold's emitter does not make an existing site's unrelated tags compliant.

## Verification

```sh
python3.12 -m unittest discover -s tests -p 'test_analytics_leads.py' -v
```

Twenty-eight tests passed on Python 3.12, including a Node harness running the actual tracking script. Coverage includes non-additive users, separate X/Threads attribution, canonical content IDs, optional key-event rate, mocked credential refresh, stop-on-rate-limit, missing metrics, thresholding, bounded pagination, strict consent/Origin/Host, spoofed forwarding headers, shared rate limits, duplicate insertion, privacy retention, exact first/last UTC bounds, read-only aggregate access, absence of public PII routes, and no lead event on duplicate/failure/no consent. The local page was opened and visually checked in a browser; one synthetic local submission showed its success state. No real contact or GA4 property was used. The deployed site's implementation and production consent journey remain unverified until its source and credentials are supplied.
