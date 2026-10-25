"""Closed, private import audit records and read-only operator projections."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta
from zoneinfo import ZoneInfo

from .config import timestamp

REASONS = {
    'disabled':'수집 또는 학습 비활성',
    'no_started_experiment':'실험 미시작',
    'experiment_not_collectible':'실험 종료·사고 또는 수집 불가 상태',
    'budget_unverified_or_exhausted':'청구 미확인 또는 예산 중단 기준',
    'no_verified_own_receipts':'검증된 본인 게시 영수증 없음',
    'interval_already_claimed':'같은 수집 구간은 재실행하지 않음',
    'post_metrics_unverified':'글별 지표 미확인',
    'cached_metrics_not_live':'최신 직접 관측이 아닌 캐시',
    'stale_or_future_observation':'만료되거나 미래인 관측 시각',
    'receipt_mismatch':'게시 영수증·글 ID 불일치',
    'metric_support_or_unit_unverified':'지원 지표 또는 노출 단위 미확인',
    'metric_counts_invalid':'지표 숫자 형식 검증 실패',
    'comments_schema_invalid':'댓글 응답 형식 검증 실패',
    'reply_permission_unverified':'Threads 답글 읽기 범위 미확인',
    'mentions_source_unverified':'X 멘션 표본 범위 미확인',
    'insufficient_classified_comments':'분류 가능한 댓글 수 또는 분류율 부족',
    'insufficient_exposures':'글별 노출 표본 부족',
    'provider_or_identity_unverified':'제공자·권한·계정 신원 검증 실패',
    'cycle_aborted_unverified':'읽기 중단으로 나머지 자료 제외',
    'cycle_interrupted':'작업 중단·완료 미확인',
    'cycle_validation_failed':'반입 검증 실패',
    'unrecognized':'확인되지 않은 사유 — 운영자 점검 필요',
}
STATES = {'collected':'반입 완료', 'skipped':'수집 보류', 'interrupted':'작업 중단',
          'unavailable':'상태 확인 필요', 'no_record':'반입 기록 없음'}
FIELDS = {'version','account_fingerprint','simulated','bucket','status','reason','accepted_posts',
          'rejected_posts','reasons','mcp_calls','identity_reads','owner_review_required'}


def _count(value, maximum):
    return value if type(value) is int and 0 <= value <= maximum else None


def record_result(engine, now, result):
    if result.get('reason') == 'interval_already_claimed': return
    status = result.get('status')
    status = status if isinstance(status,str) and status in STATES else 'unavailable'
    raw_reason = result.get('reason')
    reason = raw_reason if isinstance(raw_reason,str) and raw_reason in REASONS else ('unrecognized' if raw_reason is not None else None)
    raw_reasons = result.get('reasons',{})
    reasons = {}
    for key,value in raw_reasons.items() if isinstance(raw_reasons,dict) else []:
        code = key if key in REASONS else 'unrecognized'
        number = _count(value,10)
        if number: reasons[code] = min(10,reasons.get(code,0)+number)
    payload = {'version':1, 'account_fingerprint':engine.accounts.fingerprint(), 'simulated':engine.simulated,
               'bucket':int(timestamp(now).timestamp()//(engine.c.analytics.minimum_collection_hours*3600)),
               'status':status, 'reason':reason, 'accepted_posts':_count(result.get('accepted_posts'),10),
               'rejected_posts':_count(result.get('rejected_posts'),10), 'reasons':reasons,
               'mcp_calls':_count(result.get('mcp_calls'),32), 'identity_reads':_count(result.get('identity_reads'),32),
               'owner_review_required':result.get('owner_review_required') is True or status in {'interrupted','unavailable'}}
    key = 'mcp-audit:'+hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    engine.s.record('mcp_import_events',key,now,'MCP import audit',payload)


def read_status(engine, now):
    empty = {'status':'no_record','reason':None,'accepted_posts':None,'rejected_posts':None,
             'reasons':{},'mcp_calls':None,'identity_reads':None,'owner_review_required':False,
             'observed_at':None,'stale':False}
    try:
        fp = engine.account_fingerprint()
        tables = {r['name'] for r in engine.s.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        row = None
        if 'mcp_import_events' in tables:
            row = engine.s.one("SELECT observed_at,payload FROM mcp_import_events WHERE CASE WHEN json_valid(payload) THEN json_extract(payload,'$.account_fingerprint') END=? ORDER BY id DESC LIMIT 1",(fp,))
        result = dict(empty)
        if row:
            if len(row['payload']) > 8192: raise ValueError()
            data = json.loads(row['payload'])
            if (not isinstance(data,dict) or set(data)!=FIELDS or type(data['version']) is not int or data['version']!=1
                    or data['account_fingerprint']!=fp or data['simulated'] is not engine.simulated
                    or not isinstance(data['status'],str) or data['status'] not in STATES
                    or (data['reason'] is not None and (not isinstance(data['reason'],str) or data['reason'] not in REASONS))
                    or _count(data['bucket'],10**10) is None or type(data['owner_review_required']) is not bool
                    or not isinstance(data['reasons'],dict) or any(k not in REASONS or _count(v,10) is None for k,v in data['reasons'].items())
                    or any(data[k] is not None and _count(data[k],bound) is None for k,bound in
                           (('accepted_posts',10),('rejected_posts',10),('mcp_calls',32),('identity_reads',32)))):
                raise ValueError()
            observed = timestamp(row['observed_at'])
            if observed > timestamp(now): raise ValueError()
            result.update({k:data[k] for k in empty if k in data})
            result['observed_at']=observed.isoformat()
            result['stale']=timestamp(now)-observed > timedelta(hours=48)
        if 'mcp_import_runs' in tables:
            claim=engine.s.one("SELECT state FROM mcp_import_runs WHERE event_key LIKE ? ORDER BY rowid DESC LIMIT 1",('mcp:'+fp+':%',))
            if claim and claim['state']=='CLAIMED':
                if result['status']!='interrupted': result.update(observed_at=None,stale=False)
                result.update(status='interrupted',reason='cycle_interrupted',accepted_posts=None,rejected_posts=None,
                              reasons={},mcp_calls=None,identity_reads=None,owner_review_required=True)
        if result['status']=='no_record' and not engine.experiment()['start_at']:
            result['reason']='no_started_experiment'
        return result
    except (ValueError,TypeError,KeyError,AttributeError,RuntimeError,OSError,sqlite3.Error):
        return {**empty,'status':'unavailable','reason':'cycle_validation_failed','owner_review_required':True}


def time_label(value):
    if not value: return '기록 없음'
    return timestamp(value).astimezone(ZoneInfo('Asia/Seoul')).strftime('%m-%d %H:%M KST')


def status_text(data):
    def number(value): return '미확인' if value is None else str(value)
    text=(f"MCP 반입: {STATES[data['status']]}\n최근 기록: {time_label(data['observed_at'])}"
          f"{' · 48시간 초과 과거 기록' if data['stale'] else ''}\n"
          f"최근 회차 반입 {number(data['accepted_posts'])}개 · 제외 {number(data['rejected_posts'])}개\n"
          f"MCP 요청 {number(data['mcp_calls'])}회 · 신원 조회 {number(data['identity_reads'])}회")
    if data['reason']: text+='\n'+REASONS[data['reason']]
    if data['owner_review_required']: text+='\n운영자 점검 필요 · 자동 재실행하지 않습니다.'
    return text+'\n과거 반입 건수는 현재 학습 사용 가능 건수가 아닙니다. /sentiment에서 확인하세요.'


def excluded_text(data):
    text=f"MCP 학습 제외 사유\n대상: 최근 기록 {time_label(data['observed_at'])}"
    if data['stale']: text+=' · 과거 기록'
    if data['reason']: text+='\n수집 상태: '+REASONS[data['reason']]
    if data['reasons']:
        for code,number in sorted(data['reasons'].items()): text+=f'\n- {REASONS[code]}: {number}개 글'
    elif data['status']=='collected': text+='\n최근 회차의 글별 제외 기록 없음.'
    else: text+='\n확인된 글별 제외 집계 없음. 자료가 충분하거나 권한이 있다는 뜻은 아닙니다.'
    return text+'\n표본 수치는 댓글 수나 사람 수가 아니라 제외된 글 수입니다.'
