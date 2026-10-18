"""Shared LLM harness with timeout, fallback, and circuit breaker.

Key capabilities:
- Per-policy timeout with automatic fallback model chain
- Circuit breaker: auto-degrade v4-pro after consecutive failures
- Quality-tier routing: critical chapters get pro, routine ones use flash
- Elapsed time logging for every LLM call
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import time
from pathlib import Path
from typing import Callable, Iterable

from .config import save_markdown
from .llm import LLMClient, OpenAICompatibleClient, create_llm_client
from .models import ModelConfig
from .paths import ProjectPaths


# ── Constants ───────────────────────────────────────────────

BAD_RESPONSE_MARKERS = (
    "[API 调用失败]",
    "[错误]",
    "Response ended prematurely",
    "finish_reason=length",
    "context length exceeded",
    "maximum context length",
    "The model produced invalid content",
)

# Fallback timeout defaults per quality tier (seconds).
# These are generous enough that v4-pro won't timeout constantly
# but will fail fast enough to avoid wasting minutes on hung calls.
TIMEOUTS = {
    # ═══ WriterAgent: 永不用 flash，超时是"API 卡死"安全网 ═══
    # 15 分钟对于 v4-pro 写 3000-5000 字足够宽裕
    # 只有 API 真正卡死才会触发，正常写作不会超时
    "write_chapter": {"floor": 900, "rate": 50},
    "writing": {"floor": 900, "rate": 50},

    # ═══ ReviewAgent Full (pro): 读全章+生成报告需要时间 ═══
    "review": {"floor": 480, "rate": 40},

    # ═══ ReviewAgent Lite (flash): 快速初筛 ═══
    "review_lite": {"floor": 120, "rate": 60},

    # ═══ 轻量 Agent: prompt 不大，flash 足够 ═══
    "data_extract": {"floor": 120, "rate": 60},
    "context": {"floor": 120, "rate": 60},
    "revise": {"floor": 180, "rate": 50},
    "book_outline": {"floor": 480, "rate": 40},
    "chapter_outline": {"floor": 480, "rate": 40},
    "outline_update": {"floor": 300, "rate": 40},
    "setting": {"floor": 480, "rate": 40},
    "reality_check": {"floor": 240, "rate": 40},
    "consistency_check": {"floor": 180, "rate": 40},
    "general": {"floor": 240, "rate": 50},
}

# Circuit-breaker config
CB_WINDOW_SECONDS = 600       # 10-minute sliding window
CB_FAILURE_THRESHOLD = 3       # consecutive failures to trigger meltdown
CB_MELTDOWN_SECONDS = 1800     # 30-minute recovery period


# ── 成本估算(粗略, 仅供长篇成本可视化, 非精确账单) ──────────────
# USD / 1K tokens。DeepSeek 价格会变, 按需在 config 里覆盖即可; 这里只为
# "看清钱花在哪个 agent/模型上", 不追求账单级精确。
MODEL_PRICE_PER_1K = {
    "pro":   {"in": 0.0006, "out": 0.0024},
    "flash": {"in": 0.0001, "out": 0.0005},
}


def estimate_cost_usd(model_id: str, usage: dict) -> float:
    if not usage:
        return 0.0
    pin = (usage.get("prompt_tokens") or 0) / 1000.0
    pout = (usage.get("completion_tokens") or 0) / 1000.0
    tier = "flash" if "flash" in (model_id or "").lower() else "pro"
    price = MODEL_PRICE_PER_1K[tier]
    return round(pin * price["in"] + pout * price["out"], 6)


# ── Wall-clock timeout: 真实 API 用可杀子进程, mock/测试用线程 ──────────

# 子进程脚本: 从磁盘读 model.yaml 重建 client 并调用, 把结果(含 usage)写回文件。
# 关键: 子进程是独立 OS 进程, 父进程可 SIGKILL 强杀, 回收卡死的 socket ——
# 这是 threading.Thread 做不到的(线程卡在 requests C 层 recv 无法终止)。
_LLM_CHILD_SCRIPT = r"""
import sys, json, os, signal
def main():
    prompt_file, result_file, config_dir, model_name, task_type, hard_timeout = sys.argv[1:7]
    # 自毁定时器: 即使父进程被外部杀死、自己变成孤儿, 也在 hard_timeout 后退出,
    # 不残留卡死的 socket(SIGALRM 会打断阻塞的 recv())。
    def _selfdestruct(signum, frame):
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump({"error": "child self-timeout"}, f)
        except Exception:
            pass
        os._exit(1)
    try:
        signal.signal(signal.SIGALRM, _selfdestruct)
        signal.alarm(max(5, int(float(hard_timeout))))
    except Exception:
        pass
    try:
        from pathlib import Path
        from short_fiction_agent.config import load_multi_model_config
        from short_fiction_agent.llm import create_llm_client
        mc = load_multi_model_config(Path(config_dir))
        if mc.fallback and model_name and model_name == (mc.fallback.model or ""):
            cfg = mc.fallback
        else:
            cfg = mc.primary
        llm = create_llm_client(cfg)
        # 第3层防线: socket 级 read 超时(死连接由 requests 自己抛, 不依赖信号/外部 kill)。
        try:
            llm.read_timeout = max(60, int(float(hard_timeout)) - 20)
        except Exception:
            pass
        with open(prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
        content = llm.generate(prompt, task_type=task_type)
        out = {"content": content,
               "usage": getattr(llm, "last_usage", {}) or {},
               "actual_model": getattr(llm, "last_actual_model", "") or ""}
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception as e:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump({"error": str(e)}, f, ensure_ascii=False)
if __name__ == "__main__":
    main()
"""


# ── 活跃子进程登记表: 父进程退出/被杀时必须带走它们, 杜绝孤儿 ──────
import threading as _threading
_ACTIVE_CHILD_PGIDS: set[int] = set()
_ACTIVE_LOCK = _threading.Lock()


def _register_child(pgid: int) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CHILD_PGIDS.add(pgid)


def _unregister_child(pgid: int) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CHILD_PGIDS.discard(pgid)


def kill_active_children() -> None:
    """杀掉当前所有活跃的 LLM 子进程组(供管线信号处理/atexit 调用, 不留孤儿)。"""
    import os as _os, signal as _sig
    with _ACTIVE_LOCK:
        pgids = list(_ACTIVE_CHILD_PGIDS)
    for pgid in pgids:
        for s in (_sig.SIGKILL,):
            try:
                _os.killpg(pgid, s)
            except (ProcessLookupError, OSError):
                try:
                    _os.kill(pgid, s)
                except (ProcessLookupError, OSError):
                    pass


def _generate_in_thread(llm: LLMClient, prompt: str, task_type: str, timeout_sec: int) -> str:
    """In-process(mock/测试用): daemon 线程 + join。mock 不会卡死, 无需杀进程。"""
    import threading
    box: dict = {"result": None, "error": None}

    def _run():
        try:
            box["result"] = llm.generate(prompt, task_type)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True, name=f"llm-call-{task_type}")
    worker.start()
    worker.join(timeout_sec)
    if worker.is_alive():
        raise TimeoutError(f"LLM call exceeded {timeout_sec}s (thread abandoned)")
    if box["error"] is not None:
        raise box["error"]
    if box["result"] is None:
        raise RuntimeError("LLM call returned no content")
    return box["result"]


def _generate_in_subprocess(llm: LLMClient, prompt: str, task_type: str,
                            timeout_sec: int, config_dir: str) -> str:
    """真实 API 用: 在可杀子进程里调用, 超时 SIGTERM→SIGKILL, 强制回收卡死连接。

    这是修复"DeepSeek V4 Pro 慢速流式 → 线程 join 超时但线程残留 → 管线死锁"的
    根因方案。真机两次复现(卡 8-9 小时)证明 threading 无法杀死卡死的 recv()。
    """
    import subprocess, sys, tempfile, os, json as _json, shutil, signal as _signal, time as _time
    model_name = getattr(llm, "model", "") or ""
    tmpdir = tempfile.mkdtemp(prefix="llmcall_")
    prompt_file = os.path.join(tmpdir, "p.txt")
    result_file = os.path.join(tmpdir, "r.json")
    # 让子进程能 import short_fiction_agent: 继承父进程的 sys.path
    env = dict(os.environ)
    # A local API endpoint must not be diverted through the user's SOCKS/HTTP proxy.
    base_url = str(getattr(llm, "base_url", "") or "")
    if base_url.startswith(("http://127.0.0.1:", "http://localhost:",
                            "https://127.0.0.1:", "https://localhost:")):
        bypass = "127.0.0.1,localhost,::1"
        env["NO_PROXY"] = ",".join(filter(None, (env.get("NO_PROXY"), bypass)))
        env["no_proxy"] = ",".join(filter(None, (env.get("no_proxy"), bypass)))
    env["PYTHONPATH"] = os.pathsep.join([p for p in sys.path if p]) + \
        (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    def _abandon(p):
        """杀整个进程组后**绝不阻塞等待**子进程退出。

        关键: 不调用 proc.wait()(macOS 上有"SIGKILL 期间子进程在 D 态/正在写文件 →
        proc.wait 永久挂起"的已知坑, 正是第 6 次卡死的根因)。只发 SIGKILL, 然后用
        非阻塞 poll() 短暂收尸; 收不掉也直接放手——子进程有自己的 SIGALRM 自毁 +
        socket read 超时会收尾, 残留也会被下次启动的孤儿清理带走。父进程永不被它拖住。
        """
        try:
            os.killpg(os.getpgid(p.pid), _signal.SIGKILL)
        except (ProcessLookupError, OSError):
            try:
                os.kill(p.pid, _signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        end = _time.monotonic() + 2.0
        while _time.monotonic() < end:
            try:
                if p.poll() is not None:   # poll() 非阻塞(waitpid WNOHANG), 不会挂起
                    return
            except Exception:
                return
            _time.sleep(0.1)

    proc = None
    try:
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt)
        # start_new_session: 子进程独立进程组, 便于整组 kill。
        # 子进程自带 hard_timeout(父超时+15s)自毁, 即使父被外部杀死也不会变 4 小时僵尸。
        proc = subprocess.Popen(
            [sys.executable, "-c", _LLM_CHILD_SCRIPT,
             prompt_file, result_file, str(config_dir), model_name, task_type,
             str(int(timeout_sec) + 15)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=env, start_new_session=True,
        )
        _register_child(proc.pid)
        # 用**非阻塞 poll() 轮询**而非 proc.wait(timeout): 后者在 macOS 上可能无视
        # timeout 永久挂起(第6次卡死根因)。poll() 用 waitpid(WNOHANG), 绝不挂起。
        deadline = _time.monotonic() + timeout_sec
        while True:
            if proc.poll() is not None:
                break
            if _time.monotonic() >= deadline:
                _abandon(proc)
                raise TimeoutError(f"LLM call exceeded {timeout_sec}s (subprocess abandoned)")
            _time.sleep(0.5)

        if not os.path.exists(result_file):
            raise RuntimeError("LLM subprocess produced no result")
        with open(result_file, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if data.get("error"):
            raise RuntimeError(f"LLM subprocess error: {data['error']}")
        try:
            llm.last_usage = data.get("usage", {}) or {}
            llm.last_actual_model = data.get("actual_model", "") or ""
        except Exception:
            pass
        return data["content"]
    finally:
        # 非阻塞收尾: 子进程还活着就 SIGKILL 整组并放手, 绝不阻塞父进程
        if proc is not None:
            try:
                if proc.poll() is None:
                    _abandon(proc)
            except Exception:
                pass
            _unregister_child(proc.pid)
        shutil.rmtree(tmpdir, ignore_errors=True)


def generate_with_timeout(llm: LLMClient, prompt: str, task_type: str,
                          timeout_sec: int, config_dir: str | None = None) -> str:
    """带 wall-clock 超时地调用 ``llm.generate``。

    - 真实 API 客户端(OpenAICompatibleClient) + 有 config_dir → **可杀子进程**,
      超时能强制回收卡死连接(修复推理模型慢速流式导致的管线死锁)。
    - mock / 自定义 / 无 config_dir → in-process 线程(测试/dry-run/eval, 不会卡死)。

    所有 LLM 调用都应经此函数, 不要直接 `llm.generate()`(那样无超时)。
    """
    if config_dir and isinstance(llm, OpenAICompatibleClient):
        return _generate_in_subprocess(llm, prompt, task_type, timeout_sec, config_dir)
    return _generate_in_thread(llm, prompt, task_type, timeout_sec)


# ── Dataclasses ──────────────────────────────────────────────

@dataclass
class AgentCallPolicy:
    """Runtime policy for a single agent call."""

    agent_name: str
    task_type: str = "general"
    target_prompt_tokens: int | None = None
    min_response_chars: int = 20
    required_markers: list[str] = field(default_factory=list)
    required_regex: str | None = None
    compact_retry: bool = True
    # Quality tier & model routing
    quality_tier: str = "routine"     # "critical" | "stage_gate" | "routine" | "lite"
    default_model: str = "primary"    # "primary"=pro, "fallback"=flash
    timeout_seconds: int | None = None
    allow_fallback: bool = True
    fallback_task_type: str | None = None


# ── AgentRuntime ─────────────────────────────────────────────

class AgentRuntime:
    """Reusable harness around LLM calls for all sub-agents.

    Supports:
    - Per-policy timeout with automatic fallback model chain
    - Circuit breaker: auto-degrade after consecutive pro failures
    - Quality-tier routing
    - Response validation + compact retry
    - Elapsed-time logging
    """

    DEFAULT_TARGET_PROMPT_TOKENS = 16000

    # 单个日志文件大小上限(字节), 超过则轮转, 防止 jsonl 无限增长。
    LOG_MAX_BYTES = 5 * 1024 * 1024
    LOG_BACKUPS = 3

    def __init__(self, paths: ProjectPaths, llm: LLMClient,
                 fallback_llm: LLMClient | None = None,
                 router: "ModelRouter | None" = None):
        self.paths = paths
        self.llm = llm                        # primary (usually pro)
        self.fallback_llm = fallback_llm       # fallback (usually flash)
        # 集中式模型路由(未注入时用模块级默认表)
        from .model_router import DEFAULT_ROUTER
        self.router = router or DEFAULT_ROUTER
        # Circuit breaker state
        self._cb_failures: list[float] = []    # timestamps of pro failures
        self._cb_meltdown_until: float = 0.0

    # ── Token estimates ──────────────────────────────────────

    def estimate_tokens(self, text: str) -> int:
        # 统一走 context_budget(与 agent/client 一致, 消除 2x 估算漂移)
        from .context_budget import estimate_tokens as _est
        return _est(text)

    def prompt_limit(self) -> int:
        return int(getattr(self.llm, "max_prompt_tokens", 50000) or 50000)

    def policy_limit(self, policy: AgentCallPolicy) -> int:
        hard_limit = self.prompt_limit()
        target = policy.target_prompt_tokens or self.DEFAULT_TARGET_PROMPT_TOKENS
        return max(1200, min(target, int(hard_limit * 0.72)))

    # ── Text utilities ───────────────────────────────────────

    def preview_text(self, text: str, head_chars: int = 2200, tail_chars: int = 800) -> str:
        text = text or ""
        if len(text) <= head_chars + tail_chars + 80:
            return text
        return (
            text[:head_chars].rstrip()
            + "\n\n...（中间内容已由 AgentRuntime 省略以控制上下文）...\n\n"
            + text[-tail_chars:].lstrip()
        )

    def truncate_text(self, text: str, max_chars: int) -> str:
        """仅用于非质量关键内容（学校设定、梗概等）。审查反馈等用 compress_text。"""
        text = text or ""
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        para = cut.rfind("\n\n")
        if para > int(max_chars * 0.55):
            cut = cut[:para]
        return cut.rstrip() + "\n\n...（后续内容已截断）"

    def compress_text(self, text: str, target_chars: int, label: str = "内容") -> str:
        """把长文本压到 target_chars 以内（段落感知的确定性截断）。

        ⚠️ 精简: 此前对每份报告都用 flash LLM 智能压缩, 每章产生 5-7 次额外 flash
        调用 —— 既慢、又是大量卡死点, 而报告本身是结构化的(评分/问题/建议在前),
        截断损失很小。为速度与稳定性, 改为纯确定性截断, 不再起 LLM。
        """
        return self.truncate_text(text or "", target_chars)

    def compact_yaml_items(self, items: Iterable[dict], max_items: int = 10) -> list[dict]:
        compacted = []
        keep_keys = (
            "chapter_id", "volume", "title", "story_question",
            "main_event", "conflict", "relationship_turn",
            "irreversible_change", "ending_hook", "status",
        )
        for item in list(items or [])[:max_items]:
            if not isinstance(item, dict):
                continue
            compacted.append({k: item.get(k) for k in keep_keys
                              if item.get(k) not in (None, "", [])})
        return compacted

    # ── Prompt budget ────────────────────────────────────────

    def ensure_budget(
        self,
        prompt: str,
        policy: AgentCallPolicy,
        compact_prompt: Callable[[], str] | str | None = None,
    ) -> tuple[str, int, bool]:
        limit = self.policy_limit(policy)
        estimated = self.estimate_tokens(prompt)
        if estimated <= limit:
            return prompt, estimated, False

        if compact_prompt:
            prompt = compact_prompt() if callable(compact_prompt) else compact_prompt
            estimated = self.estimate_tokens(prompt)
            if estimated <= limit:
                return prompt, estimated, True

        fitted = self._hard_fit_prompt(prompt, limit)
        return fitted, self.estimate_tokens(fitted), True

    def _hard_fit_prompt(self, prompt: str, token_limit: int) -> str:
        char_budget = max(1200, int(token_limit * 1.6))
        if len(prompt) <= char_budget:
            return prompt
        fitted = prompt
        while char_budget >= 1200:
            head_chars = int(char_budget * 0.62)
            tail_chars = char_budget - head_chars
            fitted = (
                prompt[:head_chars].rstrip()
                + "\n\n...（Prompt 已被 AgentRuntime 兜底截断；请优先使用紧凑上下文）...\n\n"
                + prompt[-tail_chars:].lstrip()
            )
            if self.estimate_tokens(fitted) <= token_limit:
                return fitted
            char_budget = int(char_budget * 0.82)
        return fitted

    # ── Response validation ──────────────────────────────────

    def validate_response(self, content: str, policy: AgentCallPolicy) -> tuple[bool, str]:
        if content is None:
            return False, "empty response"
        stripped = content.strip()
        if not stripped:
            return False, "empty response"
        for marker in BAD_RESPONSE_MARKERS:
            if marker in stripped:
                return False, f"bad response marker: {marker}"
        if len(stripped) < policy.min_response_chars:
            return False, f"response too short: {len(stripped)} chars"
        for marker in policy.required_markers:
            if marker not in stripped:
                return False, f"missing required marker: {marker}"
        if policy.required_regex and not re.search(policy.required_regex, stripped, re.S):
            return False, f"missing required pattern: {policy.required_regex}"
        return True, ""

    # ── Timeout resolution ───────────────────────────────────

    def _resolve_timeout(self, policy: AgentCallPolicy, prompt_tokens: int = 8000) -> int:
        """Resolve timeout dynamically based on prompt size.

        Formula: max(floor, prompt_tokens / rate)
        This ensures v4-pro doesn't "always timeout" just because
        writing a long chapter takes time.
        """
        if policy.timeout_seconds is not None:
            return policy.timeout_seconds
        cfg = TIMEOUTS.get(policy.task_type, TIMEOUTS["general"])
        if isinstance(cfg, int):
            return cfg
        floor = cfg.get("floor", 180)
        rate = cfg.get("rate", 60)
        return max(floor, int(prompt_tokens / rate))

    # ── Circuit breaker ──────────────────────────────────────

    def _is_pro_meltdown(self) -> bool:
        now = time.time()
        if now < self._cb_meltdown_until:
            return True
        # Expire old failures
        cutoff = now - CB_WINDOW_SECONDS
        self._cb_failures = [t for t in self._cb_failures if t > cutoff]
        if len(self._cb_failures) >= CB_FAILURE_THRESHOLD:
            self._cb_meltdown_until = now + CB_MELTDOWN_SECONDS
            return True
        return False

    def _record_pro_failure(self):
        self._cb_failures.append(time.time())

    def _record_pro_success(self):
        # Clear failure slate on any success
        self._cb_failures.clear()
        self._cb_meltdown_until = 0.0

    def _should_use_fallback(self, policy: AgentCallPolicy) -> bool:
        """Decide whether to skip primary and go straight to fallback.

        Hard rules:
        - Critical tier review/outline: stays on pro, no fallback.
        - Routine/lite agents: can use flash if primary is failing.
        - WriterAgent: can fall back to flash after pro retry fails.
        """
        # Critical tasks stay on pro
        if policy.quality_tier in ("critical", "stage_gate") and policy.task_type not in ("write_chapter", "writing"):
            return False
        if not policy.allow_fallback or not self.fallback_llm:
            return False
        # Critical and stage_gate tiers stay on pro
        if policy.quality_tier in ("critical", "stage_gate"):
            return False
        if self._is_pro_meltdown():
            return True
        return False

    # ── Core call ────────────────────────────────────────────

    def call(
        self,
        prompt: str,
        policy: AgentCallPolicy,
        compact_prompt: Callable[[], str] | str | None = None,
        output_path: Path | None = None,
    ) -> str:
        """Generate with budget fitting, timeout, fallback, and validation.

        Flow:
        1. Check circuit breaker → may skip to fallback
        2. Fit prompt to budget
        3. Call primary LLM with timeout → validate
        4. On timeout/failure → fallback LLM → validate
        5. Write output only after validation succeeds
        """
        fitted, prompt_tokens, compacted = self.ensure_budget(prompt, policy, compact_prompt)
        timeout_sec = self._resolve_timeout(policy, prompt_tokens)

        # Determine which LLM to use
        # 1. Circuit breaker overrides everything (except WriterAgent)
        use_fallback = self._should_use_fallback(policy)
        # 2. 集中式路由表决定该任务走 primary 还是 fallback(flash)。
        #    显式 policy.default_model="fallback" 仍可强制 flash。
        routed = self.router.resolve(policy)
        prefer_flash = (
            (routed == "fallback" or policy.default_model == "fallback")
            and self.fallback_llm is not None
        )
        if prefer_flash and not use_fallback:
            primary_llm = self.fallback_llm
        elif use_fallback:
            primary_llm = self.fallback_llm
        else:
            primary_llm = self.llm
        # WriterAgent 超时后允许 flash 降级，每次新任务默认先用 pro
        has_fallback = (policy.allow_fallback and self.fallback_llm is not None
                        and not use_fallback)
        # WriterAgent（写正文）超时后走"加长 timeout 重试 pro"逻辑，
        # 其余 Agent 超时后直接降级 fallback。
        writer_mode = policy.task_type in ("write_chapter", "writing")
        # 标签按"实际选中的 client 对象"判定, 而不是只看 use_fallback ——
        # 路由表把 routine 任务路由到 flash 时 use_fallback 仍是 False,
        # 但真正执行的是 fallback_llm, 之前会被错记成 primary。
        primary_is_fallback = (primary_llm is self.fallback_llm and self.fallback_llm is not None)
        model_used = "fallback" if primary_is_fallback else "primary"
        t0 = 0.0
        elapsed = 0.0
        response = ""
        valid = False
        reason = ""

        # ── Try primary ──────────────────────────────────────
        try:
            t0 = time.time()
            response = self._generate_with_timeout(primary_llm, fitted, policy.task_type, timeout_sec)
            elapsed = time.time() - t0
            model_used = "fallback" if primary_is_fallback else "primary"

            # If circuit breaker was in meltdown but primary succeeded for critical, clear it
            if not use_fallback and policy.quality_tier == "critical":
                self._record_pro_success()

        except TimeoutError:
            elapsed = timeout_sec
            # WriterAgent: retry once with 1.5x timeout before giving up
            if writer_mode:
                retry_timeout = int(timeout_sec * 1.5)
                print(f"    ⏰ WriterAgent 超时 ({timeout_sec}s)，加长到 {retry_timeout}s 重试 pro，仍失败则降级 flash...")
                try:
                    t1 = time.time()
                    response = self._generate_with_timeout(self.llm, fitted, policy.task_type, retry_timeout)
                    elapsed += time.time() - t1
                    model_used = "primary_retry"
                except TimeoutError:
                    self._record_pro_failure()
                    raise RuntimeError(
                        f"WriterAgent 超时两次 ({timeout_sec}s + {retry_timeout}s)，"
                        f"Prompt ~{prompt_tokens} tokens。请检查 API 或减小章节长度。"
                    )
                except Exception as retry_exc:
                    self._record_pro_failure()
                    raise RuntimeError(f"WriterAgent 重试失败: {retry_exc}")
            elif has_fallback:
                elapsed = timeout_sec
                print(f"    ⏰ {policy.agent_name} 主模型超时 ({timeout_sec}s)，降级到 fallback...")
                try:
                    t1 = time.time()
                    task = policy.fallback_task_type or policy.task_type
                    response = self._generate_with_timeout(self.fallback_llm, fitted, task, timeout_sec * 2)
                    elapsed += time.time() - t1
                    model_used = "fallback_after_timeout"
                except TimeoutError:
                    raise RuntimeError(f"{policy.agent_name} 主模型超时且 fallback 也超时")
                except Exception as fb_exc:
                    raise RuntimeError(f"{policy.agent_name} 主模型超时且 fallback 失败: {fb_exc}")
            else:
                raise RuntimeError(
                    f"{policy.agent_name} LLM 调用超时 ({timeout_sec}s)，无可用 fallback"
                )

        except Exception as exc:
            elapsed = time.time() - t0 if t0 > 0 else 0
            if not use_fallback:
                self._record_pro_failure()
            if has_fallback:
                print(f"    ⚠️ {policy.agent_name} 主模型失败 ({exc})，降级到 fallback...")
                try:
                    t1 = time.time()
                    task = policy.fallback_task_type or policy.task_type
                    response = self._generate_with_timeout(self.fallback_llm, fitted, task, timeout_sec * 2)
                    elapsed += time.time() - t1
                    model_used = "fallback_after_error"
                except Exception as fb_exc:
                    raise RuntimeError(
                        f"{policy.agent_name} 主模型失败且 fallback 也失败: {fb_exc}"
                    )
            else:
                raise

        # ── Validate ──────────────────────────────────────────
        valid, reason = self.validate_response(response, policy)

        # If invalid and we haven't tried compact retry + fallback
        if not valid and compact_prompt and policy.compact_retry and not compacted:
            compacted_prompt = compact_prompt() if callable(compact_prompt) else compact_prompt
            fitted, prompt_tokens, compacted = self.ensure_budget(compacted_prompt, policy)
            try:
                t0 = time.time()
                response = self._generate_with_timeout(
                    self.fallback_llm or self.llm, fitted,
                    policy.fallback_task_type or policy.task_type,
                    timeout_sec * 2,
                )
                elapsed += time.time() - t0
                model_used += "_compact_retry"
            except Exception:
                pass
            valid, reason = self.validate_response(response, policy)

        if not valid:
            raise RuntimeError(f"{policy.agent_name}: {reason}")

        # ── Write output ─────────────────────────────────────
        if output_path is not None:
            save_markdown(output_path, response)

        # 记录"实际"用到的模型 id + 真实 token 用量 + 估算成本,
        # 而不是只记 primary/fallback 和字符估算。
        used_client = self.fallback_llm if "fallback" in model_used else self.llm
        model_id = getattr(used_client, "model", "") or ""
        usage = getattr(used_client, "last_usage", {}) or {}
        actual_model = getattr(used_client, "last_actual_model", "") or model_id
        self._log_call(policy, prompt_tokens, len(response), compacted, True,
                       model_used, elapsed, model_id=model_id,
                       usage=usage, actual_model=actual_model)
        return response

    def _generate_with_timeout(self, llm: LLMClient, prompt: str,
                                task_type: str, timeout_sec: int) -> str:
        """带 wall-clock 超时调用(真实 API 走可杀子进程, mock 走线程)。"""
        return generate_with_timeout(llm, prompt, task_type, timeout_sec,
                                     config_dir=str(self.paths.root))

    def quick_generate(self, prompt: str, task_type: str = "general",
                       timeout_sec: int = 180, prefer_fallback: bool = True) -> str:
        """供工具型 LLM 调用(压缩/翻译门禁意见/记忆摘要)使用的带超时入口。

        这些调用此前直接 `llm.generate()`、绕过 harness, 没有 wall-clock 超时,
        flash 卡死会拖死整条管线。统一走带超时(可杀子进程)的路径。
        """
        llm = self.fallback_llm if (prefer_fallback and self.fallback_llm is not None) else self.llm
        return generate_with_timeout(llm, prompt, task_type, timeout_sec,
                                     config_dir=str(self.paths.root))

    # ── Logging ──────────────────────────────────────────────

    def _log_call(
        self,
        policy: AgentCallPolicy,
        prompt_tokens: int,
        response_chars: int,
        compacted: bool,
        success: bool,
        model_used: str = "primary",
        elapsed_seconds: float = 0.0,
        error: str = "",
        model_id: str = "",
        usage: dict | None = None,
        actual_model: str = "",
    ) -> None:
        try:
            self.paths.memory_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.paths.memory_dir / "agent_runtime_log.jsonl"
            self._rotate_log_if_needed(log_path)
            usage = usage or {}
            cost = estimate_cost_usd(actual_model or model_id, usage)
            record = {
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "agent": policy.agent_name,
                "task_type": policy.task_type,
                "quality_tier": policy.quality_tier,
                "model": model_used,
                "model_id": model_id,
                "actual_model": actual_model or model_id,
                "prompt_tokens_est": prompt_tokens,
                # 真实用量(API usage); 无则为 None
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "est_cost_usd": cost,
                "response_chars": response_chars,
                "elapsed_s": round(elapsed_seconds, 1),
                "timeout_s": policy.timeout_seconds,
                "compacted": compacted,
                "success": success,
                "error": error,
            }
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _rotate_log_if_needed(self, log_path: Path) -> None:
        """大小轮转: log → log.1 → log.2 ... 防止 jsonl 无限增长(问题20)。"""
        try:
            if not log_path.exists() or log_path.stat().st_size < self.LOG_MAX_BYTES:
                return
            # 丢弃最老的一份, 其余依次后移
            oldest = log_path.with_suffix(log_path.suffix + f".{self.LOG_BACKUPS}")
            if oldest.exists():
                oldest.unlink()
            for i in range(self.LOG_BACKUPS - 1, 0, -1):
                src = log_path.with_suffix(log_path.suffix + f".{i}")
                if src.exists():
                    src.rename(log_path.with_suffix(log_path.suffix + f".{i + 1}"))
            log_path.rename(log_path.with_suffix(log_path.suffix + ".1"))
        except Exception:
            return

    @property
    def cb_status(self) -> dict:
        """Expose circuit breaker status for diagnostics."""
        now = time.time()
        cutoff = now - CB_WINDOW_SECONDS
        recent = [t for t in self._cb_failures if t > cutoff]
        return {
            "recent_failures": len(recent),
            "threshold": CB_FAILURE_THRESHOLD,
            "in_meltdown": now < self._cb_meltdown_until,
            "meltdown_remaining_s": max(0, self._cb_meltdown_until - now),
        }

