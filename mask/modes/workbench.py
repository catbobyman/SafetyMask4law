"""Workbench mode: the local browser application for importing, reviewing, exporting and restoring.

`host=True` is the same workbench opened from a host skill: exporting Markdown also hands it to the workspace.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import webbrowser

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from .. import store
from ..documents import decode_text, result_text
from ..official import ROOT, Recognizer, normalize_mapping_dict
from ..project import Project, initial_scope, save_initial_scope
from . import gateway, install


class Review(BaseModel):
    revision: int
    entities: dict
    documents: list[dict]


class Export(BaseModel):
    kind: str
    acknowledge: bool = False
    revision: int


class Categories(BaseModel):
    categories: list[str]


class Scope(Categories):
    revision: int


class Switch(BaseModel):
    enabled: bool


class Restore(BaseModel):
    text: str
    fixes: dict[str, str] = {}
    save: bool = False
    # An imported official mapping file, used for this restore only instead of the project mapping.
    mapping: dict | None = None


def create_app(project: Project, launch_token: str, host: bool = False, control_key: str | None = None):
    model = Recognizer(project.directory)
    lock = threading.RLock()
    cookie = secrets.token_urlsafe(32)
    launch_tokens = {launch_token}

    @asynccontextmanager
    async def lifespan(app):
        yield
        model.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def hosts():
        # Agents the skill and the guard can be switched on for. Machine-wide, not per project.
        return [{'id': 'dsh', 'enabled': install.installed()}]

    def public():
        return {**project.public(), 'host': host, 'default_scope': initial_scope(), 'hosts': hosts()}

    def panel():
        """The Agent panel: machine-wide state read live from the gateway, dsh's patch file and today's records."""
        try:
            last, problem = gateway.health(), None
        except SystemExit as exc:
            last, problem = None, str(exc)
        enabled, today = install.installed(), gateway.records(time.strftime('%Y-%m-%d'))
        patch = install.dsh_home() / 'cordis.patch.yml'
        # `pending`: Mask's block changed, but dsh has not sent a request since, so it may still run on its old
        # settings. Today's records also count, so a gateway restart does not forget dsh's last request.
        heard = max([(last or {}).get('dsh', ''), *(r['t'] for r in today if r.get('agent') == 'dsh')])
        state = 'off' if not enabled else 'down' if last is None else 'live' if heard >= install.written() else 'pending'
        return {'gateway': {'running': last is not None, 'problem': problem, 'requests': len(today),
                            'replaced': sum(sum(r.get('tags', {}).values()) for r in today),
                            'refused': sum('refused' in r for r in today)},
                'agents': [{'id': 'dsh', 'state': state, 'blocked': None if enabled else install.blocked(),
                            'file': str(patch), 'upstream': gateway.AGENTS['dsh']}]}

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        if request.url.hostname != '127.0.0.1':
            return JSONResponse({'error': '只接受本机地址'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': '拒绝跨站请求'}, status_code=403)
        if request.url.path.startswith('/api/'):
            if not secrets.compare_digest(request.cookies.get('mask_session', ''), cookie):
                return JSONResponse({'error': '会话已失效，请使用启动窗口中的链接重新打开'}, status_code=401)
            if request.method != 'GET' and request.headers.get('x-mask') != '1':
                return JSONResponse({'error': '缺少工作台请求标记'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'error': str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def failed(request, exc):
        # Keep an explicit failure at the UI boundary; do not substitute empty results.
        return JSONResponse({'error': f'{type(exc).__name__}: {exc}'}, status_code=500)

    @app.post('/session')
    def session(request: Request):
        token = request.headers.get('x-launch-token', '')
        if token not in launch_tokens:
            return JSONResponse({'error': '启动链接已使用或无效'}, status_code=403)
        launch_tokens.discard(token)
        response = JSONResponse({'ok': True})
        response.set_cookie('mask_session', cookie, httponly=True, samesite='strict')
        return response

    @app.post('/control/open')
    def reopen(request: Request):
        # The skill command asks a running workbench to open itself again. The one-time link goes
        # straight to the browser, so it never appears in the host conversation.
        if not control_key or not secrets.compare_digest(request.headers.get('x-mask-control', ''), control_key):
            return JSONResponse({'error': '无效的控制请求'}, status_code=403)
        token = secrets.token_urlsafe(32)
        launch_tokens.add(token)
        webbrowser.open(f'{str(request.base_url)}#{token}')
        return {'ok': True}

    @app.get('/')
    def index():
        html = (ROOT / 'prototype/mask.html').read_text(encoding='utf-8')
        html = html.replace('<head>', '<head><script>window.MASK_LIVE=true;</script>')
        return HTMLResponse(html.replace('</body>', '<script src="/live.js"></script></body>'))

    @app.get('/live.js')
    def script():
        return FileResponse(ROOT / 'prototype/live.js', media_type='text/javascript')

    @app.get('/api/project')
    def state():
        with lock:
            return public()

    @app.get('/api/workspace')
    def workspace():
        # ~$ files are Office owner locks created while a document is open, not materials.
        return [{'name': p.name, 'supported': p.suffix.lower() in {'.txt', '.md', '.docx', '.pdf'}}
                for p in sorted(project.workspace.iterdir())
                if p.is_file() and p.suffix.lower() in {'.txt', '.md', '.docx', '.pdf', '.doc'} and not p.name.startswith('~$')]

    @app.post('/api/import-local')
    def import_local(body: dict):
        name = body.get('name', '')
        if not name or Path(name).name != name:
            raise ValueError('请选择工作目录中列出的文件')
        path = project.workspace / name
        if path.resolve().parent != project.workspace:
            raise ValueError('文件不在当前工作目录')
        with lock:
            project.add(name, path.read_bytes(), path)
            return public()

    @app.post('/api/upload')
    def upload(file: UploadFile = File(...)):
        with lock:
            project.add(file.filename or '', file.file.read())
            return public()

    @app.put('/api/scope')
    def scope(body: Scope):
        with lock:
            project.check_revision(body.revision)
            project.set_scope(body.categories)
            return public()

    @app.put('/api/default-scope')
    def default_scope(body: Categories):
        # The scope new projects start with; this project is not changed.
        save_initial_scope(body.categories)
        return {'default_scope': initial_scope()}

    @app.put('/api/hosts/dsh')
    def switch_host(body: Switch):
        try:
            install.install() if body.enabled else install.uninstall()
        except SystemExit as exc:
            raise ValueError(str(exc)) from exc
        return {'hosts': hosts()}

    @app.get('/api/hosts')
    def host_panel():
        return panel()

    @app.get('/api/hosts/dsh/preview')
    def host_preview(enabled: bool):
        try:
            return install.preview(enabled)
        except SystemExit as exc:
            raise ValueError(str(exc)) from exc

    @app.post('/api/gateway/start')
    def gateway_start():
        try:
            gateway.start()
        except SystemExit as exc:
            raise ValueError(str(exc)) from exc
        return panel()

    @app.get('/api/gateway/log')
    def gateway_log():
        # Newest first. `here` lets the page say which records belong to the project it has open.
        return {'here': project.directory.name, 'records': gateway.records(time.strftime('%Y-%m-%d'))[::-1][:300]}

    @app.post('/api/scan/{identity}')
    def scan(identity: str):
        with lock:
            doc = project.document(identity)
            doc.update(status='scanning', error='')
            project.save()
            try:
                project.recognize(doc, model)
            except Exception as exc:
                doc.update(status='failed', error=f'{type(exc).__name__}: {exc}', handed=False)
            project.save()
            return public()

    @app.put('/api/review')
    def review(body: Review):
        with lock:
            project.review(body.model_dump())
            return public()

    @app.delete('/api/documents/{identity}')
    def remove_document(identity: str, revision: int):
        with lock:
            project.check_revision(revision)
            project.remove(identity)
            return public()

    @app.post('/api/export')
    def export(body: Export):
        with lock:
            project.check_revision(body.revision)
            return project.export(body.kind, body.acknowledge, handoff=body.kind == 'markdown')

    @app.post('/api/export-mapping')
    def export_mapping(body: dict):
        with lock:
            project.check_revision(body.get('revision'))
            return project.export_mapping()

    @app.post('/api/restore')
    def restore(body: Restore):
        with lock:
            return project.restore_result(body.text, body.fixes, body.save, body.mapping)

    @app.post('/api/read-result')
    def read_result(file: UploadFile = File(...)):
        ext = Path(file.filename or '').suffix.lower()
        if ext not in {'.txt', '.md', '.docx'}:
            raise ValueError('还原结果请提供 TXT、Markdown 或 Word（.docx），或直接粘贴文字')
        data = file.file.read()
        return {'text': result_text(data) if ext == '.docx' else decode_text(data)[0]}

    @app.post('/api/restore-word')
    def restore_word(file: UploadFile = File(...), choices: str = Form(...)):
        # choices is the JSON of {fixes, mapping}, the same two fields as /api/restore.
        chosen = json.loads(choices)
        with lock:
            return project.restore_word(file.file.read(), chosen['fixes'], chosen['mapping'])

    @app.post('/api/read-mapping')
    def read_mapping(file: UploadFile = File(...)):
        if Path(file.filename or '').suffix.lower() != '.json':
            raise ValueError('映射文件请提供 .json，例如导出的 <目录名>.mapping.json')
        try:
            data = json.loads(decode_text(file.file.read())[0])
            # Same layouts as official load_mapping, without its treat-text-as-a-path lookup.
            if isinstance(data, dict) and isinstance(data.get('mapping'), dict):
                data = data['mapping']
            return {'name': Path(file.filename).name, 'mapping': normalize_mapping_dict(data)}
        except ValueError as exc:
            raise ValueError(f'不是有效的映射文件：{exc}') from exc

    @app.get('/api/download/{identity}')
    def download(identity: str):
        with lock:
            item = next((f for f in project.state['outputs'] if f['id'] == identity), None)
            if not item:
                raise ValueError('文件不存在或上次导出未成功')
            return FileResponse(item['path'], filename=item['name'])

    return app


def run(workspace: Path, port: int = 0, browser: bool = True, host: bool = False):
    project = Project(workspace)
    # One writer per project; another terminal must not overwrite persisted review state.
    guard = (project.directory / 'process.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        guard.seek(0); guard.write(b'0'); guard.flush(); guard.seek(0)
        try:
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise SystemExit('这个项目的工作台已经运行，请使用原来的窗口') from exc
    token, control_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    control = project.directory / 'control.mask'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))
        port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}/#{token}'
        print(f'Mask: {url}', flush=True)
        print(f'项目数据（仅本机）: {project.directory}', flush=True)
        store.write(control, {'port': port, 'key': control_key})
        if browser:
            # Open after uvicorn starts listening on the bound socket.
            threading.Timer(1, lambda: webbrowser.open(url)).start()
        app = create_app(project, token, host, control_key)
        try:
            uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False)).run(sockets=[sock])
        finally:
            control.unlink(missing_ok=True)
    guard.close()
