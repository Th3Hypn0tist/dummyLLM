# server.py
# dummyLLM (Python/FastAPI) — deterministic-ish HTTP job queue simulator for Basic Q testing.
# Endpoints:
#   POST /v1/jobs
#   GET  /v1/jobs/{id}
#   POST /v1/jobs/{id}/cancel
#   GET  /health
#
# Modes: ok, fail, slow, hang, flaky, timeout

from __future__ import annotations

import time
import uuid
import random
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

State = Literal["queued", "running", "ok", "fail", "timeout", "cancelled"]
Mode = Literal["ok", "fail", "slow", "hang", "flaky", "timeout"]

app = FastAPI(title="dummyLLM", version="0.1.0")


def now_sec() -> int:
    return int(time.time())


class Simulate(BaseModel):
    mode: Mode = "ok"
    latency_ms: int = Field(default=250, ge=0)
    seed: Optional[int] = None
    fail_message: str = "simulated error"
    ok_text: Optional[str] = None


class JobCreate(BaseModel):
    op: str
    args: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=8000, ge=0)
    trace_id: Optional[str] = None
    simulate: Optional[Simulate] = None


class JobCreateResp(BaseModel):
    id: str
    state: State
    created_at: int


class JobError(BaseModel):
    code: str
    message: str


class JobResult(BaseModel):
    text: str
    usage: Dict[str, int] = Field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})


class JobStatus(BaseModel):
    id: str
    state: State
    created_at: int
    updated_at: int
    result: Optional[JobResult] = None
    error: Optional[JobError] = None


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
    simulate: Simulate
    result: Optional[JobResult] = None
    error: Optional[JobError] = None
    task: Optional[asyncio.Task] = None
    rng: Optional[random.Random] = None


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


async def run_job(job: Job) -> None:
    # queued -> running immediately
    set_state(job, "running")

    sim = job.simulate
    # hang = never finishes (q must timeout)
    if sim.mode == "hang":
        return

    # timeout = server marks timeout itself after some delay
    if sim.mode == "timeout":
        delay = min(sim.latency_ms, max(50, job.timeout_ms))
        await asyncio.sleep(delay / 1000)
        if job.state == "cancelled":
            return
        finish_timeout(job)
        return

    ms = sim.latency_ms
    if sim.mode == "slow":
        ms = sim.latency_ms * 6

    await asyncio.sleep(ms / 1000)

    if job.state == "cancelled":
        return

    if sim.mode == "fail":
        finish_fail(job, "SIM_FAIL", sim.fail_message)
        return

    if sim.mode == "flaky":
        rnd = job.rng.random() if job.rng else random.random()
        if rnd < 0.5:
            finish_fail(job, "SIM_FLAKY", "flaky failure")
        elif rnd < 0.7:
            # “stall after running” (never finishes)
            return
        else:
            txt = sim.ok_text if sim.ok_text is not None else f"ok (flaky) :: op={job.op}"
            finish_ok(job, txt)
        return

    # ok default
    txt = sim.ok_text if sim.ok_text is not None else f"ok :: op={job.op}"
    finish_ok(job, txt)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "name": "dummyLLM", "time": now_sec()}


@app.post("/v1/jobs", response_model=JobCreateResp, status_code=201)
async def create_job(req: JobCreate) -> JobCreateResp:
    if not req.op or not isinstance(req.op, str):
        raise HTTPException(status_code=400, detail={"error": {"code": "BAD_REQUEST", "message": "Missing op"}})

    sim = req.simulate or Simulate()
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
        simulate=sim,
        rng=(random.Random(sim.seed) if sim.seed is not None else None),
    )

    JOBS[job_id] = job
    job.task = asyncio.create_task(run_job(job))

    return JobCreateResp(id=job_id, state=job.state, created_at=job.created_at)


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


@app.post("/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> Dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "job not found"}})

    if job.state in ("ok", "fail", "timeout", "cancelled"):
        return {"id": job.id, "state": job.state}

    set_state(job, "cancelled")
    job.result = None
    job.error = JobError(code="CANCELLED", message="cancelled by client")

    # stop task if running
    if job.task and not job.task.done():
        job.task.cancel()

    return {"id": job.id, "state": job.state}
