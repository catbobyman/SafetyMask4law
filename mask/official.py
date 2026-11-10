"""Adapter for the installed, unmodified has-anonymizer 1.0.3 skill (MIT-0)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'vendor/skills/has-anonymizer'
if not (SKILL / '_meta.json').exists():
    raise RuntimeError('缺少官方 has-anonymizer 1.0.3，请按 README 安装到 vendor/skills/')
if json.loads((SKILL / '_meta.json').read_text(encoding='utf-8'))['version'] != '1.0.3':
    raise RuntimeError('当前适配器要求 has-anonymizer 1.0.3')
sys.path.insert(0, str(SKILL / 'scripts'))
from has_text.client import HaSClient  # noqa: E402
from has_text.commands.scan import run_scan  # noqa: E402
from has_text.commands.seek import _tool_seek  # noqa: E402
from has_text.mapping import TAG_PATTERN as TAG, normalize_mapping_dict, save_mapping  # noqa: E402,F401


class LocalClient(HaSClient):
    def __init__(self, url, key):
        super().__init__(url)
        self._session.trust_env = False
        self._session.headers['Authorization'] = 'Bearer ' + key

    def chat(self, messages):
        # Explicit sampling and completion checks, without changing official prompts or parser.
        response = self._session.post(self.base_url + '/v1/chat/completions',
                                      json={'messages': messages, 'temperature': 0, 'max_tokens': 4096}, timeout=120)
        response.raise_for_status()
        choice = response.json()['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise RuntimeError(f'HaS 响应未完整结束：{choice.get("finish_reason")}')
        return choice['message']['content']


class Recognizer:
    def __init__(self, data_dir: Path):
        self.data_dir, self.process, self.client, self.log = data_dir, None, None, None

    def start(self):
        if self.process and self.process.poll() is None:
            return
        exe = ROOT / 'vendor/llama.cpp/b12032/llama-server.exe'
        model = ROOT / 'models/has_text_model.gguf'
        if not exe.exists() or not model.exists():
            raise RuntimeError('缺少本地 llama.cpp b12032 或 HaS 模型，请按 README 准备')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        key = secrets.token_urlsafe(32)
        self.log = (self.data_dir / 'llama.log').open('wb')
        self.process = subprocess.Popen([str(exe), '-m', str(model), '--host', '127.0.0.1', '--port', str(port),
            '--api-key', key, '-ngl', '999', '-c', '8192', '-np', '1', '-fa', 'on', '-ctk', 'q8_0', '-ctv', 'q8_0'],
            stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.client = LocalClient(f'http://127.0.0.1:{port}', key)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f'HaS 启动失败，详见 {self.data_dir / "llama.log"}')
            if self.client.health():
                return
            time.sleep(.2)
        self.close()
        raise RuntimeError('HaS 启动超时，未开始识别')

    def scan(self, text, categories):
        self.start()
        return run_scan(self.client, text, categories, max_chunk_tokens=3000, max_parallel_requests=1)['entities']

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=15)
        if self.log:
            self.log.close()
