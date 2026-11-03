from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import secrets
import threading

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from .documents import decode_text
from .official import ROOT, Recognizer
from .workflow import Project


class Review(BaseModel):
    revision: int
    entities: dict
    documents: list[dict]


class Export(BaseModel):
    kind: str
    acknowledge: bool = False
    revision: int


class Restore(BaseModel):
    text: str
    fixes: dict[str, str] = {}
    save: bool = False


def create_app(project: Project, launch_token: str):
    model = Recognizer(project.directory)
    lock = threading.RLock()
    cookie = secrets.token_urlsafe(32)
    unused_launch_token = launch_token

    @asynccontextmanager
    async def lifespan(app):
        yield
        model.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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
        nonlocal unused_launch_token
        token = request.headers.get('x-launch-token', '')
        if not unused_launch_token or not secrets.compare_digest(token, unused_launch_token):
            return JSONResponse({'error': '启动链接已使用或无效'}, status_code=403)
        unused_launch_token = None
        response = JSONResponse({'ok': True})
        response.set_cookie('mask_session', cookie, httponly=True, samesite='strict')
        return response

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
            return project.public()

    @app.get('/api/workspace')
    def workspace():
        # ~$ files are Office owner locks created while a document is open, not materials.
        return [{'name': p.name} for p in sorted(project.workspace.iterdir())
                if p.is_file() and p.suffix.lower() in {'.txt', '.docx', '.pdf'} and not p.name.startswith('~$')]

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
            return project.public()

    @app.post('/api/upload')
    def upload(file: UploadFile = File(...)):
        with lock:
            project.add(file.filename or '', file.file.read())
            return project.public()

    @app.post('/api/scan/{identity}')
    def scan(identity: str):
        with lock:
            doc = next((d for d in project.state['documents'] if d['id'] == identity), None)
            if not doc:
                raise ValueError('材料不存在')
            doc.update(status='scanning', error='')
            project.save()
            try:
                project.recognize(doc, model)
            except Exception as exc:
                doc.update(status='failed', error=f'{type(exc).__name__}: {exc}', handed=False)
            project.save()
            return project.public()

    @app.put('/api/review')
    def review(body: Review):
        with lock:
            project.review(body.model_dump())
            return project.public()

    @app.delete('/api/documents/{identity}')
    def remove_document(identity: str, revision: int):
        with lock:
            project.check_revision(revision)
            if not any(d['id'] == identity for d in project.state['documents']):
                raise ValueError('材料不存在')
            project.state['documents'] = [d for d in project.state['documents'] if d['id'] != identity]
            # Historical mappings remain valid for AI results produced before removal.
            project.save()
            return project.public()

    @app.post('/api/export')
    def export(body: Export):
        with lock:
            project.check_revision(body.revision)
            return project.export(body.kind, body.acknowledge)

    @app.post('/api/export-mapping')
    def export_mapping(body: dict):
        with lock:
            project.check_revision(body.get('revision'))
            return project.export_mapping()

    @app.post('/api/export-mapping')
    def export_mapping(body: dict):
        with lock:
            project.check_revision(body.get('revision'))
            return project.export_mapping()

    @app.post('/api/export-mapping')
    def export_mapping(body: dict):
        with lock:
            project.check_revision(body.get('revision'))
            return project.export_mapping()

    @app.post('/api/restore')
    def restore(body: Restore):
        with lock:
            return project.restore_result(body.text, body.fixes, body.save)

    @app.post('/api/read-result')
    def read_result(file: UploadFile = File(...)):
        if Path(file.filename or '').suffix.lower() not in {'.txt', '.md'}:
            raise ValueError('还原结果请提供 TXT 或 Markdown，或直接粘贴文字')
        return {'text': decode_text(file.file.read())[0]}

    @app.get('/api/download/{identity}')
    def download(identity: str):
        with lock:
            item = next((f for f in project.state['outputs'] if f['id'] == identity), None)
            if not item:
                raise ValueError('文件不存在或上次导出未成功')
            return FileResponse(item['path'], filename=item['name'])

    return app
