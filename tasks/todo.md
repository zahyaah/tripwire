# TripWire Task List

Plan: `tasks/plan.md`. Specs: `SPEC.md`, `CAPABILITY-MAP.md`.
Module ids in brackets match `CAPABILITY-MAP.md`.

Definition of done for every task: `uv run ruff check` clean, `uv run mypy src` clean,
`uv run pytest -q` green, no new dependency added without asking, spec updated if a documented
decision changed.

---

## Phase 1: Foundation

## Task 1: Repository scaffold [trace-core]

**Description:** Create the uv-managed Python 3.12 project with the directory layout from
`SPEC.md`, the `tripwire` CLI entry point as a stub, and lint/type/test tooling wired up.

**Acceptance criteria:**
- [ ] `pyproject.toml` declares Python 3.12, the runtime dependencies, dev dependencies, the
      `tripwire` console script, `ruff`, `mypy` (strict on `src/tripwire`), and the `regression`
      pytest marker; `uv.lock` is committed
- [ ] `uv run tripwire --help` lists the planned subcommands (run, report, label, calibrate, gate,
      gen-corpus), each stubbed to exit non-zero with "not implemented"
- [ ] `runs/` is gitignored; `data/`, `fixtures/cassettes/`, `baselines/`, `docs/` exist

**Verification:**
- [ ] `uv sync && uv run pytest -q` passes (one smoke test asserting the CLI help text)
- [ ] `uv run mypy src` clean
- [ ] Manual check: `uv run tripwire run` exits non-zero with the stub message

**Dependencies:** None

**Files likely touched:** `pyproject.toml`, `uv.lock`, `.gitignore`, `src/tripwire/__init__.py`,
`src/tripwire/cli.py`, `tests/unit/test_cli_smoke.py`

**Estimated scope:** S

---

## Task 2: Trace schemas and JSONL store [trace-core]

**Description:** Define the run/span record model and the append-only JSONL trace store. This
schema is the contract for assertions, the judge, and the report, and gets frozen at the Phase 1
checkpoint.

**Acceptance criteria:**
- [ ] Pydantic models for `RunRecord` (run id, case id, agent config: model, effort, prompt hash,
      harness version, started/finished timestamps, outcome) and `Span` variants
      (`agent_run`, `model_call`, `tool_call`, `judge_call`) with span id, parent span id, step
      index, latency in ms, and an error flag
- [ ] `TraceWriter` appends spans to `runs/<run_id>/trace.jsonl` as they happen (crash-safe: a
      partial run is still readable); `TraceReader` loads a run back into typed objects
- [ ] A schema version field is written on every record, and reading an unknown version raises a
      clear error rather than silently mis-parsing

**Verification:**
- [ ] Tests: round-trip a multi-step run through write and read with identical objects; a
      truncated final line is tolerated by the reader with a warning
- [ ] `uv run pytest -q -k trace` green

**Dependencies:** Task 1

**Files likely touched:** `src/tripwire/core/records.py`, `src/tripwire/core/store.py`,
`src/tripwire/core/ids.py`, `tests/unit/test_records.py`, `tests/unit/test_store.py`

**Estimated scope:** M

---

## Task 3: Price table and usage rollup [trace-core]

**Description:** Price a model call from the API's reported usage, and roll per-step usage up to a
run total. Money as integer micro-dollars.

**Acceptance criteria:**
- [ ] Versioned price table with an `as_of` date covering the models in use, with separate rates
      for input, output, cache read, and cache write tokens
- [ ] `price_call(model, usage)` returns micro-dollars; an unknown model id raises rather than
      defaulting to zero
- [ ] `rollup(run)` returns per-step and total tokens, micro-dollars, and latency, plus p95 step
      latency

**Verification:**
- [ ] Tests: three hand-computed cases, including one with cache reads and one with cache writes,
      match to the micro-dollar
- [ ] Test: unknown model id raises `UnknownModelError`

**Dependencies:** Task 2

**Files likely touched:** `src/tripwire/cost/prices.py`, `src/tripwire/cost/rollup.py`,
`tests/unit/test_prices.py`, `tests/unit/test_rollup.py`

**Estimated scope:** S

---

## Task 4: Instrumented model gateway with record/replay [trace-core]

**Description:** Wrap the Anthropic client so every call emits a `model_call` span with usage,
cost, and latency, and so calls can be recorded to and replayed from cassettes.

**Acceptance criteria:**
- [ ] `ModelGateway.create(...)` emits a `model_call` span (model, effort, stop reason, usage,
      micro-dollars, latency) and returns the SDK response unchanged to the caller
- [ ] Three modes (`live`, `record`, `replay`) selected by argument or `TRIPWIRE_LLM_MODE`;
      cassette key is a stable hash over the semantically relevant request fields (model, system,
      messages, tools, effort, thinking config), insensitive to dict ordering
- [ ] Replay restores the recorded usage and recorded latency so cost and latency metrics stay
      real; a cassette miss raises `CassetteMissError` printing the key and the re-record command
- [ ] Adaptive thinking is set on every call; errors are caught by specific SDK exception classes,
      most specific first

**Verification:**
- [ ] Tests (no network): record against a fake transport, then replay and assert identical
      response content, usage, and span cost; assert a modified request misses the cassette
- [ ] Test: `budget_tokens` is never sent
- [ ] Manual check: one real `--mode record` call against the API writes a cassette that replays

**Dependencies:** Task 3

**Files likely touched:** `src/tripwire/llm/gateway.py`, `src/tripwire/llm/cassettes.py`,
`src/tripwire/llm/keys.py`, `tests/unit/test_gateway.py`, `tests/unit/test_cassettes.py`

**Estimated scope:** M

---

### Checkpoint: Foundation
- [ ] `uv run pytest -q` green; `uv run mypy src` clean
- [ ] A recorded call replays with usage and latency intact
- [ ] Cost of a replayed call matches a hand-computed figure
- [ ] Span schema reviewed and frozen with the human before Phase 2

---

## Phase 2: Agent and first end-to-end case

## Task 5: Synthetic corpus generator [synthetic-data]

**Description:** Generate a deterministic synthetic inbox: threads with messages, plus the
customer and order records the agent can look up. Seeded, committed, no real data.

**Acceptance criteria:**
- [ ] `uv run tripwire gen-corpus --seed 1337 --threads 400` writes `data/corpus/` and is
      byte-identical across runs for the same seed
- [ ] Threads span the intent taxonomy (billing, refund, bug report, feature request, FAQ, spam,
      angry escalation, must-refuse) with an intent label on each thread for reporting only, not
      visible to the agent
- [ ] Customers and orders cross-reference thread senders so lookups return real context, and
      some threads deliberately reference a missing order

**Verification:**
- [ ] Tests: two generations with the same seed match; different seeds differ; every thread's
      sender resolves to a customer or is explicitly marked unknown
- [ ] Manual check: read 5 generated threads and confirm they read like plausible email

**Dependencies:** Task 2

**Files likely touched:** `src/tripwire/../agents/inbox_triage/corpus.py` (generator under
`src/tripwire/data/`), `data/corpus/*.json`, `tests/unit/test_corpus.py`

**Estimated scope:** M

---

## Task 6: Agent tool schemas and implementations [agent]

**Description:** Define the Inbox Triage Agent's tools and implement them against the synthetic
corpus, with no model in the loop.

**Acceptance criteria:**
- [ ] Tools defined with JSON schemas and `strict: true` where arguments are structured:
      `search_threads`, `get_thread`, `lookup_customer`, `lookup_order`, `add_label`,
      `draft_reply`, `escalate`, `snooze`, `archive`
- [ ] Each implementation is a pure function over corpus state plus a mutation log, returning a
      compact string or JSON result; failures return an error result rather than raising
- [ ] The tool layer does not import anything from `src/tripwire/assertions` or
      `src/tripwire/judge`

**Verification:**
- [ ] Tests: each tool's happy path and one failure path (missing order, unknown thread id)
- [ ] Test: an import-boundary test asserts `agents/` does not import the scoring modules

**Dependencies:** Task 5

**Files likely touched:** `agents/inbox_triage/tools.py`, `agents/inbox_triage/schemas.py`,
`agents/inbox_triage/state.py`, `tests/unit/test_tools.py`, `tests/unit/test_boundaries.py`

**Estimated scope:** M

---

## Task 7: Instrumented agent loop [agent]

**Description:** Implement the manual agentic loop: call the model through the gateway, execute
tool calls, feed results back, emit a `tool_call` span per call, and stop on end turn, step
budget, or cost budget.

**Acceptance criteria:**
- [ ] Manual loop handles `tool_use`, multiple parallel tool calls in one assistant message
      (all results returned in a single user message), `end_turn`, and `max_tokens`
- [ ] Tool inputs parsed as JSON objects, never string-matched; a failed tool returns a
      `tool_result` with `is_error: true` rather than being dropped
- [ ] Step budget and cost budget terminate the run with an explicit `budget_exceeded` outcome
      recorded on the `RunRecord`
- [ ] System prompt lives in one file and its hash is recorded on the run

**Verification:**
- [ ] Tests in replay mode: a 4-step run produces 1 `agent_run`, 4 `model_call`, and N
      `tool_call` spans with correct parents and step indices
- [ ] Test: a cassette scripting an infinite tool loop terminates at the step budget
- [ ] Manual check: one `--mode record` run end to end and read its trace JSONL

**Dependencies:** Task 4, Task 6

**Files likely touched:** `agents/inbox_triage/loop.py`, `agents/inbox_triage/prompt.md`,
`src/tripwire/core/runner.py`, `tests/unit/test_loop.py`

**Estimated scope:** M

---

## Task 8: Golden set format, loader, and seed cases [synthetic-data]

**Description:** Define the YAML golden case format — input, expected tool calls with argument
matchers, expected order, forbidden calls, expected outcome, budgets — and write 8 seed cases.

**Acceptance criteria:**
- [ ] YAML schema validated by pydantic on load, with a clear error naming file, case id, and
      field on malformed input
- [ ] Each case declares: case id, intent, input thread id, expected ordered tool calls with a
      matcher per argument, forbidden tools, expected final outcome, step budget, cost budget,
      and whether each assertion is required or advisory
- [ ] 8 seed cases covering at least: FAQ reply, refund needing an order lookup, escalation,
      spam archive, and one must-refuse case

**Verification:**
- [ ] Tests: loader accepts all committed cases; three malformed fixtures produce specific errors
- [ ] `uv run tripwire run --suite data/golden --mode replay` lists 8 cases (assertions land next)

**Dependencies:** Task 5

**Files likely touched:** `src/tripwire/core/golden.py`, `data/golden/*.yaml`,
`tests/unit/test_golden_loader.py`

**Estimated scope:** M

---

## Task 9: Assertion matchers [assertions]

**Description:** Implement the matcher library that decides whether a recorded run satisfies a
golden case's expectations.

**Acceptance criteria:**
- [ ] Matchers: `tool_called`, `tool_args` (exact, subset, regex), `order` (subsequence by
      default, strict-adjacency opt-in), `not_called`, `max_calls`, `final_outcome`,
      `step_budget`, `cost_budget`
- [ ] Every matcher returns a `StepAssertionResult` with a failure message stating expected,
      actual, case id, and step index
- [ ] Matchers read only recorded trace records; they never touch agent objects

**Verification:**
- [ ] Tests: pass and fail path for each matcher, including the subsequence-versus-strict order
      distinction and a regex argument match
- [ ] Test: failure messages contain case id and step index

**Dependencies:** Task 2, Task 8

**Files likely touched:** `src/tripwire/assertions/matchers.py`,
`src/tripwire/assertions/results.py`, `tests/unit/test_matchers.py`

**Estimated scope:** M

---

## Task 10: Assertion runner and regression suite [assertions]

**Description:** Run a golden case end to end (execute agent in replay mode, evaluate assertions,
persist results into the trace) and expose it as a parametrized pytest suite.

**Acceptance criteria:**
- [ ] `run_case(case)` returns a `CaseResult` with per-assertion results, routing verdict, cost,
      and the run id; results are written alongside the trace
- [ ] `tests/regression/test_golden_set.py` parametrizes over all cases with the case id as the
      pytest parameter id and fails on any required assertion failure
- [ ] `uv run tripwire run` prints a per-case table and exits non-zero on any required failure

**Verification:**
- [ ] `uv run pytest -q -m regression` green on the 8 seed cases
- [ ] Manual check: change one expected argument in a YAML case; the failure output names the
      case, the step, and the expected-versus-actual argument

**Dependencies:** Task 7, Task 9

**Files likely touched:** `src/tripwire/assertions/runner.py`, `src/tripwire/cli.py`,
`tests/regression/test_golden_set.py`

**Estimated scope:** M

---

### Checkpoint: End-to-end
- [ ] 8 seed cases pass in replay mode with no network access
- [ ] A broken expectation produces an actionable failure
- [ ] Every run writes a readable trace JSONL
- [ ] Review with the human before scaling the golden set

---

## Phase 3: Breadth and the judge

## Task 11: Expand the golden set to 60 cases [synthetic-data]

**Description:** Write the remaining golden cases across the intent taxonomy, including 15
adversarial cases, and record cassettes for all of them.

**Acceptance criteria:**
- [ ] 60 cases committed, each labeled with intent and an `adversarial` flag; 15 adversarial
- [ ] Cassettes committed for every case; `uv run pytest -m regression` runs offline
- [ ] At least one case the current agent fails is committed and documented in `docs/known-gaps.md`
      rather than deleted or weakened

**Verification:**
- [ ] `uv run tripwire run --suite data/golden --mode replay` reports overall and per-intent
      routing accuracy
- [ ] Test: every case id is unique and every case has at least one required assertion

**Dependencies:** Task 10

**Files likely touched:** `data/golden/*.yaml`, `fixtures/cassettes/*`, `docs/known-gaps.md`

**Estimated scope:** M

---

## Task 12: Judge rubric and structured-output judge call [judge]

**Description:** Score a recorded run against a rubric using an LLM judge with structured output,
recorded as a `judge_call` span with its own cost.

**Acceptance criteria:**
- [ ] Pydantic rubric model with `reply_helpfulness` (1-5), `tone_match` (1-5),
      `escalation_appropriate` (bool), `contains_unsupported_claim` (bool), plus a short rationale
      per dimension
- [ ] Judge called via `client.messages.parse(..., output_format=...)` through the gateway, so it
      is cassetted, priced, and traced like any other call; no prefill, no prose parsing
- [ ] The judge sees the run transcript and the corpus context, and explicitly does not see the
      golden expectations or the human label

**Verification:**
- [ ] Tests in replay mode: judge output validates into the rubric model; a malformed cassette
      response raises rather than yielding partial scores
- [ ] Test: judge cost appears in the run rollup separately from agent cost

**Dependencies:** Task 11

**Files likely touched:** `src/tripwire/judge/rubric.py`, `src/tripwire/judge/judge.py`,
`src/tripwire/judge/prompt.md`, `tests/unit/test_judge.py`

**Estimated scope:** M

---

## Task 13: Labeling CLI and label store [judge]

**Description:** A keyboard-driven CLI for labeling recorded runs on the same rubric a human uses,
writing an append-only JSONL label store with a dev/holdout split manifest.

**Acceptance criteria:**
- [ ] `uv run tripwire label --run <id>` shows the transcript and prompts each dimension; labels
      append to `data/labels/human.jsonl` with labeler id, timestamp, and rubric version
- [ ] Resumable: already-labeled runs are skipped; partial sessions lose nothing
- [ ] `data/labels/split.json` assigns each labeled run to dev or holdout, generated once from a
      fixed seed and never regenerated silently

**Verification:**
- [ ] Tests: label round-trip; re-running skips labeled runs; split is stable across invocations
- [ ] Manual check: label 5 runs and confirm the flow is fast enough to do 60 in a sitting

**Dependencies:** Task 12

**Files likely touched:** `src/tripwire/labeling/cli.py`, `src/tripwire/judge/labels.py`,
`data/labels/split.json`, `tests/unit/test_labels.py`

**Estimated scope:** M

---

## Task 14: Calibration statistics and agreement report [judge]

**Description:** Compare judge scores to human labels and report measured agreement per dimension,
computed on the holdout split.

**Acceptance criteria:**
- [ ] Hand-written `stats.py`: raw agreement, Cohen's kappa (binary), quadratic-weighted kappa
      (1-5), confusion matrix, and a bootstrap 95% CI
- [ ] `uv run tripwire calibrate` prints and writes a per-dimension report: n, agreement, kappa,
      CI, and a confidence verdict (kappa below 0.6 is low-confidence)
- [ ] Dev and holdout numbers are reported separately, and the holdout figure is the headline

**Verification:**
- [ ] Tests: kappa and weighted kappa match hand-computed values on small fixed tables, including
      the degenerate all-agree and all-disagree cases; bootstrap CI is deterministic under a
      fixed seed
- [ ] Manual check: `docs/judge-calibration.md` written from the real report, including whichever
      dimensions came out weak

**Dependencies:** Task 13

**Files likely touched:** `src/tripwire/judge/stats.py`, `src/tripwire/judge/calibration.py`,
`docs/judge-calibration.md`, `tests/unit/test_stats.py`

**Estimated scope:** M

---

### Checkpoint: Measurement
- [ ] Routing accuracy reported overall, per intent, per case
- [ ] 120 labels committed with a fixed dev/holdout split
- [ ] Holdout agreement per dimension with a bootstrap CI
- [ ] Weak dimensions published as low-confidence, not hidden

---

## Phase 4: Observability and the gate

## Task 15: Self-contained HTML trace viewer [report]

**Description:** Render a run's trace as a single offline HTML file: step waterfall, drill-down,
assertion overlay, and per-step cost.

**Acceptance criteria:**
- [ ] `runs/<run_id>/trace.html` is one file with inlined CSS and JS, no network requests, and
      opens from a CI artifact download
- [ ] Shows per-step tool name, arguments, result, latency, tokens, micro-dollars, and pass/fail
      badges for the assertions attached to that step; judge scores shown with their agreement
      figure attached
- [ ] Renders a partial trace from a crashed run without erroring

**Verification:**
- [ ] Tests: generated HTML contains no `http://` or `https://` asset references; a partial trace
      renders; a run with a failed assertion renders the failure detail
- [ ] Manual check: open a real trace, follow one case's routing decision end to end

**Dependencies:** Task 14

**Files likely touched:** `src/tripwire/report/html.py`, `src/tripwire/report/templates/trace.html.j2`,
`tests/unit/test_html_report.py`

**Estimated scope:** M

---

## Task 16: Run summary in Markdown and JSON [report]

**Description:** Emit a suite-level summary suitable for a PR comment and for the regression gate.

**Acceptance criteria:**
- [ ] `runs/<suite_run_id>/summary.json` and `summary.md`: per-case verdicts, routing accuracy
      overall and per intent, judge scores with agreement and confidence verdict, total and
      per-case cost, p95 step latency, and the agent config (model, effort, prompt hash)
- [ ] `uv run tripwire report --run latest --open` regenerates the summary and opens the HTML
- [ ] Every number in the summary is traceable to a span in the trace; nothing is estimated

**Verification:**
- [ ] Tests: summary JSON validates against its schema; totals equal the sum of per-case figures
- [ ] Manual check: the Markdown summary reads correctly pasted into a PR comment

**Dependencies:** Task 15

**Files likely touched:** `src/tripwire/report/summary.py`, `src/tripwire/cli.py`,
`tests/unit/test_summary.py`

**Estimated scope:** S

---

## Task 17: Baselines and the regression gate [ci]

**Description:** Compare a run summary to a committed baseline and decide whether the build fails.

**Acceptance criteria:**
- [ ] `baselines/main.json` committed; `uv run tripwire gate --run latest --baseline
      baselines/main.json` exits non-zero on: any required assertion failure, a routing accuracy
      drop beyond tolerance, a step or cost regression beyond threshold, or a judge regression on
      a dimension whose holdout kappa is at or above 0.6
- [ ] Judge dimensions below the kappa threshold are reported but never block
- [ ] Gate output names every violated threshold with baseline, current, and delta
- [ ] Updating a baseline is an explicit command, never automatic

**Verification:**
- [ ] Tests: synthetic summaries that trip each threshold individually; a clean summary passes
- [ ] Manual check: gate output is readable enough to act on without opening the JSON

**Dependencies:** Task 16

**Files likely touched:** `src/tripwire/report/gate.py`, `baselines/main.json`,
`tests/unit/test_gate.py`

**Estimated scope:** M

---

## Task 18: GitHub Actions workflow [ci]

**Description:** Run lint, types, unit tests, the replay regression suite, and the gate on every
pull request, uploading traces and posting the summary.

**Acceptance criteria:**
- [ ] `.github/workflows/eval.yml` runs on pull request and on main, with `uv sync`, ruff, mypy,
      `pytest -q`, `tripwire run --mode replay`, and `tripwire gate`; `TRIPWIRE_LLM_MODE=replay`
      and no API key present
- [ ] `runs/` artifacts uploaded (trace JSONL, HTML, summary) on both pass and fail
- [ ] The Markdown summary is posted as a single PR comment, updated in place on re-runs

**Verification:**
- [ ] A pull request shows a green run with the summary comment and downloadable artifacts
- [ ] Manual check: the workflow fails if an API key is somehow required (assert the env is unset)

**Dependencies:** Task 17

**Files likely touched:** `.github/workflows/eval.yml`

**Estimated scope:** S

---

## Task 19: Broken-prompt demo [ci]

**Description:** Prove the headline claim: a prompt change that breaks routing fails the build.

**Acceptance criteria:**
- [ ] A committed prompt variant (behind `--prompt-variant`) that mis-routes a known intent, with
      cassettes recorded for it
- [ ] A test asserts that the gate fails on that variant and names the affected cases
- [ ] `docs/demo.md` shows the failing gate output and the trace screenshot path

**Verification:**
- [ ] `uv run pytest -q -k broken_prompt` green (it asserts failure of the gate, not of the suite)
- [ ] Manual check: a scratch PR using the variant shows a red CI run with the diagnostic

**Dependencies:** Task 18

**Files likely touched:** `agents/inbox_triage/prompt_variants/`, `tests/regression/test_broken_prompt.py`,
`docs/demo.md`

**Estimated scope:** S

---

## Task 20: README and docs with measured results [report]

**Description:** Write the project documentation, including the results table, the calibration
write-up, and the known gaps — every number sourced from a committed run summary.

**Acceptance criteria:**
- [ ] README covers what TripWire is, how to run it, the architecture, and a results table
      (routing accuracy, per-intent accuracy, judge scores with holdout agreement, cost per case,
      p95 step latency)
- [ ] `docs/` holds the calibration write-up, known gaps, and an architecture note explaining the
      record/replay design and the harness/agent boundary
- [ ] Every figure in the README is traceable to a file in `runs/` or `baselines/`

**Verification:**
- [ ] Manual check: follow the README from a clean clone and reproduce the replay run
- [ ] Manual check: no number in the README lacks a source

**Dependencies:** Task 19

**Files likely touched:** `README.md`, `docs/architecture.md`, `docs/judge-calibration.md`,
`docs/known-gaps.md`

**Estimated scope:** S

---

### Checkpoint: Complete
- [ ] A prompt-change PR fails CI on the routing regression with an actionable diagnostic
- [ ] The failing run's HTML trace downloads from CI artifacts and opens offline
- [ ] Every README number traces to a committed run summary
- [ ] All `SPEC.md` success criteria met
- [ ] Reviewed with the human
