from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import socket
import webbrowser

import uvicorn

from .server import create_app
from .workflow import Project


def main():
    parser = argparse.ArgumentParser(description='Mask 本地脱敏工作台')
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    project = Project(args.workspace)
    # One writer per project; another terminal must not overwrite persisted review state.
    guard = (project.directory / 'process.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        guard.seek(0); guard.write(b'0'); guard.flush(); guard.seek(0)
        try:
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise SystemExit('这个项目的工作台已经运行，请使用原来的窗口') from exc
    token = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))
        port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}/#{token}'
        print(f'Mask: {url}', flush=True)
        print(f'项目数据（仅本机）: {project.directory}', flush=True)
        (project.directory / 'session.url').write_text(url, encoding='utf-8')
        if not args.no_browser:
            # Open after uvicorn starts listening on the bound socket.
            import threading
            threading.Timer(1, lambda: webbrowser.open(url)).start()
        server = uvicorn.Server(uvicorn.Config(create_app(project, token), log_level='warning', access_log=False))
        server.run(sockets=[sock])
    guard.close()


if __name__ == '__main__':
    main()
