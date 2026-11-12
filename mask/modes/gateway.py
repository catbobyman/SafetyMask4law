"""Gateway mode: one process on this machine that connected agents send their model requests through.

A connected agent's model address is http://127.0.0.1:8788/<agent>. The gateway replaces what the user
confirmed in the workbench (the vault of the project the agent works in) by tags, refuses a request that
still holds an unmasked checksum-valid number, and forwards the rest to the agent's own model service.
This is the only point that sees typed text, @-references, tool results and compaction alike.

The reply is passed through untouched: real names are restored in the workbench, never in the agent's
conversation. The gateway does not run the recognition model: nobody reviews its decisions.
"""
from __future__ import annotations

from collections import Counter
import json
import os
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

from .. import payload, store
from ..categories import STRICT, pattern_hits
from ..official import ROOT
from ..project import app_dir, data_dir, find
from ..vault import Vault, replacer

PORT = 8788
URL = f'http://127.0.0.1:{PORT}'
# Path prefix of each agent -> the model service it talks to when it is not connected.
AGENTS = {'dsh': 'https://api.deepseek.com/anthropic'}
KEEP_DAYS = 30


class Refused(Exception):
    """A request that is not forwarded. `kind` goes into the record, the message to the agent's user."""
    def __init__(self, kind, message, status=400, **record):
        super().__init__(message)
        self.kind, self.status, self.record = kind, status, record


class Vaults:
    """What to replace in a request, chosen by the agent's working directory; rebuilt when a vault file changes."""
    def __init__(self):
        self.cache = {}

    def of(self, cwd):
        """(project label for the record, {original: tag}, replacer)."""
        if cwd is None:
            # A request without the agent's environment (a session title) can still quote what the user typed,
            # so every project's originals are replaced; the reply to such a request is never restored.
            label, files = '*', list((app_dir() / 'projects').glob('*/vault.mask'))
        else:
            workspace = find(cwd)
            label, files = (data_dir(workspace).name, [data_dir(workspace) / 'vault.mask']) if workspace else (None, [])
        # The same original in two projects takes the tag of the project changed last.
        stamps = sorted(((f.stat().st_mtime_ns, f) for f in files), reverse=True)
        if self.cache.get(label, [None])[0] != stamps:
            forms = {}
            for _, file in stamps:
                for original, tag in Vault(store.read(file)).forms(escaped=True).items():
                    forms.setdefault(original, tag)
            self.cache[label] = (stamps, forms, replacer(forms))
        return label, *self.cache[label][1:]


def prepare(vaults: Vaults, raw: bytes):
    """The request body to forward and what to record about it; raises Refused."""
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise Refused('not-json', '请求不是 JSON，无法检查其中是否含未脱敏内容')
    cwd = payload.working_directory(body)
    label, forms, replace = vaults.of(cwd)
    named = next((tag for original, tag in forms.items() if cwd and original in str(cwd)), None)
    if named:
        # The model only ever sees the tag, so the paths in its tool calls would not exist.
        raise Refused('path', f'工作目录的路径里含有已脱敏的 {named}。模型只见过代号，按这个路径读写文件会失败；'
                              '请把材料放到路径里不含该名称的文件夹再处理', project=label)
    tags, strict = Counter(), set()
    def text(value):
        value, used = replace(value)
        tags.update(used)
        strict.update(kind for _, _, kind in pattern_hits(value, STRICT))
        return value
    body = payload.rewrite(body, text)
    if strict:
        raise Refused('strict', f'请求含未脱敏的{"、".join(sorted(strict))}号码，请先在工作台处理该材料',
                      project=label, strict=sorted(strict))
    return json.dumps(body, ensure_ascii=False).encode('utf-8'), {'project': label, 'tags': dict(tags)}


def log(record):
    """One line per request. Tags, counts and the project's data folder name only: no text, no path."""
    folder = app_dir() / 'gateway'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f'{record["t"][:10]}.jsonl').open('a', encoding='utf-8') as file:
        file.write(json.dumps(record, ensure_ascii=False) + '\n')


def create_app(transport=None):
    import httpx
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    vaults, last = Vaults(), {}
    # A reply may stream for minutes; only connecting is bounded.
    def proxy(upstream):
        # The user's proxy settings are read here: httpx's own reading fails on the IPv6 entry of the NO_PROXY
        # that dsh hands the gateway when it starts it.
        target = urlsplit(upstream)
        return None if transport or proxy_bypass(target.hostname) else getproxies().get(target.scheme)
    clients = {agent: httpx.AsyncClient(base_url=upstream, timeout=httpx.Timeout(None, connect=15), transport=transport,
                                        trust_env=False, proxy=proxy(upstream))
               for agent, upstream in AGENTS.items()}

    def refuse(message, status):
        # Anthropic's error shape; the agent shows the message to the user.
        return JSONResponse({'type': 'error', 'error': {'type': 'invalid_request_error', 'message': f'Mask 已拦截：{message}'}},
                            status_code=status)

    async def health(request):
        # `agents` is when each last sent a request: the panel tells "waiting for a restart" from "in effect" by it.
        return JSONResponse({'agents': last}, headers={'X-Mask': 'gateway'})

    async def forward(request):
        agent, started = request.path_params['agent'], time.monotonic()
        if agent not in clients:
            return refuse(f'网关不认识 /{agent}', 404)
        record = {'t': time.strftime('%Y-%m-%dT%H:%M:%S'), 'agent': agent}
        last[agent] = record['t']
        def done(status, **more):
            log({**record, **more, 'status': status})
        try:
            if request.headers.get('content-encoding'):
                raise Refused('encoding', f'请求体经 {request.headers["content-encoding"]} 压缩，无法检查', 415)
            body = await request.body()
            if body:
                body, found = prepare(vaults, body)
                record.update(found)
        except Refused as refused:
            done(refused.status, refused=refused.kind, **refused.record)
            return refuse(str(refused), refused.status)
        except Exception as exc:
            # A request that could not be checked is not sent.
            traceback.print_exc()
            done(503, refused='error')
            return refuse(f'检查请求时出错，未发送（{type(exc).__name__}）。详见 {app_dir() / "gateway" / "gateway.log"}', 503)
        # The time the gateway itself added, not the model's.
        record['ms'] = round((time.monotonic() - started) * 1000)
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in {'host', 'content-length'}]
        outgoing = clients[agent].build_request(request.method, '/' + request.path_params['path'], params=request.url.query,
                                                headers=headers, content=body)
        try:
            response = await clients[agent].send(outgoing, stream=True)
        except httpx.HTTPError as exc:
            done(502)
            return refuse(f'连接不上模型服务 {AGENTS[agent]}（{type(exc).__name__}）', 502)
        done(response.status_code)
        async def reply():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                # Also when the agent hangs up in the middle of a stream.
                await response.aclose()
        passed = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'transfer-encoding', 'connection'}}
        return StreamingResponse(reply(), status_code=response.status_code, headers=passed)

    return Starlette(routes=[Route('/mask/health', health),
                             Route('/{agent}/{path:path}', forward, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])])


def run():
    import uvicorn
    old = time.strftime('%Y-%m-%d', time.localtime(time.time() - KEEP_DAYS * 86400))
    for file in (app_dir() / 'gateway').glob('*.jsonl'):
        if file.stem < old:
            file.unlink()
    print(f'Mask 网关: {URL}', flush=True)
    uvicorn.run(create_app(), host='127.0.0.1', port=PORT, log_level='warning', access_log=False)


def running():
    import requests
    session = requests.Session()
    session.trust_env = False
    try:
        reply = session.get(f'{URL}/mask/health', timeout=3)
    except requests.ConnectionError:
        return False
    if reply.headers.get('X-Mask') != 'gateway':
        raise SystemExit(f'端口 {PORT} 被别的程序占用，Mask 网关无法启动')
    return True


def start():
    """Make sure the gateway is listening; it keeps running after the caller exits."""
    if running():
        return
    folder = app_dir() / 'gateway'
    folder.mkdir(parents=True, exist_ok=True)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with (folder / 'gateway.log').open('ab') as output:
        process = subprocess.Popen([sys.executable, '-m', 'mask', 'gateway'], cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
    deadline = time.monotonic() + 30
    while not running():
        if process.poll() is not None or time.monotonic() > deadline:
            raise SystemExit(f'Mask 网关未能启动，详见 {folder / "gateway.log"}')
        time.sleep(.2)
