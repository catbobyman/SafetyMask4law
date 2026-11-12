import os
import subprocess
import sys

from ..official import ROOT


def launch(args, log):
    """Start `python -m mask <args>` in the background, its output appended to `log`; it outlives the caller.

    A host may run its tools in a Windows job that is closed, with its processes, when the host exits
    (dsh headless does). Breaking away keeps the process out of it; a job that forbids breaking away
    refuses that creation, and the process is then started inside the job.
    """
    command = [sys.executable, '-m', 'mask', *args]
    with log.open('ab') as output:
        start = lambda flags: subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                               stderr=subprocess.STDOUT, creationflags=flags)
        if os.name != 'nt':
            return start(0)
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            return start(flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except PermissionError:
            return start(flags)
