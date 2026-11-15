"""Gateway mode: one process on this machine that connected agents send their model requests through.

A connected agent's model address is http://127.0.0.1:8788/<agent>. The gateway replaces what the user
confirmed in the workbench (the vault of the project the agent works in) by tags, refuses a request that
still holds an unmasked checksum-valid number, and forwards the rest to the agent's own model service.
This is the only point that sees typed text, @-references, tool results and compaction alike.

The reply is passed through untouched: real names are restored in the workbench, never in the agent's
conversation. The gateway does not run the recognition model: nobody reviews its decisions.
"""
from __future__ import annotations

from fnmatch import fnmatch
import json
import os
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import getproxies, getproxies_environment, proxy_bypass

from .. import payload, store
from ..project import app_dir, data_dir, find
from ..vault import Vault, replacer
from . import launch
from .guard import checker

PORT = 8788
URL = f'http://127.0.0.1:{PORT}'
# Path prefix of each agent -> the model service it talks to when it is not connected.
# Codex signed in with a ChatGPT account talks to chatgpt.com; with an API key it would be api.openai.com/v1.
AGENTS = {'dsh': 'https://api.deepseek.com/anthropic', 'codex': 'https://chatgpt.com/backend-api/codex'}
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
        workspace = find(cwd) if cwd is not None else None
        if workspace:
            label, files = data_dir(workspace).name, [data_dir(workspace) / 'vault.mask']
        else:
            # A request without the agent's environment (a session title), or from a folder outside every project,
            # can still quote what the user typed or read, so every project's originals are replaced; the reply
            # to such a request is never restored.
            label, files = '*' if cwd is None else None, list((app_dir() / 'projects').glob('*/vault.mask'))
        # The same original in two projects takes the tag of the project changed last.
        stamps = sorted(((f.stat().st_mtime_ns, f) for f in files), reverse=True)
        if self.cache.get(label, [None])[0] != stamps:
            forms = {}
            for _, file in stamps:
                for original, tag in Vault(store.read(file)).forms(escaped=True).items():
                    forms.setdefault(original, tag)
            self.cache[label] = (stamps, forms, replacer(forms))
        return label, *self.cache[label][1:]


def bypassed(host):
    """Whether the user's proxy settings exclude `host`. For the Windows settings urllib's own check also looks
    the host up in reverse DNS, which took 16 seconds per upstream here and kept the gateway from starting in
    time; an upstream is a public name, so the exclusion list is matched against the name only."""
    if os.name != 'nt' or getproxies_environment():
        return proxy_bypass(host)
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
        try:
            excluded = str(winreg.QueryValueEx(key, 'ProxyOverride')[0])
        except FileNotFoundError:
            return False
    return any('.' not in host if pattern == '<local>' else fnmatch(host, pattern) for pattern in excluded.split(';') if pattern)


def prepare(vaults: Vaults, agent: str, raw: bytes, zstd: bool):
    """The request body to forward and what to record about it; raises Refused."""
    if zstd:
        # Codex compresses its requests; the frame does not state its size, hence the streaming reader.
        import zstandard
        raw = zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise Refused('not-json', '请求不是 JSON，无法检查其中是否含未脱敏内容')
    cwd = payload.working_directory(body, agent)
    label, forms, replace = vaults.of(cwd)
    named = next((tag for original, tag in forms.items() if cwd and original in str(cwd)), None)
    if named:
        # The model only ever sees the tag, so the paths in its tool calls would not exist.
        raise Refused('path', f'工作目录的路径里含有已脱敏的 {named}。模型只见过代号，按这个路径读写文件会失败；'
                              '请把材料放到路径里不含该名称的文件夹再处理', project=label)
    text, tags, strict = checker(replace)
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
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    vaults, last = Vaults(), {}
    # A reply may stream for minutes; only connecting is bounded.
    def proxy(upstream):
        # The user's proxy settings are read here: httpx's own reading fails on the IPv6 entry of the NO_PROXY
        # that dsh hands the gateway when it starts it.
        target = urlsplit(upstream)
        return None if transport or bypassed(target.hostname) else getproxies().get(target.scheme)
    # The reply is passed on as it arrives, so it may only be compressed in a way the agent itself asked for;
    # httpx would otherwise ask for its own (Codex could not read its model list).
    clients = {agent: httpx.AsyncClient(base_url=upstream, timeout=httpx.Timeout(None, connect=15), transport=transport,
                                        trust_env=False, proxy=proxy(upstream), headers={'Accept-Encoding': 'identity'})
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
        if request.headers.get('upgrade', '').lower() == 'websocket':
            # Codex tries a WebSocket first and falls back to plain requests at once on this answer.
            return refuse('网关不支持 WebSocket', 426)
        record = {'t': time.strftime('%Y-%m-%dT%H:%M:%S'), 'agent': agent}
        last[agent] = record['t']
        body = await request.body()
        # Checking and logging run off the event loop, so the replies streaming meanwhile are not held up.
        async def done(status, **more):
            # A request without a body carries nothing of the user's (Codex fetches its model list every start).
            if body:
                await run_in_threadpool(log, {**record, **more, 'status': status})
        try:
            encoding = request.headers.get('content-encoding')
            if encoding not in (None, 'zstd'):
                raise Refused('encoding', f'请求体经 {encoding} 压缩，无法检查', 415)
            if body:
                body, found = await run_in_threadpool(prepare, vaults, agent, body, bool(encoding))
                record.update(found)
        except Refused as refused:
            await done(refused.status, refused=refused.kind, **refused.record)
            return refuse(str(refused), refused.status)
        except Exception as exc:
            # A request that could not be checked is not sent.
            traceback.print_exc()
            await done(503, refused='error')
            return refuse(f'检查请求时出错，未发送（{type(exc).__name__}）。详见 {app_dir() / "gateway" / "gateway.log"}', 503)
        # The time the gateway itself added, not the model's.
        record['ms'] = round((time.monotonic() - started) * 1000)
        # The body goes out uncompressed, whatever it came in as.
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in {'host', 'content-length', 'content-encoding'}]
        outgoing = clients[agent].build_request(request.method, '/' + request.path_params['path'], params=request.url.query,
                                                headers=headers, content=body)
        try:
            response = await clients[agent].send(outgoing, stream=True)
        except httpx.HTTPError as exc:
            await done(502)
            return refuse(f'连接不上模型服务 {AGENTS[agent]}（{type(exc).__name__}）', 502)
        await done(response.status_code)
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
    # Without a WebSocket implementation an upgrade request reaches `forward` as a plain one, which answers it.
    uvicorn.run(create_app(), host='127.0.0.1', port=PORT, log_level='warning', access_log=False, ws='none')


def health():
    """When each agent last sent a request since the gateway started, or None when it is not listening."""
    import requests
    session = requests.Session()
    session.trust_env = False
    try:
        reply = session.get(f'{URL}/mask/health', timeout=3)
    except requests.ConnectionError:
        return None
    except requests.Timeout:
        raise SystemExit(f'端口 {PORT} 上的程序 3 秒内没有应答，Mask 网关可能卡住了，详见 {app_dir() / "gateway" / "gateway.log"}')
    if reply.headers.get('X-Mask') != 'gateway':
        raise SystemExit(f'端口 {PORT} 被别的程序占用，Mask 网关无法启动')
    return reply.json()['agents']


def running():
    return health() is not None


def records(day: str):
    """The request records of one day, oldest first."""
    file = app_dir() / 'gateway' / f'{day}.jsonl'
    # The gateway may be writing the last line right now: only lines that end in a newline are complete.
    return [json.loads(line) for line in file.read_text(encoding='utf-8').split('\n')[:-1]] if file.exists() else []


def start():
    """Make sure the gateway is listening; it keeps running after the caller exits."""
    if running():
        return
    folder = app_dir() / 'gateway'
    folder.mkdir(parents=True, exist_ok=True)
    process = launch(['gateway'], folder / 'gateway.log')
    deadline = time.monotonic() + 30
    while not running():
        if process.poll() is not None or time.monotonic() > deadline:
            raise SystemExit(f'Mask 网关未能启动，详见 {folder / "gateway.log"}')
        time.sleep(.2)
