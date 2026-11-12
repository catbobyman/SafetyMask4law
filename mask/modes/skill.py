"""Skill mode: the commands a host skill runs (see integrations/skill/mask/SKILL.md).

Whatever these commands print is read by the host model, so they print counts and masked file
paths only: no names, no originals, no workbench link.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import requests

from .. import store
from ..official import ROOT
from ..project import Project, data_dir


def _reopen(control: Path):
    try:
        info = store.read(control)
        session = requests.Session()
        session.trust_env = False
        return session.post(f'http://127.0.0.1:{info["port"]}/control/open',
                            headers={'X-Mask-Control': info['key']}, timeout=5).ok
    except (OSError, ValueError, requests.RequestException):
        # A control file left by a workbench that is no longer running.
        return False


def start(workspace: Path):
    folder = data_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    control = folder / 'control.mask'
    if not (control.exists() and _reopen(control)):
        control.unlink(missing_ok=True)
        command = [sys.executable, '-m', 'mask', 'workbench', '--workspace', str(workspace), '--host']
        log = folder / 'workbench.log'
        if os.name == 'nt':
            # A host ends the processes its tools started when it exits (dsh headless does), and the
            # workbench has to outlive the turn that opened it. Created through WMI it is not a child
            # of this command, so it is not in the host's process tree.
            line = f'cmd /c "{subprocess.list2cmdline(command)} >> "{log}" 2>&1"'
            script = ('$s = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0};'
                      'exit (Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments '
                      '@{CommandLine=$env:MASK_LINE; CurrentDirectory=$env:MASK_ROOT; ProcessStartupInformation=$s}).ReturnValue')
            created = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], stdin=subprocess.DEVNULL,
                                     capture_output=True, env={**os.environ, 'MASK_LINE': line, 'MASK_ROOT': str(ROOT)})
            alive = lambda: created.returncode == 0
        else:
            with log.open('ab') as out:
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
            alive = lambda: process.poll() is None
        deadline = time.monotonic() + 30
        while not control.exists():
            if not alive() or time.monotonic() > deadline:
                raise SystemExit(f'工作台未能启动，详见 {folder / "workbench.log"}')
            time.sleep(.2)
    report(workspace, '工作台已在用户的浏览器打开。等用户核对并点“交给 AI”后，再运行 status。')


def status(workspace: Path, wait: int = 0):
    deadline = time.monotonic() + wait
    while wait and not _state(workspace)['handed_off'] and time.monotonic() < deadline:
        time.sleep(2)
    state = _state(workspace)
    if state['handed_off']:
        note = '材料已交接。只读取 files 中的脱敏稿，按其中的代号说明回答。'
    elif not state['materials']:
        note = '用户尚未选择材料。'
    else:
        note = f'尚未交接：{state["unrecognized"]} 份待识别，{state["pending"]} 处待确认。请用户在工作台完成核对。'
    report(workspace, note, state)


def _state(workspace: Path):
    if not (data_dir(workspace) / 'project.mask').exists():
        return {'materials': 0, 'unrecognized': 0, 'pending': 0, 'handed_off': False, 'files': []}
    return Project(workspace).status()


def report(workspace, note, state=None):
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps({**(state or _state(workspace)), 'note': note}, ensure_ascii=False, indent=2))
