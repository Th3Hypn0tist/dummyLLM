# dummyLLM

Deterministic, environment-controlled LLM job simulator for testing Q /
runner systems.

dummyLLM behaves like a minimal async LLM backend while keeping all
testing controls **server-side only**.

Client payloads remain strictly standard.

------------------------------------------------------------------------

## Version

0.3.1

------------------------------------------------------------------------

## Design Goals

-   Standard client payload (no simulate flags)
-   Deterministic execution
-   Reproducible chaos testing
-   Server-side behavior control via ENV
-   ELIZA default mode for interactive testing
-   Echo mode for payload verification

This is not a real LLM.\
It is a deterministic job behavior simulator.

------------------------------------------------------------------------

## Standard Client Payload

POST `/v1/jobs`

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
  "trace_id": "optional"
}
```

No simulation parameters are allowed in the request.

------------------------------------------------------------------------

## Environment Configuration

### Global Mode

    DUMMYLLM_MODE=ok|echo|slow|fail|hang|timeout|flaky|random

Default (if unset):

    ok

### Mode Behavior

  Mode      Behavior
  --------- ---------------------------------------------------------
  ok        ELIZA chat response
  echo      Returns `args.messages` verbatim (minified JSON string)
  slow      ok but latency ×6
  fail      SIM_FAIL
  hang      Never completes (client must timeout)
  timeout   Server returns TIMEOUT
  flaky     Deterministic fail/hang/ok mix
  random    Weighted random per job

------------------------------------------------------------------------

### Random Mode Weights

    DUMMYLLM_RANDOM_WEIGHTS="ok=70,echo=10,slow=10,fail=5,hang=3,timeout=2,flaky=0"

If all weights are zero → defaults to `ok`.

------------------------------------------------------------------------

### Base Latency

    DUMMYLLM_LATENCY_MS=250

Used by ok/echo.\
`slow` multiplies this by 6.

------------------------------------------------------------------------

### Deterministic Seed

    DUMMYLLM_SEED=1337

Same seed = reproducible random mode selection and ELIZA variation.

------------------------------------------------------------------------

## Installation

``` bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

------------------------------------------------------------------------

## Run

``` bash
uvicorn server:app --host 127.0.0.1 --port 8787
```

Optional env override example:

``` bash
export DUMMYLLM_MODE=random
export DUMMYLLM_RANDOM_WEIGHTS="ok=70,echo=10,slow=10,fail=5,hang=3,timeout=2,flaky=0"
export DUMMYLLM_LATENCY_MS=250
export DUMMYLLM_SEED=1337

uvicorn server:app --host 127.0.0.1 --port 8787
```

------------------------------------------------------------------------

## API Endpoints

### Health

GET `/health`

Returns active mode, latency, seed, and weights (if random).

------------------------------------------------------------------------

### Create Job

POST `/v1/jobs`

Returns job id.

------------------------------------------------------------------------

### Get Job Status

GET `/v1/jobs/{id}`

States:

    queued | running | ok | fail | timeout | cancelled

------------------------------------------------------------------------

### Cancel Job

POST `/v1/jobs/{id}/cancel`

------------------------------------------------------------------------

### Debug Request Inspection

GET `/v1/jobs/{id}/request`

Returns:

-   Original op
-   Original args
-   timeout_ms
-   trace_id
-   chosen_mode
-   base_latency_ms
-   seed
-   random_weights (if random mode)

This allows verification that Q did not mutate payload.

------------------------------------------------------------------------

## Testing Philosophy

dummyLLM validates:

-   State transitions
-   Timeout logic
-   Cancellation
-   Retry behavior
-   Payload integrity

Q remains clean and standards-compliant.\
dummyLLM acts as a deterministic chaos engine.

------------------------------------------------------------------------

## License

MIT (or your choice)
