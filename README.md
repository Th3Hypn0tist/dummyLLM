# dummyLLM

Deterministic HTTP job-queue simulator for testing LLM integrations.

dummyLLM acts as a controllable backend for execution engines, task
runners, or agent systems before integrating a real language model. It
provides predictable behavior including success, failure, slow
responses, hangs, and flaky execution modes.

## Features

-   HTTP job-based API
-   Deterministic simulation (seed support)
-   Controlled latency
-   Failure simulation
-   Hang simulation (client must enforce timeout)
-   Cancel endpoint
-   In-memory store
-   Minimal dependencies (FastAPI + Uvicorn)

## API

### GET /health

Health check.

Response:

``` json
{
  "ok": true,
  "name": "dummyLLM",
  "time": 1700000000
}
```

### POST /v1/jobs

Create a simulated job.

Request body:

``` json
{
  "op": "llm.chat",
  "args": {
    "model": "llama3.1:8b",
    "messages": [
      {"role": "user", "content": "Hello"}
    ]
  },
  "timeout_ms": 8000,
  "trace_id": "optional-trace-id",
  "simulate": {
    "mode": "ok | fail | slow | hang | flaky | timeout",
    "latency_ms": 250,
    "seed": 1337,
    "fail_message": "simulated error",
    "ok_text": "custom success text"
  }
}
```

Response:

``` json
{
  "id": "job_xxxxxxxxxxxx",
  "state": "queued",
  "created_at": 1700000000
}
```

### GET /v1/jobs/{id}

Retrieve job status.

Response:

``` json
{
  "id": "job_xxxxxxxxxxxx",
  "state": "queued | running | ok | fail | timeout | cancelled",
  "created_at": 1700000000,
  "updated_at": 1700000001,
  "result": {
    "text": "assistant response",
    "usage": {
      "prompt_tokens": 0,
      "completion_tokens": 0
    }
  },
  "error": {
    "code": "SIM_FAIL | TIMEOUT | CANCELLED",
    "message": "error message"
  }
}
```

### POST /v1/jobs/{id}/cancel

Cancel a running job.

Response:

``` json
{
  "id": "job_xxxxxxxxxxxx",
  "state": "cancelled"
}
```

## Simulation Modes

  Mode      Behavior
  --------- --------------------------------------------------
  ok        Completes successfully
  fail      Returns simulated failure
  slow      Completes successfully but with extended latency
  hang      Never completes (client must enforce timeout)
  flaky     Random success/failure/stall (seedable)
  timeout   Server marks job as timeout

If `seed` is provided, flaky mode becomes deterministic.

## Installation

``` bash
git clone <repo-url>
cd dummyLLM

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

``` bash
uvicorn server:app --host 127.0.0.1 --port 8787
```

Server:

-   http://127.0.0.1:8787\
-   Swagger UI: http://127.0.0.1:8787/docs

## Example

Create job:

``` bash
curl -X POST http://127.0.0.1:8787/v1/jobs   -H "Content-Type: application/json"   -d '{
    "op": "llm.chat",
    "args": {"model": "llama3.1:8b"},
    "simulate": {"mode": "ok", "latency_ms": 200}
  }'
```

Poll status:

``` bash
curl http://127.0.0.1:8787/v1/jobs/<JOB_ID>
```

## Intended Use

dummyLLM is useful for:

-   Testing task execution engines
-   Validating timeout logic
-   Testing retry strategies
-   Simulating flaky backends
-   CI integration tests
-   Offline development

## Design Philosophy

dummyLLM does not attempt to emulate LLM semantics.

It simulates execution behavior only:

-   state transitions
-   latency
-   failure modes
-   cancellation

This makes it suitable for validating execution pipelines independently
of model quality.

## License

MIT
