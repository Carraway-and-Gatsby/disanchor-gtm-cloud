"""A publishing facade bound to reviewed, platform-specific native identities.

The facade does not approve content. It rechecks the destination immediately
before delegating a submission, while provider job reads/cancellations remain
available for reconciliation. Reconnection between check and submission cannot
be made atomic with Upload-Post's profile-targeted API.
"""
from __future__ import annotations

from .accounts import AccountRegistry
from .account_connection import UploadPostAccountClient
from .publisher import UploadPostPublisher, ValidationError, ProtocolError, _validate


class AccountBoundPublisher:
    simulated = False

    def __init__(self, api_key, registry, targets, transport=None):
        if (not isinstance(registry, AccountRegistry) or not isinstance(targets, dict)
                or set(targets) != {"x", "threads"} or any(not isinstance(value, str) for value in targets.values())):
            raise ValidationError("account_target_configuration_invalid")
        self.registry, self.targets = registry, dict(targets)
        self._fingerprint = registry.fingerprint()
        self._accounts = UploadPostAccountClient(api_key, transport)
        self._publishers, self._profiles = {}, {}
        try:
            for platform in ("x", "threads"):
                row = registry.get(self.targets[platform])
                if row["platform"] != platform:
                    raise ValueError("platform_mismatch")
                profile = row["upload_post_profile"]
                self._profiles[platform] = profile
                if profile not in self._publishers:
                    self._publishers[profile] = UploadPostPublisher(api_key, profile, transport)
        except (ValueError, KeyError, TypeError):
            raise ValidationError("account_target_configuration_invalid") from None

    def _publisher(self, platform):
        if not isinstance(platform, str) or platform not in self._profiles:
            raise ValidationError("account_target_platform_unsupported")
        return self._publishers[self._profiles[platform]]

    def verify_destination(self, platform):
        self._publisher(platform)
        try:
            if self.registry.fingerprint() != self._fingerprint:
                raise ValueError("account_registry_changed")
            binding = self.registry.binding(self.targets[platform])
            if binding["platform"] != platform or binding["upload_post_profile"] != self._profiles[platform]:
                raise ValueError("account_target_changed")
            receipt = self._accounts.verify_binding(binding["upload_post_profile"], platform, binding["native_account_id"])
        except (ValueError, KeyError, TypeError):
            raise ValidationError("account_identity_changed") from None
        return {"binding": binding, "receipt": receipt}

    def schedule(self, platform, text, scheduled_at, idempotency_key):
        publisher = self._publisher(platform)
        _validate(platform, text, scheduled_at, idempotency_key)
        self.verify_destination(platform)
        return publisher.schedule(platform, text, scheduled_at, idempotency_key)

    def quota(self):
        # One allowance belongs to the API account, even with distinct profiles.
        return {**self._accounts.current_user(), "simulated": False}

    def status(self, job_id):
        # Job IDs are global provider identities previously recorded by the host.
        return self._publisher("x").status(job_id)

    def cancel(self, job_id):
        # Identity drift must not prevent canceling an already accepted schedule.
        return self._publisher("x").cancel(job_id)

    def analytics(self, post_ids=None):
        if post_ids is None:
            raise ValidationError("bound_analytics_requires_post_request_ids")
        return self._publisher("x").analytics(post_ids)

    def history(self):
        rows, seen = [], {}
        for profile, publisher in self._publishers.items():
            for row in publisher.history():
                platform = row.get("platform")
                # One profile can contain additional channels; those are outside
                # the selected destinations even if its provider query is valid.
                if not isinstance(platform, str) or platform not in self._profiles or self._profiles[platform] != profile:
                    continue
                identity = next(((field, row[field]) for field in
                                 ("provider_job_id", "provider_request_id", "platform_post_id", "external_id")
                                 if row.get(field)), None)
                if identity is not None:
                    key = (profile, platform, *identity)
                    if key in seen:
                        if seen[key] != row:
                            raise ProtocolError("conflicting_account_history")
                        continue
                    seen[key] = row
                rows.append({**row, "profile_username": profile})
        return rows
