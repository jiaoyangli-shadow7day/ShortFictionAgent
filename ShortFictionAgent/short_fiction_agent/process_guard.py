"""进程护栏：单实例锁 + 按项目精确匹配/清理管线进程。

防止两个管线（或两个守护进程）同时写同一本书——真机测试出现过 2 个管线
残留、互相干扰、空转 7 小时。

设计要点：
- 只用标准库（pgrep + /proc + os.kill），不依赖 psutil。
- 匹配必须**同时**命中"写作管线命令"和"本项目路径"，绝不粗暴 pkill python，
  不会误杀其它项目或无关进程。
- 单实例锁用 PID 文件：进程死了锁自动失效（可回收），活着则拒绝第二实例。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

# 写作管线子命令（只有这些才算"管线进程"）
PIPELINE_SUBCOMMANDS = ("write-range", "write-all", "write-volume")


def _pgrep(pattern: str) -> list[int]:
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return []
    pids = []
    for tok in r.stdout.split():
        try:
            pids.append(int(tok))
        except ValueError:
            pass
    return pids


def _cmdline(pid: int) -> str:
    # Linux: /proc/<pid>/cmdline
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        if raw:
            return raw.replace(b"\x00", b" ").decode("utf-8", "ignore")
    except (OSError, ValueError):
        pass
    # 退化（如 macOS）：ps
    try:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                           capture_output=True, text=True)
        return r.stdout.strip()
    except (FileNotFoundError, OSError):
        return ""


def _proc_start_time(pid: int) -> float:
    try:
        return Path(f"/proc/{pid}").stat().st_mtime
    except OSError:
        return 0.0


def is_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (ValueError, OSError):
        return False


def find_pipeline_pids(project_dir, exclude_self: bool = True) -> list[int]:
    """返回正在为**本项目**运行的写作管线进程 PID（不含自身）。"""
    project_dir = str(Path(project_dir).resolve())
    me = os.getpid()
    found = []
    # 先用 pgrep 粗筛 short_fiction_agent, 再用 cmdline 精确确认子命令 + 项目路径
    for pid in set(_pgrep("short_fiction_agent")):
        if exclude_self and pid == me:
            continue
        cl = _cmdline(pid)
        if not cl:
            continue
        if any(sub in cl for sub in PIPELINE_SUBCOMMANDS) and project_dir in cl:
            found.append(pid)
    return sorted(set(found))


def kill_pids(pids, grace: float = 3.0) -> list[int]:
    """先 SIGTERM, 宽限后仍存活再 SIGKILL。返回实际发过信号的 PID。"""
    signalled = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            signalled.append(pid)
        except (ProcessLookupError, ValueError, OSError):
            pass
    deadline = time.time() + grace
    while time.time() < deadline and any(is_alive(p) for p in signalled):
        time.sleep(0.2)
    for pid in signalled:
        if is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, ValueError, OSError):
                pass
    return signalled


def cleanup_residual_pipelines(project_dir, keep_newest: bool = True) -> dict:
    """保证本项目最多一个管线在跑：发现多个则杀掉多余的（默认保留最新启动的）。"""
    pids = find_pipeline_pids(project_dir)
    if len(pids) <= 1:
        return {"found": pids, "killed": []}
    if keep_newest:
        newest = max(pids, key=_proc_start_time)
        victims = [p for p in pids if p != newest]
    else:
        victims = list(pids)
    killed = kill_pids(victims)
    return {"found": pids, "killed": killed}


def kill_all_pipelines(project_dir) -> list[int]:
    """杀掉本项目所有管线进程（用于守护进程重启前的彻底清场）。"""
    pids = find_pipeline_pids(project_dir)
    kill_pids(pids)
    return pids


def find_orphan_llm_children(project_dir, exclude_self: bool = True) -> list[int]:
    """找出本项目残留的 LLM 子进程(被 SIGKILL 的父进程留下的孤儿)。

    LLM 子进程的命令行里含 tmpdir 前缀 'llmcall_' 和本项目根路径(config_dir 参数)。
    这些孤儿卡在 recv() 上占着 socket, 不清理会累积到 socket 耗尽 → 管线卡死。
    """
    project_dir = str(Path(project_dir).resolve())
    me = os.getpid()
    found = []
    for pid in set(_pgrep("llmcall_")):
        if exclude_self and pid == me:
            continue
        cl = _cmdline(pid)
        if cl and "llmcall_" in cl and project_dir in cl:
            found.append(pid)
    return sorted(set(found))


def kill_orphan_llm_children(project_dir) -> list[int]:
    """杀掉本项目残留的 LLM 子进程孤儿(回收泄漏的 socket)。"""
    pids = find_orphan_llm_children(project_dir)
    if pids:
        kill_pids(pids, grace=1.0)
    return pids


# ── 单实例锁（PID 文件）──────────────────────────────────────

def acquire_singleton_lock(lock_path) -> bool:
    """获取单实例锁。另一活实例持锁 → 返回 False；否则写入本进程 PID 返回 True。"""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            old = int((lock_path.read_text(encoding="utf-8").strip() or "0"))
        except (ValueError, OSError):
            old = 0
        if old and old != os.getpid() and is_alive(old):
            return False  # 另一实例仍在运行
    try:
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def release_singleton_lock(lock_path) -> None:
    lock_path = Path(lock_path)
    try:
        if lock_path.exists() and lock_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock_path.unlink()
    except OSError:
        pass

