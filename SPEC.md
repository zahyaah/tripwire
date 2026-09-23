# Spec: TripWire (project foundations)

This document specifies what is shared by every TripWire module. Module-specific requirements
live in `SPEC-<module-id>.md`, indexed by `CAPABILITY-MAP.md`.

## Assumptions

These were chosen deliberately. Correct any that are wrong before implementation starts.

1. Python 3.12 harness and agent, managed with `uv`. No `pip` or `requirements.txt`.
2. The agent under test calls Gemini (`gemini-3.8-flash`) via its OpenAI-compatible endpoint
   (`base_url="https://generativelanguage.googleapis.com/v1beta/openai/"`), using the `openai`
   Python SDK. Model calls run live locally and are replayed from recorded fixtures in CI, so CI
   needs no API key and costs nothing per build. Free tier, rate-limited to 20 requests/day/model
   — see ADR-0001 for the full provider history (this is the second swap) and its consequences.
3. Email data is fully synthetic and generated from a seeded generator. No real inbox, no
   scraped corpus, no PII.
4. Human labels are produced by hand by the repository author, roughly 120 labeled runs. The
   judge's reported agreement is against those labels, not against synthetic ground truth.
5. Traces are viewed in a self-contained local HTML file. No collector, no docker-compose, no
   OpenTelemetry in v1 (an exporter is a possible later adapter, explicitly out of scope now).
6. The regression suite runs on GitHub Actions. There is no other CI system to support.
7. The agent is a single-agent tool-use loop, not a multi-agent system, and not Managed Agents.

## Objective

Build a harness that evaluates a multi-step agent rather than a single model response, and makes
its behavior legible.

**Who it is for:** an engineer changing an agent's prompt, tools, or model who needs to know,
before merging, whether routing behavior got worse.

**What it must produce:**

1. A golden set of inputs with expected tool calls and expected outcomes.
2. Per-step assertions: right tool, right arguments, right order, and nothing forbidden.
3. A regression suite that runs in CI, so a prompt change that breaks routing fails the build.
4. LLM-as-judge scoring calibrated against human labels, reporting measured agreement instead of
   asserting the judge is correct.
5. Per-step cost, token, and latency tracking, with an openable trace for every run.

**Success looks like:** a reviewer can open a PR that changes the agent's system prompt, see the
build fail with the exact golden case and the exact step that broke, open the HTML trace for
that run, and read the cost delta — without running anything locally.

### Success criteria

Specific and testable. These are the project-level bar; per-task criteria are in `tasks/todo.md`.

- `uv run tripwire run --mode replay` executes the full golden set (target: 60 cases) with no
  network access and exits non-zero if any required assertion fails.
- Per-step assertions cover, at minimum: tool called, tool arguments (exact, subset, and regex
  matchers), call order as a subsequence, forbidden calls, maximum call counts, final outcome
  fields, and step/cost budgets.
- Routing accuracy on the golden set is reported as a single number, per intent, and per case.
- A deliberately broken prompt variant is committed as a demo and makes the regression gate fail
  with a diagnostic that names the case, the step, and the assertion.
- The judge reports, per rubric dimension: raw agreement, Cohen's kappa (binary dimensions),
  quadratic-weighted kappa (1-5 dimensions), and a bootstrap 95% confidence interval, computed
  on a holdout label split that was not used to tune the judge prompt.
- Any rubric dimension whose holdout kappa is below 0.6 is reported as low-confidence in the run
  summary rather than being silently treated as a score.
- Every run emits `runs/<run_id>/trace.jsonl` and `runs/<run_id>/trace.html`. The HTML opens in
  a browser with no network access and shows a per-step waterfall with tokens, cost, and latency.
- Per-step cost is computed from the API's reported usage (`prompt_tokens`/`completion_tokens`)
  against a versioned price table, never from a third-party tokenizer. If the catalog endpoint
  reports no billable price (free-tier/preview), cost is tracked as zero-priced tokens rather
  than a fabricated dollar figure, and the report says so explicitly.
- CI uploads the trace artifacts and posts a summary table (accuracy, judge scores with
  agreement, total cost, p95 step latency) on the pull request.

### Non-goals (v1)

Real email provider integration; OpenTelemetry export; a hosted dashboard; multi-agent
orchestration; training or fine-tuning; evaluating agents written in other languages.

## Tech Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| Package and env manager | `uv` (`pyproject.toml` + `uv.lock`, committed) |
| Model access | `openai` Python SDK against Gemini's OpenAI-compat endpoint (`base_url=https://generativelanguage.googleapis.com/v1beta/openai/`) — see ADR-0001 |
| Agent model | `gemini-3.8-flash` |
| Judge model | `gemini-3.8-flash` — same model as the agent for now; a distinct judge model is future work, not required for v1 |
| Schemas and validation | `pydantic` v2 |
| CLI | `typer` |
| Golden set format | YAML (`pyyaml`) |
| HTML report | `jinja2`, inlined CSS and JS, no CDN |
| Terminal output | `rich` |
| Tests | `pytest`, `pytest-cov` |
| Lint and format | `ruff` |
| Types | `mypy` (strict on `src/tripwire`) |
| Statistics | hand-written kappa and bootstrap in `src/tripwire/judge/stats.py`; no scikit-learn |

Model-call rules that are not negotiable, because getting them wrong invalidates the metrics:

- Judge output via structured output (OpenAI-compatible `response_format={"type": "json_schema",
  ...}` generated from the pydantic rubric model) if the catalog endpoint supports it; a
  schema-in-prompt plus strict pydantic `model_validate_json` fallback otherwise. No prefill, no
  regex parsing of judge prose. Confirm which path applies before Task 12 (see Open Questions).
- Token counts come from `response.usage` (`prompt_tokens`, `completion_tokens`,
  `total_tokens`) — the only source of truth, since there is no separate count-tokens endpoint on
  this API. No third-party tokenizer (`tiktoken` included) stands in for it, including for
  pre-call estimates.
- Tool inputs are parsed as JSON objects from `tool_calls[].function.arguments`, never
  string-matched.
- The agent uses a manual agentic loop, not a vendor tool-runner helper, because the harness must
  observe and record every step boundary itself.
- No adaptive-thinking or budget-token concept applies to this model; drop those parameters
  entirely rather than porting them.

## Commands

```
Install:        uv sync
Add a dep:      uv add <package>
Test (fast):    uv run pytest -q -m "not regression"
Test (all):     uv run pytest -q
Regression:     uv run pytest -q -m regression
Coverage:       uv run pytest --cov=src/tripwire --cov-report=term-missing
Lint:           uv run ruff check --fix .
Format:         uv run ruff format .
Types:          uv run mypy src
Run eval:       uv run tripwire run --suite data/golden --mode replay
Run live:       uv run tripwire run --suite data/golden --mode record
Report:         uv run tripwire report --run latest --open
Label:          uv run tripwire label --run <run_id>
Calibrate:      uv run tripwire calibrate --labels data/labels/human.jsonl
Gate:           uv run tripwire gate --run latest --baseline baselines/main.json
Generate data:  uv run tripwire gen-corpus --seed 1337 --threads 400
```

`--mode` is one of `live` (call the API, record nothing), `record` (call the API, write
cassettes), `replay` (cassettes only; a cassette miss is a hard failure naming the missing key).
The default is read from `TRIPWIRE_LLM_MODE`, and CI sets `replay`.

## Project Structure

```
src/tripwire/
  core/          Run and span schemas, run ids, clock, JSONL trace store
  cost/          Versioned price table, usage rollup, budget checks
  llm/           OpenAI-compatible client wrapper (NVIDIA catalog), span emission, cassette record/replay
  assertions/    Matchers and the assertion runner
  judge/         Rubric, judge client, label store, calibration stats
  labeling/      Interactive labeling CLI
  report/        HTML trace viewer and run summary rendering
  cli.py         typer entry point (run, report, label, calibrate, gate, gen-corpus)
agents/
  inbox_triage/  The agent under test: prompt, tool schemas, tool impls, loop
data/
  corpus/        Generated synthetic threads, customers, orders (committed, deterministic)
  golden/        Golden set YAML cases
  labels/        Human labels (JSONL) and the dev/holdout split manifest
fixtures/
  cassettes/     Recorded model calls, keyed by request hash (committed)
tests/
  unit/          Fast, no network, no cassettes needed
  regression/    Golden set suite, marked `regression`, replay mode
runs/            Trace output, gitignored
baselines/       Committed baseline metrics for the regression gate
docs/            Findings, judge calibration write-up, architecture notes
.github/workflows/eval.yml
```

## Code Style

Typed, small, boring. Pydantic models at every boundary that crosses a module. One real example
sets the tone better than a list of rules:

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class ToolCallSpan(BaseModel):
    """One tool invocation inside an agent run."""

    span_id: str
    parent_span_id: str
    step_index: int
    tool_name: str
    arguments: dict[str, object]
    result_summary: str
    latency_ms: int
    is_error: bool = False


class StepAssertionResult(BaseModel):
    assertion_id: str
    passed: bool
    required: bool = True
    detail: str = Field(default="", description="Human-readable reason on failure")

    @property
    def blocks_build(self) -> bool:
        return self.required and not self.passed
```

Conventions:

- `from __future__ import annotations` in every module; built-in generics (`list[str]`).
- Functions are typed, including returns. `mypy --strict` clean under `src/tripwire`.
- No bare `except`. SDK errors are caught by specific `openai` exception classes, most specific
  first, never by string-matching messages.
- No mutable default arguments; no module-level I/O at import time.
- Names say what the thing is: `expected_tool_calls`, not `etc`. No invented abbreviations.
- Docstrings on public functions and on every pydantic model. One line is enough when one line
  is true.
- Money is `Decimal` or integer micro-dollars, never a float that gets summed across 60 runs.
- Assertion failures produce actionable messages: expected, actual, case id, step index.

## Testing Strategy

Four levels, each with a distinct job:

1. **Unit** (`tests/unit/`, no network, no cassettes): schemas, cost math, matchers, kappa and
   bootstrap statistics, cassette key hashing, trace serialization round-trips. Hand-computed
   expected values for the statistics — a kappa implementation tested only against itself is
   worthless. Runs in under 5 seconds.
2. **Tool-level** (`tests/unit/`): the agent's tool implementations against the synthetic corpus,
   with no model in the loop at all.
3. **Regression** (`tests/regression/`, marked `regression`, replay mode): one parametrized test
   per golden case, asserting the case's per-step assertions. The parametrize id is the case id,
   so a failure names the case in the pytest output.
4. **Calibration** (`tests/regression/`): the judge runs over cassetted runs with committed human
   labels, and the test asserts the computed agreement statistics match committed expected values
   within tolerance. This tests the calibration pipeline, not the judge's opinion.

Rules:

- Coverage bar: 85% line coverage on `src/tripwire`. `agents/` is exercised through the
  regression suite rather than chased for coverage.
- No test calls the live API. Live calls happen only through `tripwire run --mode record|live`,
  driven by a human.
- Every bug fix starts with a failing test that reproduces it.
- Tests must be order-independent and must not write into `data/` or `fixtures/`; run output goes
  to a tmp path.

## Boundaries

**Always:**

- Run `uv run ruff check`, `uv run mypy src`, and `uv run pytest -q` before committing.
- Keep `uv.lock` committed and in sync with `pyproject.toml`.
- Update `SPEC.md` or the relevant `SPEC-<module>.md` before implementing a change to a
  documented decision.
- Record the model id, effort, prompt hash, and harness version in every run record, so a metric
  is always attributable to a configuration.
- Report a judge dimension with its agreement number attached, never alone.

**Ask first:**

- Adding a dependency (`uv add`), especially anything that pulls a scientific stack.
- Changing the golden set expectations, the judge rubric, or the gate thresholds — these are the
  measuring instruments, and silently loosening them defeats the project.
- Re-recording cassettes in bulk, which changes cost and latency numbers under every metric.
- Changing the agent's model or effort level, which invalidates the committed baselines.
- Any schema change to trace records that existing committed traces cannot be read under.

**Never:**

- Commit an API key, a real email address, or real customer data.
- Use `tiktoken` or any third-party tokenizer for token counts — `response.usage` only.
- Delete, skip, or `xfail` a failing golden case to make the build green.
- Tune the judge prompt against the holdout label split.
- Let the agent under test import from `src/tripwire/assertions` or `src/tripwire/judge` — the
  thing being measured must not see the measuring instrument.
- Fabricate a metric. If a number was not measured in a recorded run, it does not go in a report
  or a README.

## Open Questions

1. Golden set intent mix: how many of the 60 cases should be adversarial (spam that looks urgent,
   an angry customer whose issue is actually a FAQ, a thread that requires refusing to act)?
   Current assumption: 15 of 60.
2. Judge rubric dimensions. Current assumption: `reply_helpfulness` (1-5), `tone_match` (1-5),
   `escalation_appropriate` (binary), `contains_unsupported_claim` (binary).
3. Gate policy for judge scores: block the build on a judge regression, or report only? Current
   assumption: block on deterministic assertions and routing accuracy, report-only on judge
   dimensions whose holdout kappa is below 0.6, block on dimensions at or above 0.6.
4. Whether v1 ships a second agent (even a trivial one) to prove the harness is not
   single-agent-shaped. Current assumption: no, but no module may import from `agents/`.
5. ~~Exact Nemotron 3.5 Lightning catalog model id~~ — **moot**: the provider swapped to Gemini
   before this was ever needed (ADR-0001). Gemini's model id is confirmed live via
   `client.models.list()` against the real API: `gemini-3.8-flash`. `PRICE_TABLE`'s key (Task 3)
   matches it.
6. ~~Whether Nemotron 3.5 Lightning supports native tool/function calling~~ — **moot** (ADR-0001,
   provider swap). Gemini's native `tool_calls` support is confirmed live: a real call with a
   `strict: true` function tool returned `finish_reason="tool_calls"` with a correctly-shaped
   `tool_calls[0].function`.
7. ~~Whether the NVIDIA API catalog endpoint for this model is metered or free/preview~~ —
   **moot** (ADR-0001, provider swap). Gemini's free tier is confirmed rate-limited to 20
   requests/day/model, not metered; `billable=False` in `PRICE_TABLE` (Task 3) reflects this.
8. `max_tokens` (not OpenAI-proper's newer `max_completion_tokens`) is confirmed live to work
   against Gemini's OpenAI-compat endpoint; the gateway (Task 4) uses it on that basis.
9. Gemini 3's `usage.total_tokens` exceeds `prompt_tokens + completion_tokens` by 40-90% live,
   with no `*_tokens_details` populated to explain it — hidden "thinking" tokens billed but not
   itemized through the compat layer. `TokenUsage.reasoning_tokens` (`core/records.py`) recovers
   this gap; see `usage_from_completion` in `llm/gateway.py`.
