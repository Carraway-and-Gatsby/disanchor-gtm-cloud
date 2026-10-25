# Read-only comment permission audit · 2026-10-25

Sanitized live GET evidence. No credentials, native IDs, post IDs, comment text or author identities. `network_calls` is the first audit; `total_network_calls` includes the user-supplied personal X post check. No publishing or connection changes.

```json
{
  "checked_at_utc": "2026-10-25T06:05:04.459817+00:00",
  "method": "GET_only_no_redirect_no_retry",
  "network_calls": 11,
  "calls": [
    {
      "kind": "profile",
      "http_status": 200
    },
    {
      "kind": "media",
      "http_status": 200
    },
    {
      "kind": "comments",
      "http_status": 200
    },
    {
      "kind": "media",
      "http_status": 200
    },
    {
      "kind": "profile",
      "http_status": 200
    },
    {
      "kind": "media",
      "http_status": 200
    },
    {
      "kind": "comments",
      "http_status": 200
    },
    {
      "kind": "media",
      "http_status": 200
    },
    {
      "kind": "comments",
      "http_status": 200
    },
    {
      "kind": "profile",
      "http_status": 200
    },
    {
      "kind": "media",
      "http_status": 200
    }
  ],
  "accounts": [
    {
      "account_key": "default_x",
      "profile": "default",
      "platform": "x",
      "selected": false,
      "identity_matches": true,
      "connection": "connected",
      "permission_metadata_exposed": false,
      "media_status": "http_200",
      "comments_status": "read_succeeded",
      "http_status": 200,
      "partial": null,
      "source": "mentions",
      "returned_comment_count": 0
    },
    {
      "account_key": "default_threads",
      "profile": "default",
      "platform": "threads",
      "selected": true,
      "identity_matches": true,
      "connection": "connected",
      "permission_metadata_exposed": false,
      "media_status": "no_recent_post",
      "comments_status": "unverified_no_own_post",
      "http_status": null,
      "partial": null,
      "source": null,
      "returned_comment_count": null
    },
    {
      "account_key": "personal_x",
      "profile": "disanchor_twitter",
      "platform": "x",
      "selected": false,
      "identity_matches": true,
      "connection": "connected",
      "permission_metadata_exposed": false,
      "media_status": "http_200",
      "comments_status": "read_succeeded",
      "http_status": 200,
      "partial": null,
      "source": "mentions",
      "returned_comment_count": 0
    },
    {
      "account_key": "personal_threads",
      "profile": "disanchor_twitter",
      "platform": "threads",
      "selected": false,
      "identity_matches": true,
      "connection": "connected",
      "permission_metadata_exposed": false,
      "media_status": "http_200",
      "comments_status": "partial_read_only",
      "http_status": 200,
      "partial": true,
      "source": null,
      "returned_comment_count": 0
    },
    {
      "account_key": "disanchor_x",
      "profile": "disanchor_x",
      "platform": "x",
      "selected": true,
      "identity_matches": true,
      "connection": "connected",
      "permission_metadata_exposed": false,
      "media_status": "no_recent_post",
      "comments_status": "unverified_no_own_post",
      "http_status": null,
      "partial": null,
      "source": null,
      "returned_comment_count": null
    }
  ],
  "runtime_files_unchanged": true,
  "posts_sent": 0,
  "writes_sent": 0,
  "publishing": "OFF",
  "paid_models": "OFF",
  "budget_usd": 20,
  "raw_responses_stored": false,
  "post_and_comment_ids_stored": false,
  "user_supplied_post_check": {
    "checked_at_utc": "2026-10-25T06:07:00.096827+00:00",
    "account_key": "personal_x",
    "selected": false,
    "user_supplied_post": true,
    "comments_status": "read_succeeded",
    "http_status": 200,
    "source": "mentions",
    "partial": null,
    "returned_comment_count": 0,
    "network_calls": 2,
    "calls": [
      {
        "kind": "profile",
        "http_status": 200
      },
      {
        "kind": "comments",
        "http_status": 200
      }
    ],
    "runtime_files_unchanged": true,
    "posts_sent": 0,
    "writes_sent": 0,
    "raw_responses_stored": false,
    "post_and_comment_ids_stored": false
  },
  "total_network_calls": 13
}
```
