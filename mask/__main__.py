"""Entry points, one per mode. All of them work on the project of a workspace folder.

python -m mask [workbench]            local workbench in the browser
python -m mask skill start|status     commands run by a host skill
python -m mask guard hook             refuse a host prompt or tool call that would expose originals
python -m mask gateway [start]        rewrite the model requests of connected agents; start: in the background
python -m mask install|uninstall dsh|codex  put the skill, the guard (dsh) and the gateway address into a host
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(prog='python -m mask', description='Mask 本地脱敏')
    modes = parser.add_subparsers(dest='mode')

    def workspace(p):
        p.add_argument('--workspace', type=Path, default=Path.cwd(), help='材料所在目录，默认当前目录')

    # Without a mode name the workbench starts, so existing shortcuts keep working.
    for p in (parser, modes.add_parser('workbench', help='本地工作台')):
        workspace(p)
        p.add_argument('--port', type=int, default=0)
        p.add_argument('--no-browser', action='store_true')
        p.add_argument('--host', action='store_true', help='由宿主 Skill 发起：导出 Markdown 时交接到工作目录')

    skill = modes.add_parser('skill', help='宿主 Skill 调用的命令').add_subparsers(dest='command', required=True)
    workspace(skill.add_parser('start', help='打开工作台'))
    status = skill.add_parser('status', help='查看是否已交接')
    workspace(status)
    status.add_argument('--wait', type=int, default=0, help='最多等待交接的秒数')

    guard = modes.add_parser('guard', help='拦截模式').add_subparsers(dest='command', required=True)
    guard.add_parser('hook', help='宿主 hook：从标准输入读事件，输出放行或拦截')

    modes.add_parser('gateway', help='本机网关：改写已接入 Agent 发往模型的请求').add_argument(
        'command', nargs='?', choices=['start'], help='start：在后台启动，已在运行则不动')

    for command in ('install', 'uninstall'):
        modes.add_parser(command, help='把技能、拦截和网关地址装入宿主 / 从宿主移除').add_argument(
            'host', choices=['dsh', 'codex'], help='dsh：DeepSeek Harness；codex：Codex（技能和网关地址）')

    args = parser.parse_args()
    if args.mode in ('install', 'uninstall'):
        from .modes import install as mode
        getattr(mode, args.mode)(args.host)
    elif args.mode == 'skill':
        from .modes import skill as mode
        mode.start(args.workspace) if args.command == 'start' else mode.status(args.workspace, args.wait)
    elif args.mode == 'guard':
        from .modes import guard as mode
        mode.run_hook()
    elif args.mode == 'gateway':
        from .modes import gateway as mode
        mode.start() if args.command else mode.run()
    else:
        from .modes.workbench import run
        run(args.workspace, args.port, not args.no_browser, args.host)


if __name__ == '__main__':
    main()
