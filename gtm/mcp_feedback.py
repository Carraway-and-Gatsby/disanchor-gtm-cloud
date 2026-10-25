"""Automatic MCP -> verified own-post aggregates -> existing Hermes skill loop.

No input files or model-provided assertions can bypass live ownership checks.
Raw MCP bodies and comments exist only in memory. Rejection supersedes old data.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import hashlib
import json
import os
import re
import time

from .config import iso, timestamp
from .feedback import classify_sentiment, sentiment_summary, _PRIVATE
from .mcp_read import ReadOnlyMCP, MCPReadError
from .account_connection import UploadPostAccountClient
from .publisher import PublisherError


class Rejected(ValueError):
    pass


def metrics_for(data, post, now=None):
    if not isinstance(data, dict) or ('success' in data and data['success'] is not True) or data.get('scope', 'post') != 'post':
        raise Rejected('post_metrics_unverified')
    if data.get('source') == 'snapshot_cache': raise Rejected('cached_metrics_not_live')
    if now is not None:
        for field in ('captured_at', 'observed_at'):
            if field in data:
                try:
                    observed = timestamp(data[field])
                    if not timestamp(now) - timedelta(hours=48) <= observed <= timestamp(now): raise ValueError()
                except (ValueError, TypeError): raise Rejected('stale_or_future_observation') from None
    if 'request_id' in data and data['request_id'] != post['provider_request_id']:
        raise Rejected('receipt_mismatch')
    row = data.get('platforms', {}).get(post['platform']) if isinstance(data.get('platforms'), dict) else None
    if (not isinstance(row, dict) or row.get('post_metrics_error') or ('success' in row and row['success'] is not True)
            or row.get('metrics_scope', 'post') != 'post'
            or ('status' in row and row['status'] not in {'published','completed','success'})):
        raise Rejected('post_metrics_unverified')
    native = row.get('platform_post_id', row.get('post_id'))
    if str(native) != post['platform_post_id']: raise Rejected('receipt_mismatch')
    values = row.get('post_metrics')
    if not isinstance(values, dict): raise Rejected('post_metrics_unverified')
    for field in ('available_metrics', 'metric_type', 'primary_impressions_field'):
        if field in row and field in values and row[field] != values[field]:
            raise Rejected('metric_support_or_unit_unverified')
    available = row.get('available_metrics', values.get('available_metrics'))
    unit = row.get('metric_type', values.get('metric_type'))
    primary = row.get('primary_impressions_field', values.get('primary_impressions_field', unit))
    if (not isinstance(available, list) or any(not isinstance(k, str) for k in available)
            or not isinstance(unit, str) or unit not in {'impressions','views','reach'} or primary != unit):
        raise Rejected('metric_support_or_unit_unverified')
    if unit not in available: raise Rejected('metric_support_or_unit_unverified')
    out = {}
    for dest, aliases in ((unit, (unit,)), ('likes', ('likes',)),
                          ('replies', ('replies','comments')), ('reposts', ('reposts','shares'))):
        keys = [k for k in aliases if k in available and k in values]
        if not keys: raise Rejected('metric_support_or_unit_unverified')
        nums = [values[k] for k in keys]
        if any(type(v) is not int or not 0 <= v <= 10**12 for v in nums) or len(set(nums)) != 1:
            raise Rejected('metric_counts_invalid')
        out[dest] = nums[0]
    return out


def comments_for(client, post, limits):
    counts = dict.fromkeys(('positive','negative','mixed','unknown'), 0)
    seen_ids, seen_text, cursors = set(), set(), set()
    sampled, cursor = 0, None
    for _ in range(limits.max_comment_pages):
        args = {'user': post['profile_username'], 'platform': post['platform'],
                'postId': post['platform_post_id'], 'limit': min(25, limits.max_comments_per_post - sampled)}
        if cursor: args['after'] = cursor
        data = client.call('get_post_comments', args)
        if not isinstance(data, dict) or data.get('success') is not True or not isinstance(data.get('comments'), list):
            raise Rejected('comments_schema_invalid')
        for key, expected in (('post_id', post['platform_post_id']), ('platform', post['platform']),
                              ('user', post['profile_username']), ('profile_username', post['profile_username'])):
            if key in data and str(data[key]) != expected: raise Rejected('receipt_mismatch')
        if post['platform'] == 'threads' and data.get('partial') is not False:
            raise Rejected('reply_permission_unverified')
        if post['platform'] == 'x' and (data.get('source') != 'mentions' or data.get('partial', False) is not False):
            raise Rejected('mentions_source_unverified')
        for comment in data['comments'][:limits.max_comments_per_post - sampled]:
            sampled += 1
            if not isinstance(comment, dict): raise Rejected('comments_schema_invalid')
            cid = comment.get('id')
            if type(cid) not in (int, str) or not re.fullmatch(r'[0-9]{1,40}', str(cid)) or str(cid) in seen_ids: continue
            seen_ids.add(str(cid))
            author = comment.get('user') if isinstance(comment.get('user'), dict) else {}
            author_id = str(comment.get('author_id', author.get('id', '')))
            if comment.get('is_owner') is True or author_id == post['native_account_id']: continue
            if comment.get('is_owner') is not False and not re.fullmatch(r'[0-9]{1,40}', author_id): continue
            text = comment.get('text')
            if not isinstance(text, str) or len(text.encode()) > 4096: continue
            normalized = _PRIVATE.sub(' ', text).strip().casefold()
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            if digest in seen_text: continue
            seen_text.add(digest)
            counts[classify_sentiment(text)] += 1
        pagination = data.get('pagination')
        if not isinstance(pagination, dict) or type(pagination.get('has_next')) is not bool:
            raise Rejected('comments_schema_invalid')
        if not pagination['has_next'] or sampled >= limits.max_comments_per_post: break
        cursor = pagination.get('next_cursor')
        if not isinstance(cursor, str) or not 0 < len(cursor) <= 1024 or cursor in cursors or any(ord(c) < 32 for c in cursor):
            raise Rejected('comments_schema_invalid')
        cursors.add(cursor)
    summary = sentiment_summary(counts, min_comments=limits.minimum_comments)
    if summary['label'] == 'unknown': raise Rejected('insufficient_classified_comments')
    return summary


def own_posts(engine, now):
    fingerprint = engine.account_fingerprint()
    rows = engine.s.rows('SELECT d.*,p.platform_post_id,p.provider_request_id,p.confirmed_at FROM post_drafts d JOIN platform_posts p ON p.post_id=d.id WHERE d.experiment_id=? AND d.account_fingerprint=? ORDER BY d.id LIMIT 100',
                         (engine.experiment()['id'], fingerprint))
    posts, identities = [], set()
    for row in rows:
        if row['state'] not in {'PUBLISHED','CONFIRMED'}: continue
        try:
            engine.check_draft_account(row)
            account = engine.accounts.binding(row['account_key'])
            confirmed = timestamp(row['confirmed_at'])
            if confirmed > timestamp(now) or confirmed < timestamp(engine.experiment()['start_at']): continue
            if (not isinstance(row['platform_post_id'], str) or not re.fullmatch(r'[0-9]{1,40}', row['platform_post_id'])
                    or not isinstance(row['provider_request_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', row['provider_request_id'])): continue
            identity = (row['platform'], row['platform_post_id'])
            if identity in identities: raise Rejected('duplicate_receipt')
            identities.add(identity)
            posts.append({**row, 'profile_username': account['upload_post_profile'],
                          'native_account_id': account['native_account_id'], 'account_fingerprint': fingerprint})
        except (ValueError, TypeError, RuntimeError):
            continue
    return sorted(posts, key=lambda p: p['id'])[:engine.c.feedback.max_posts_per_collection]


def collect_mcp(engine, now, *, mcp_factory=None, identity_factory=None, refresh_learning=True):
    from .mcp_status import record_result
    try:
        result = _collect_mcp(engine,now,mcp_factory=mcp_factory,identity_factory=identity_factory,refresh_learning=refresh_learning)
    except BaseException as exc:
        # A SIGKILL cannot record this event; CLAIMED remains visible to readers.
        record_result(engine,now,{'status':'interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'unavailable',
                                  'reason':'cycle_interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'cycle_validation_failed'})
        raise
    record_result(engine,now,result)
    return result


def _collect_mcp(engine, now, *, mcp_factory=None, identity_factory=None, refresh_learning=True):
    """One bounded cycle. Factories are host code, never MCP/model input."""
    e, c, s = engine.experiment(), engine.c, engine.s
    result = {'status': 'skipped', 'accepted_posts': 0, 'rejected_posts': 0, 'reasons': {},
              'mcp_calls': 0, 'identity_reads': 0, 'model_calls': 0, 'posts_sent': 0}
    if not c.feedback.enabled or not c.learning.enabled: return {**result, 'reason': 'disabled'}
    if not e['start_at']: return {**result, 'reason': 'no_started_experiment'}
    if e['incident'] or e['status'] not in {'RUNNING','PAUSED'} or timestamp(now) >= timestamp(e['end_at']):
        return {**result, 'reason': 'experiment_not_collectible'}
    budget = engine.budget(now)
    if not engine.simulated and (not budget['billing_verified'] or budget['halt_optional']):
        return {**result, 'reason': 'budget_unverified_or_exhausted'}
    posts = own_posts(engine, now)
    if not posts: return {**result, 'reason': 'no_verified_own_receipts'}
    fingerprint = engine.account_fingerprint()
    bucket = int(timestamp(now).timestamp() // (c.analytics.minimum_collection_hours * 3600))
    key = f'mcp:{fingerprint}:{bucket}'
    s.db.execute('CREATE TABLE IF NOT EXISTS mcp_import_runs (event_key TEXT PRIMARY KEY, state TEXT NOT NULL)')
    if s.one('SELECT state FROM mcp_import_runs WHERE event_key=?', (key,)):
        return {**result, 'reason': 'interval_already_claimed'}
    # Commit the claim before any I/O; interrupted/unknown reads are not retried.
    with s.transaction():
        s.db.execute("INSERT INTO mcp_import_runs VALUES (?,'CLAIMED')", (key,))
        # Invalidate earlier evidence before I/O, including process interruption.
        # A completed response gets the later timestamp in the final transaction.
        for post in posts:
            binding = {k: post[k] for k in ('account_key','account_fingerprint','platform')}
            binding['post_id'] = post['id']
            claimed_at = timestamp(now) - timedelta(microseconds=1)
            s.record('post_metrics_snapshots', key + ':claim:' + post['id'], claimed_at, 'Upload-Post MCP',
                     {**binding, 'metrics': {}, 'ingestion': 'mcp-v1', 'status': 'verification_pending'})
            s.record('sentiment_snapshots', key + ':claim:' + post['id'], claimed_at, 'Upload-Post MCP',
                     {**binding, 'status': 'unavailable', 'partial': None, 'sentiment': sentiment_summary(), 'ingestion': 'mcp-v1'})
    client = identity = None
    observations, verified, reasons = [], set(), Counter()
    provider_failed = False
    deadline = time.monotonic() + 60
    try:
        client = (mcp_factory or (lambda: ReadOnlyMCP(os.environ.get('UPLOAD_POST_API_KEY', ''))))()
        identity = (identity_factory or (lambda: UploadPostAccountClient(os.environ.get('UPLOAD_POST_API_KEY', ''))))()
        if any(bool(getattr(obj, 'simulated', False)) != engine.simulated for obj in (client, identity)):
            raise MCPReadError()
        for post in posts:
            metrics, summary, reason = None, None, None
            stop = False
            try:
                if time.monotonic() > deadline: raise MCPReadError('mcp_limit')
                engine.check_draft_account(post)
                owner = (post['profile_username'], post['platform'], post['native_account_id'])
                if owner not in verified:
                    identity.verify_binding(*owner)
                    verified.add(owner)
                metrics = metrics_for(client.call('get_post_analytics', {'requestId': post['provider_request_id']}), post, now)
                if max(metrics.get(k, 0) for k in ('impressions','views','reach')) < c.feedback.minimum_exposures:
                    raise Rejected('insufficient_exposures')
                summary = comments_for(client, post, c.feedback)
            except Rejected as exc:
                reason = str(exc)  # Only our closed local reasons, no provider text.
            except (MCPReadError, PublisherError, ValueError, TypeError, RuntimeError):
                reason, stop = 'provider_or_identity_unverified', True
                provider_failed = True
            if reason: reasons[reason] += 1
            observations.append((post, metrics if not reason else None, summary if not reason else None, reason))
            if stop:
                # Revoke old eligible evidence for the remaining target posts too.
                for remaining in posts[len(observations):]:
                    observations.append((remaining, None, None, 'cycle_aborted_unverified'))
                    reasons['cycle_aborted_unverified'] += 1
                break
    except (MCPReadError, PublisherError, ValueError, TypeError, RuntimeError):
        provider_failed = True
        observations = [(p, None, None, 'provider_or_identity_unverified') for p in posts]
        reasons = Counter({'provider_or_identity_unverified': len(posts)})
    # A concurrent registry change invalidates the entire cycle before commit.
    engine.account_fingerprint()
    result.update(status='collected', accepted_posts=sum(not row[3] for row in observations),
                  rejected_posts=sum(bool(row[3]) for row in observations), reasons=dict(reasons),
                  mcp_calls=getattr(client, 'calls', 0), identity_reads=getattr(identity, 'network_calls', 0),
                  owner_review_required=provider_failed)
    with s.transaction():
        if provider_failed:
            # Stop future cycles/authorization without invoking cancellation or
            # any other provider write. Existing external jobs need owner review.
            s.db.execute("UPDATE experiments SET status='PAUSED',authorization_hash=NULL,authorized_at=NULL,incident='mcp_read_unverified' WHERE id=?", (e['id'],))
        for post, metrics, summary, reason in observations:
            binding = {k: post[k] for k in ('account_key','account_fingerprint','platform')}
            binding['post_id'] = post['id']
            payload = {**binding, 'hypothesis': post['hypothesis'], 'hook_type': post['hook_type'],
                       'metrics': metrics or {}, 'ingestion': 'mcp-v1', 'status': 'accepted' if not reason else 'excluded'}
            # Empty metrics and null sentiment supersede old accepted snapshots.
            sentiment = {**binding, 'status': 'sampled' if not reason else 'unavailable',
                         'partial': False if not reason else None,
                         'coverage': 'mentions_sample' if post['platform']=='x' else 'reply_sample',
                         'sentiment': summary or sentiment_summary(), 'ingestion': 'mcp-v1'}
            s.record('post_metrics_snapshots', key + ':' + post['id'], now, 'Upload-Post MCP', payload)
            s.record('sentiment_snapshots', key + ':' + post['id'], now, 'Upload-Post MCP', sentiment)
        s.db.execute("UPDATE mcp_import_runs SET state='COMMITTED' WHERE event_key=?", (key,))
        from .mcp_status import record_result
        record_result(engine,now,result)
    if refresh_learning:
        result['learning'] = engine.learn(now)
    return result
