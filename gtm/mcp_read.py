"""Minimal, bounded Upload-Post MCP reader. No arbitrary tools or destinations."""
from __future__ import annotations

import json
import re
import time
from urllib import request, error

ENDPOINT = 'https://mcp.upload-post.com/mcp'
VERSION = '2025-03-26'


class MCPReadError(RuntimeError):
    def __init__(self, code='mcp_read_failed'):
        self.code = code if code in {'mcp_read_failed', 'mcp_rate_limited', 'mcp_protocol_invalid', 'mcp_limit'} else 'mcp_read_failed'
        super().__init__(self.code)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def http(headers, payload):
    """JSON and finite SSE responses only; no GET stream, retries or redirects."""
    req = request.Request(ENDPOINT, headers=headers, data=json.dumps(payload, allow_nan=False).encode(), method='POST')
    try:
        with request.build_opener(NoRedirect).open(req, timeout=12) as response:
            session = response.headers.get('Mcp-Session-Id')
            if response.status == 202:
                return {'status': 202, 'session': session, 'body': None}
            content_type = response.headers.get_content_type()
            if content_type == 'application/json':
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576: raise MCPReadError('mcp_limit')
                body = json.loads(raw)
            elif content_type == 'text/event-stream':
                used, pieces, body, deadline = 0, [], None, time.monotonic() + 12
                while used <= 1_048_576 and time.monotonic() < deadline:
                    line = response.readline(65537)
                    used += len(line)
                    if used > 1_048_576: raise MCPReadError('mcp_limit')
                    if not line or len(line) > 65536: break
                    if line.startswith(b'data:'): pieces.append(line[5:].strip())
                    if not line.strip() and pieces:
                        item = json.loads(b'\n'.join(pieces))
                        pieces = []
                        # Server-to-client tool calls, prompts and notifications
                        # are never executed. Only our matching response is read.
                        if isinstance(item, dict) and item.get('id') == payload.get('id') and 'method' not in item:
                            body = item
                            break
                if body is None: raise MCPReadError('mcp_protocol_invalid')
            else:
                raise MCPReadError('mcp_protocol_invalid')
            return {'status': response.status, 'session': session, 'body': body}
    except error.HTTPError as exc:
        raise MCPReadError('mcp_rate_limited' if exc.code == 429 else 'mcp_read_failed') from None
    except MCPReadError:
        raise
    except Exception:
        raise MCPReadError() from None


class ReadOnlyMCP:
    simulated = False

    def __init__(self, api_key, transport=None):
        if not isinstance(api_key, str) or not api_key or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise MCPReadError()
        self._key, self._transport = api_key, transport or http
        self._session, self._ready, self.calls = None, False, 0
        self._deadline = time.monotonic() + 60

    def _rpc(self, method, params=None, *, notification=False):
        if self.calls >= 32 or time.monotonic() > self._deadline: raise MCPReadError('mcp_limit')
        self.calls += 1
        payload = {'jsonrpc': '2.0', 'method': method}
        if params is not None: payload['params'] = params
        if not notification: payload['id'] = self.calls
        headers = {'Authorization': 'ApiKey ' + self._key, 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': VERSION}
        if self._session: headers['Mcp-Session-Id'] = self._session
        try:
            result = self._transport(headers, payload)
            if result['status'] == 429: raise MCPReadError('mcp_rate_limited')
            session = result.get('session')
            if session is not None:
                if not isinstance(session, str) or not re.fullmatch(r'[!-~]{1,200}', session): raise ValueError()
                if self._session is not None and session != self._session: raise ValueError()
                self._session = session
            if notification:
                if result['status'] != 202: raise ValueError()
                return None
            body = result['body']
            if (result['status'] != 200 or not isinstance(body, dict) or body.get('jsonrpc') != '2.0'
                    or type(body.get('id')) is not int or body['id'] != payload['id']
                    or 'method' in body or 'error' in body or 'result' not in body): raise ValueError()
            return body['result']
        except MCPReadError:
            raise
        except Exception:
            raise MCPReadError('mcp_protocol_invalid') from None

    def call(self, name, args):
        if not isinstance(args, dict): raise MCPReadError('mcp_protocol_invalid')
        if name == 'get_post_analytics':
            valid = set(args) == {'requestId'} and isinstance(args['requestId'], str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,200}', args['requestId']))
        elif name == 'get_post_comments':
            valid = (set(args) in ({'user','platform','postId','limit'}, {'user','platform','postId','limit','after'})
                     and args.get('platform') in {'x','threads'} and isinstance(args.get('user'), str)
                     and bool(re.fullmatch(r'[A-Za-z0-9_@-]{1,200}', args['user']))
                     and isinstance(args.get('postId'), str) and bool(re.fullmatch(r'[0-9]{1,40}', args['postId']))
                     and type(args.get('limit')) is int and 1 <= args['limit'] <= 40
                     and ('after' not in args or (isinstance(args['after'], str) and 0 < len(args['after']) <= 1024
                          and all(32 <= ord(c) <= 126 for c in args['after']))))
        else:
            valid = False
        if not valid: raise MCPReadError('mcp_protocol_invalid')
        if not self._ready:
            init = self._rpc('initialize', {'protocolVersion': VERSION, 'capabilities': {},
                                         'clientInfo': {'name': 'disanchor-read-only', 'version': '1.0'}})
            if not isinstance(init, dict) or init.get('protocolVersion') != VERSION: raise MCPReadError('mcp_protocol_invalid')
            self._rpc('notifications/initialized', notification=True)
            self._ready = True
        result = self._rpc('tools/call', {'name': name, 'arguments': args})
        if not isinstance(result, dict) or result.get('isError', False) is not False: raise MCPReadError()
        structured = result.get('structuredContent')
        try:
            if isinstance(structured, dict) and isinstance(structured.get('result'), dict): return structured['result']
            blocks = result.get('content')
            if not isinstance(blocks, list) or len(blocks) != 1 or blocks[0].get('type') != 'text': raise ValueError()
            text = blocks[0]['text']
            if not isinstance(text, str) or len(text.encode()) > 1_048_576: raise ValueError()
            data = json.loads(text)
            if not isinstance(data, dict): raise ValueError()
            return data
        except Exception:
            raise MCPReadError('mcp_protocol_invalid') from None
