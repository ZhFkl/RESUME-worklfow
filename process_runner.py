"""可取消的本地编译/截图命令；子进程仅继承运行所需的环境变量。"""
import asyncio
import os
import subprocess
from pathlib import Path
from task_metrics import span


async def run_process(args, *, timeout, cwd=None, env=None):
    # Only executable name is recorded; arguments may contain user data or paths.
    with span("process", command=Path(args[0]).name, timeout_seconds=timeout) as metrics:
        result = await _run_process(args, timeout=timeout, cwd=cwd, env=env)
        metrics["returncode"] = result.returncode
        return result


async def _run_process(args, *, timeout, cwd=None, env=None):
    child_env = {key: value for key, value in os.environ.items()
                 if key in {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT"}}
    child_env.update(env or {})
    process = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=child_env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    communication = asyncio.create_task(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await communication
        raise
    return subprocess.CompletedProcess(args, process.returncode,
                                       stdout.decode(errors="replace"), stderr.decode(errors="replace"))
