#!/usr/bin/env python3
"""Owner-approved fixed provider batch journal. No network, model or posting API."""
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'state/gtm-native-batch-20261027-35.json'
TARGETS = {'x': ('disanchor_x', 3, 21), 'threads': ('default', 2, 14)}


def validate(plan, expected):
    items = plan['items']
    if len(items) != 35 or len({i['id'] for i in items}) != 35:
        raise ValueError('invalid_batch_size_or_ids')
    counts = {}
    for item in items:
        platform = item['platform']
        if platform not in TARGETS or item['user'] != TARGETS[platform][0] or item['handle'] != 'disanchor_com':
            raise ValueError('destination_changed')
        if hashlib.sha256(item['text'].encode()).hexdigest() != item['text_sha256']:
            raise ValueError('text_changed')
        stamp = datetime.fromisoformat(item['scheduled_date'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.isoformat() != datetime.fromisoformat(item['ET']).astimezone(timezone.utc).isoformat():
            raise ValueError('timestamp_changed')
        day = item['ET'][:10]
        counts[platform, day] = counts.get((platform, day), 0) + 1
    for platform, (_, per_day, total) in TARGETS.items():
        days = {d: n for (p, d), n in counts.items() if p == platform}
        if len(days) != 7 or sum(days.values()) != total or any(n != per_day for n in days.values()):
            raise ValueError('daily_count_changed')
    canonical = [{k: i[k] for k in ('id', 'platform', 'user', 'handle', 'scheduled_date', 'text', 'text_sha256')}
                 for i in items]
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if digest != expected or plan['plan_sha256'] != expected:
        raise ValueError('approval_digest_changed')


def transition(plan, action, *, item_id=None, job_id=None, request_id=None):
    now = datetime.now(timezone.utc).isoformat()
    if action == 'approve':
        if plan['status'] != 'AWAITING_EXACT_TEXT_APPROVAL' or plan['exact_text_approved']:
            raise ValueError('already_approved_or_not_pending')
        plan.update(status='APPROVED_FIXED_BATCH', exact_text_approved=True, approved_at=now,
                    approval_basis='owner approved the exact 35-post document and calendar in this chat')
        return
    if not plan['exact_text_approved']:
        raise ValueError('exact_text_approval_required')
    if action == 'finish':
        if any(i['state'] != 'SCHEDULED_VERIFIED' for i in plan['items']):
            raise ValueError('provider_readback_required')
        plan.update(status='ACTIVE_BOUNDED_BATCH', provider_native_scheduling_enabled=True,
                    scheduled_posts_verified=35, verified_at=now)
        return
    item = next(i for i in plan['items'] if i['id'] == item_id)
    if action == 'claim':
        if item['state'] != 'PREPARED' or item['submission_attempts'] != 0:
            raise ValueError('no_resubmission')
        if any(i['state'] == 'SUBMISSION_UNKNOWN' for i in plan['items']):
            raise ValueError('resolve_unknown_submission_first')
        if datetime.fromisoformat(item['scheduled_date'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
            raise ValueError('missed_slot_no_immediate_post')
        item.update(state='SUBMISSION_UNKNOWN', submission_attempts=1, claimed_at=now)
        plan['status'] = 'REGISTERING_FIXED_BATCH'
    elif action == 'receipt':
        if item['state'] != 'SUBMISSION_UNKNOWN' or not job_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', job_id):
            raise ValueError('invalid_receipt_state_or_job')
        item.update(state='SCHEDULE_ACCEPTED', job_id=job_id, request_id=request_id, receipt_at=now)
        plan['provider_native_scheduling_enabled'] = True
    elif action == 'verify':
        if item['state'] != 'SCHEDULE_ACCEPTED' or job_id != item['job_id']:
            raise ValueError('receipt_mismatch')
        item.update(state='SCHEDULED_VERIFIED', provider_readback_at=now)
    else:
        raise ValueError('unknown_action')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('inspect', 'approve', 'claim', 'receipt', 'verify', 'finish'))
    parser.add_argument('--digest', required=True)
    parser.add_argument('--id')
    parser.add_argument('--job-id')
    parser.add_argument('--request-id')
    args = parser.parse_args()
    os.umask(0o077)
    with open(str(PATH) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fd = os.open(PATH, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 131072:
                raise ValueError('private_plan_required')
            plan = json.load(handle)
        validate(plan, args.digest)
        if args.action == 'inspect':
            print(json.dumps(plan, ensure_ascii=False))
            return
        transition(plan, args.action, item_id=args.id, job_id=args.job_id, request_id=args.request_id)
        temporary = PATH.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
        os.chmod(temporary, 0o600)
        temporary.replace(PATH)
        print(json.dumps({'status': plan['status'], 'action': args.action, 'id': args.id}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status': 'batch_journal_blocked', 'details': 'omitted', 'posts_sent': 0}))
        raise SystemExit(1)
