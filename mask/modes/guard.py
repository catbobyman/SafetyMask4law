"""Guard mode: keep known originals out of what a host sends to its cloud model.

Two interception points share one check (`scrub`):
- hook: the host runs `python -m mask guard hook` before a prompt is submitted or a tool is used.
  A hook can refuse, not rewrite.
- proxy: the host's model endpoint points at `python -m mask guard proxy`; every request body is
  rewritten before it is forwarded. This is the only point that sees typed text, @-references,
  tool results and compaction alike.

The guard knows what the user already confirmed in the workbench (the vault) and values with a
checksum. It does not run the recognition model: nobody reviews its decisions.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .. import store
from ..categories import STRICT, pattern_hits
from ..project import Project, data_dir, find
from ..vault import Vault


def scrub(value, vault: Vault):
    """Replace known originals in every string of a JSON value.

    Returns (value, replaced, strict) where strict lists categories of unmasked checksum-valid numbers left in it.
    """
    replaced, strict = 0, set()
    def walk(v):
        nonlocal replaced
        if isinstance(v, str):
            v, n = vault.redact(v, escaped=True)
            replaced += n
            strict.update(kind for _, _, kind in pattern_hits(v, STRICT))
            return v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    return walk(value), replaced, sorted(strict)


def load_vault(workspace: Path):
    return Vault(store.read(data_dir(workspace) / 'vault.mask'))


def hook(event: dict):
    """Decide one host hook event (Claude Code / Codex hook JSON). Returns the JSON to print, or None to allow."""
    workspace = find(Path(event.get('cwd') or '.'))
    if not workspace:
        return None
    name = event.get('hook_event_name')
    if name == 'UserPromptSubmit':
        prompt = event.get('prompt', '')
        vault = load_vault(workspace)
        known = sorted({o for o in vault.mapping.values() if o in prompt}, key=len, reverse=True)
        _, _, strict = scrub(prompt, vault)
        if not known and not strict:
            return None
        # The host shows this reason to the user and drops the prompt; it is not sent to the model.
        lines = [f'  {o} → {t}' for t, o in vault.mapping.items() if o in known]
        reason = 'Mask 已拦截：这句话含有已脱敏的真实信息，未发送。请改用代号：\n' + '\n'.join(lines) if known else ''
        if strict:
            reason += ('\n' if reason else 'Mask 已拦截：') + f'含有未脱敏的{"、".join(strict)}号码，请先在工作台处理该材料。'
        return {'decision': 'block', 'reason': reason}
    if name == 'PreToolUse':
        project = Project(workspace)
        private = {Path(d['origin']).resolve() for d in project.state['documents'] if d.get('origin')}
        tool_input = event.get('tool_input') or {}
        targets = {(Path(event.get('cwd') or '.') / tool_input[k]).resolve()
                   for k in ('file_path', 'path', 'notebook_path') if isinstance(tool_input.get(k), str)}
        command = tool_input.get('command') if isinstance(tool_input.get('command'), str) else ''
        inside = any(project.directory == t or project.directory in t.parents for t in targets)
        if targets & private or inside or any(p.name in command for p in private) or str(project.directory) in command:
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                    'permissionDecisionReason': 'Mask：这是已脱敏材料的原件或映射，不能读取。请读取 mask-handoff/ 里的脱敏稿。'}}
    return None


def run_hook():
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = hook(json.loads(sys.stdin.buffer.read().decode('utf-8')))
    except Exception as exc:
        # Exit code 2 makes the host refuse the action: a guard that cannot check does not let it through.
        print(f'Mask 拦截检查失败，已阻止本次操作：{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
    if result:
        print(json.dumps(result, ensure_ascii=False))


def create_proxy(upstream: str, vault_source, transport=None):
    import httpx
    from starlette.applications import Starlette
    from starlette.background import BackgroundTask
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    # A reply may stream for minutes; only connecting is bounded.
    client = httpx.AsyncClient(base_url=upstream, timeout=httpx.Timeout(None, connect=15), transport=transport)

    def refuse(message, status=400):
        # Shape read by both Anthropic and OpenAI clients; the host shows the message to the user.
        return JSONResponse({'type': 'error', 'error': {'type': 'invalid_request_error', 'message': message}}, status_code=status)

    async def forward(request):
        body = await request.body()
        if body:
            try:
                data = json.loads(body)
            except ValueError:
                return refuse('Mask 已拦截：请求不是 JSON，无法检查其中是否含未脱敏内容')
            data, _, strict = scrub(data, vault_source())
            if strict:
                return refuse(f'Mask 已拦截：请求含未脱敏的{"、".join(strict)}号码，请先在工作台处理该材料')
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in {'host', 'content-length'}]
        outgoing = client.build_request(request.method, request.url.path, params=request.url.query, headers=headers, content=body)
        try:
            response = await client.send(outgoing, stream=True)
        except httpx.HTTPError as exc:
            return refuse(f'Mask 代理连接不上模型服务 {upstream}：{type(exc).__name__}', 502)
        # The reply is passed through untouched: real names are restored in the workbench, never in the host conversation.
        passed = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'transfer-encoding', 'connection'}}
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code, headers=passed,
                                 background=BackgroundTask(response.aclose))

    return Starlette(routes=[Route('/{path:path}', forward, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])])


def run_proxy(workspace: Path, upstream: str, port: int):
    import uvicorn
    file = data_dir(workspace) / 'vault.mask'
    if not file.exists():
        raise SystemExit('这个目录还没有 Mask 项目，请先在工作台处理材料')
    cache = {}
    def vault():
        # The workbench keeps confirming new subjects while the proxy runs.
        stamp = file.stat().st_mtime_ns
        if cache.get('stamp') != stamp:
            cache.update(stamp=stamp, vault=Vault(store.read(file)))
        return cache['vault']
    print(f'Mask 拦截代理: http://127.0.0.1:{port} → {upstream}', flush=True)
    uvicorn.run(create_proxy(upstream, vault), host='127.0.0.1', port=port, log_level='warning', access_log=False)
