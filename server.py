# server.py
# dummyLLM (FastAPI) — env-controlled job queue simulator + ELIZA normal mode.
#
# Client/Q payload is STANDARD ONLY:
#   { op, args, timeout_ms?, trace_id? }
# No simulate knobs allowed in the request.
#
# Server-side behavior is selected by ENV:
#   DUMMYLLM_MODE = ok|echo|slow|fail|hang|timeout|flaky|random
#   DUMMYLLM_RANDOM_WEIGHTS = "ok=70,echo=10,slow=10,fail=5,hang=3,timeout=2,flaky=0"
#   DUMMYLLM_LATENCY_MS = 250
#   DUMMYLLM_SEED = 1337
#
# Endpoints:
#   GET  /health
#   POST /v1/jobs
#   GET  /v1/jobs/{id}
#   GET  /v1/jobs/{id}/request        (debug introspection; shows received args + chosen mode)
#   POST /v1/jobs/{id}/cancel

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

State = Literal["queued", "running", "ok", "fail", "timeout", "cancelled"]
Mode = Literal["ok", "echo", "slow", "fail", "hang", "flaky", "timeout", "random"]

# ---------------- ENV CONFIG ----------------

def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v.strip())
    except ValueError:
        return default

def parse_weights(s: str) -> Dict[str, int]:
    """
    Parse "ok=70,echo=10,slow=10,fail=5,hang=3,timeout=2,flaky=0"
    Unknown keys are ignored. Missing keys default to 0.
    """
    allowed = {"ok", "echo", "slow", "fail", "hang", "flaky", "timeout"}
    out = {k: 0 for k in allowed}
    parts = [p.strip() for p in (s or "").split(",") if p.strip()]
    for p in parts:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        if k not in allowed:
            continue
        try:
            out[k] = max(0, int(v.strip()))
        except ValueError:
            out[k] = 0
    return out

DUMMYLLM_MODE: Mode = _env_str("DUMMYLLM_MODE", "ok")  # type: ignore
DUMMYLLM_RANDOM_WEIGHTS_RAW = _env_str(
    "DUMMYLLM_RANDOM_WEIGHTS",
    "ok=70,echo=10,slow=10,fail=5,hang=3,timeout=2,flaky=0",
)
DUMMYLLM_RANDOM_WEIGHTS = parse_weights(DUMMYLLM_RANDOM_WEIGHTS_RAW)
DUMMYLLM_LATENCY_MS = max(0, _env_int("DUMMYLLM_LATENCY_MS", 250))
DUMMYLLM_SEED = _env_int("DUMMYLLM_SEED", 1337)

# global RNG for mode selection (deterministic if seed fixed)
_mode_rng = random.Random(DUMMYLLM_SEED)
_mode_rng_lock = asyncio.Lock()

def now_sec() -> int:
    return int(time.time())

def weighted_choice(weights: Dict[str, int]) -> Mode:
    # if all zero -> default ok
    total = sum(weights.values())
    if total <= 0:
        return "ok"
    r = _mode_rng.randrange(1, total + 1)
    acc = 0
    for k, w in weights.items():
        if w <= 0:
            continue
        acc += w
        if r <= acc:
            return k  # type: ignore
    return "ok"

async def choose_mode_for_job() -> Mode:
    """
    Choose mode based on global DUMMYLLM_MODE.
    If random, choose weighted per job using deterministic RNG.
    """
    global DUMMYLLM_MODE
    m = DUMMYLLM_MODE
    if m != "random":
        return m
    async with _mode_rng_lock:
        return weighted_choice(DUMMYLLM_RANDOM_WEIGHTS)

# ---------------- API MODELS ----------------

app = FastAPI(title="dummyLLM", version="0.3.0")

class JobCreate(BaseModel):
    op: str
    args: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=8000, ge=0)
    trace_id: Optional[str] = None

class JobCreateResp(BaseModel):
    id: str
    state: State
    created_at: int

class JobError(BaseModel):
    code: str
    message: str

class JobResult(BaseModel):
    text: str
    usage: Dict[str, int] = Field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )

class JobStatus(BaseModel):
    id: str
    state: State
    created_at: int
    updated_at: int
    result: Optional[JobResult] = None
    error: Optional[JobError] = None

class JobRequestView(BaseModel):
    op: str
    args: Dict[str, Any]
    timeout_ms: int
    trace_id: Optional[str] = None
    chosen_mode: Mode
    base_latency_ms: int
    random_weights: Optional[Dict[str, int]] = None
    seed: int

# ---------------- JOB STORE ----------------

@dataclass
class Job:
    id: str
    state: State
    created_at: int
    updated_at: int
    op: str
    args: Dict[str, Any]
    timeout_ms: int
    trace_id: Optional[str]
    chosen_mode: Mode
    base_latency_ms: int
    seed: int
    result: Optional[JobResult] = None
    error: Optional[JobError] = None
    task: Optional[asyncio.Task] = None

JOBS: Dict[str, Job] = {}

def set_state(job: Job, state: State) -> None:
    job.state = state
    job.updated_at = now_sec()

def finish_ok(job: Job, text: str) -> None:
    set_state(job, "ok")
    job.result = JobResult(text=text)
    job.error = None

def finish_fail(job: Job, code: str, message: str) -> None:
    set_state(job, "fail")
    job.result = None
    job.error = JobError(code=code, message=message)

def finish_timeout(job: Job) -> None:
    set_state(job, "timeout")
    job.result = None
    job.error = JobError(code="TIMEOUT", message="simulated timeout")

def finish_cancelled(job: Job) -> None:
    set_state(job, "cancelled")
    job.result = None
    job.error = JobError(code="CANCELLED", message="cancelled by client")

# ---------------- ELIZA ----------------

_REFLECTIONS = {
    "i": "you",
    "me": "you",
    "my": "your",
    "am": "are",
    "you": "I",
    "your": "my",
    "yours": "mine",
    "mine": "yours",
}

_FALLBACKS = [
    "Please tell me more.",
    "How does that make you feel?",
    "Why do you say that?",
    "Can you elaborate on that?",
    "Let's explore that a bit further.",
]

_RULES: List[Tuple[str, List[str]]] = [
    ("i need", ["Why do you need {x}?", "Would it really help you to get {x}?", "Are you sure you need {x}?"]),
    ("i am", ["How long have you been {x}?", "How do you feel about being {x}?", "Why do you say you're {x}?"]),
    ("i feel", ["Do you often feel {x}?", "When do you usually feel {x}?", "What makes you feel {x}?"]),
    ("because", ["Is that the real reason?", "What other reasons come to mind?", "Does that reason apply to anything else?"]),
    ("why", ["What do you think?", "Why do you ask?", "What answer would satisfy you?"]),
    ("hello", ["Hello. How are you feeling today?", "Hi. What's on your mind?"]),
    ("hi", ["Hello. How are you feeling today?", "Hi. What's on your mind?"]),
    ("hey", ["Hello. How are you feeling today?", "Hi. What's on your mind?"]),
    ("mother", ["Tell me more about your family.", "How is your relationship with your mother?"]),
    ("father", ["Tell me more about your family.", "How is your relationship with your father?"]),
    ("always", ["Can you think of a specific example?", "When exactly does that happen?"]),
]

def _reflect(text: str) -> str:
    words = text.split()
    out = []
    for w in words:
        out.append(_REFLECTIONS.get(w.lower(), w))
    return " ".join(out)

def extract_last_user_message(args: Dict[str, Any]) -> str:
    msgs = args.get("messages", [])
    if not isinstance(msgs, list):
        return ""
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else ""
    return ""

def eliza_reply(user_text: str, rng: random.Random) -> str:
    s = (user_text or "").strip()
    if not s:
        return "Hello. What would you like to talk about?"
    low = s.lower()
    for pat, templates in _RULES:
        if pat in low:
            idx = low.find(pat) + len(pat)
            tail = s[idx:].strip(" .!?")
            x = _reflect(tail) if tail else ""
            tpl = templates[int(rng.random() * len(templates))]
            return tpl.format(x=x if x else "that")
    return _FALLBACKS[int(rng.random() * len(_FALLBACKS))]

# ---------------- JOB RUNNER ----------------

async def run_job(job: Job) -> None:
    set_state(job, "running")

    mode = job.chosen_mode
    base_ms = job.base_latency_ms
    timeout_ms = job.timeout_ms

    # hang: never completes (client/Q must enforce timeout)
    if mode == "hang":
        return

    # server-side timeout mode
    if mode == "timeout":
        delay_ms = min(max(50, base_ms), max(50, timeout_ms))
        await asyncio.sleep(delay_ms / 1000)
        if job.state == "cancelled":
            return
        finish_timeout(job)
        return

    # fail
    if mode == "fail":
        # small delay so polling sees "running" sometimes
        await asyncio.sleep(min(200, base_ms) / 1000)
        if job.state == "cancelled":
            return
        finish_fail(job, "SIM_FAIL", "simulated error")
        return

    # latency
    ms = base_ms * 6 if mode == "slow" else base_ms
    await asyncio.sleep(ms / 1000)
    if job.state == "cancelled":
        return

    # flaky: deterministic-ish per-job RNG
    if mode == "flaky":
        r = random.Random(job.seed ^ int(job.id[-4:], 16) if job.id[-4:].isalnum() else job.seed)
        x = r.random()
        if x < 0.5:
            finish_fail(job, "SIM_FLAKY", "flaky failure")
            return
        if x < 0.7:
            return  # stall
        # else continue as ok (ELIZA)

    # echo: return args.messages verbatim (minified JSON string)
    if mode == "echo":
        payload = job.args.get("messages", [])
        txt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        finish_ok(job, txt)
        return

    # ok/slow/flaky-success: ELIZA for llm.chat, otherwise generic ok
    if job.op == "llm.chat":
        user_msg = extract_last_user_message(job.args)
        rng = random.Random(job.seed)
        finish_ok(job, eliza_reply(user_msg, rng))
    else:
        finish_ok(job, f"ok :: op={job.op}")

# ---------------- ROUTES ----------------

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "name": "dummyLLM",
        "time": now_sec(),
        "mode": DUMMYLLM_MODE,
        "latency_ms": DUMMYLLM_LATENCY_MS,
        "seed": DUMMYLLM_SEED,
        "random_weights": DUMMYLLM_RANDOM_WEIGHTS if DUMMYLLM_MODE == "random" else None,
    }

@app.post("/v1/jobs", response_model=JobCreateResp, status_code=201)
async def create_job(req: JobCreate) -> JobCreateResp:
    if not req.op or not isinstance(req.op, str):
        raise HTTPException(status_code=400, detail={"error": {"code": "BAD_REQUEST", "message": "Missing op"}})

    chosen = await choose_mode_for_job()

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    created = now_sec()

    job = Job(
        id=job_id,
        state="queued",
        created_at=created,
        updated_at=created,
        op=req.op,
        args=req.args,
        timeout_ms=req.timeout_ms,
        trace_id=req.trace_id,
        chosen_mode=chosen,
        base_latency_ms=DUMMYLLM_LATENCY_MS,
        seed=DUMMYLLM_SEED,
    )

    JOBS[job_id] = job
    job.task = asyncio.create_task(run_job(job))

    return JobCreateResp(id=job.id, state=job.state, created_at=job.created_at)

@app.get("/v1/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})

    return JobStatus(
        id=job.id,
        state=job.state,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result=job.result,
        error=job.error,
    )

@app.get("/v1/jobs/{job_id}/request", response_model=JobRequestView)
def get_job_request(job_id: str) -> JobRequestView:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})

    return JobRequestView(
        op=job.op,
        args=job.args,
        timeout_ms=job.timeout_ms,
        trace_id=job.trace_id,
        chosen_mode=job.chosen_mode,
        base_latency_ms=job.base_latency_ms,
        random_weights=DUMMYLLM_RANDOM_WEIGHTS if DUMMYLLM_MODE == "random" else None,
        seed=job.seed,
    )

@app.post("/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> Dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})

    if job.state in ("ok", "fail", "timeout", "cancelled"):
        return {"id": job.id, "state": job.state}

    finish_cancelled(job)

    if job.task and not job.task.done():
        job.task.cancel()

    return {"id": job.id, "state": job.state}
