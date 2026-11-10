"""Entry points, one per mode. All of them work on the project of a workspace folder.

python -m mask [workbench]            local workbench in the browser
python -m mask skill start|status     commands run by a host skill
python -m mask guard hook|proxy       keep known originals out of host requests
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
    proxy = guard.add_parser('proxy', help='本地代理：改写发往模型的请求')
    workspace(proxy)
    proxy.add_argument('--upstream', required=True, help='模型服务地址，如 https://api.anthropic.com')
    proxy.add_argument('--port', type=int, default=8788)

    args = parser.parse_args()
    if args.mode == 'skill':
        from .modes import skill as mode
        mode.start(args.workspace) if args.command == 'start' else mode.status(args.workspace, args.wait)
    elif args.mode == 'guard':
        from .modes import guard as mode
        mode.run_hook() if args.command == 'hook' else mode.run_proxy(args.workspace, args.upstream, args.port)
    else:
        from .modes.workbench import run
        run(args.workspace, args.port, not args.no_browser, args.host)


if __name__ == '__main__':
    main()
