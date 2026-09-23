Written for: anyone deciding whether TripWire's headline claim actually holds — "a prompt change
that breaks routing fails the build."

# Demo: a broken prompt fails the gate

## The claim

`agents/inbox_triage/prompt_variants/always_escalate.md` is a real, committed prompt variant that
changes exactly one rule from the production prompt (`agents/inbox_triage/prompt.md`): instead of
"read to the end before reacting to tone," it says "escalate immediately once the tone is clear."
This should break every `angry_escalation` golden case that forbids `escalate` and expects the
agent to notice the real, small ask at the end of the thread (`docs/known-gaps.md`).

## Status: proven at the harness level, not yet against a live model

`tests/regression/test_broken_prompt.py` proves the mechanism end to end — `run_case`,
assertions, `build_summary`, `evaluate_gate` — using a scripted, in-memory fake model client (the
same pattern `tests/unit/test_loop.py` already uses), not a real Gemini call. That's a deliberate,
honest substitution: it proves the *harness* catches the regression correctly; it does not yet
prove the *live model* actually mis-routes under `always_escalate.md` the way the variant's own
comment predicts. That confirmation needs a real `--mode record` run against the live API, which
is still blocked by the Gemini free-tier daily quota (`docs/known-gaps.md`) — until then, this
demo's numbers come from the scripted test, clearly marked as such below, not from a live run.

No trace screenshot exists — none was taken, and this file doesn't claim one exists. The trace
HTML referenced below is real and openable; regenerate it with the reproduction steps at the
bottom of this file.

## The gate output

This is the real, unedited output of `evaluate_gate` comparing the scripted broken-variant run
against the scripted default-prompt run, captured by running
`tests/regression/test_broken_prompt.py`'s own logic directly (2026-09-23):

```
gate: FAIL — 2 violation(s)

  [required_assertions] baseline=0 failing current=1 failing delta=broken-prompt-demo-escalation
    required assertion(s) failed on: broken-prompt-demo-escalation
  [routing_accuracy] baseline=100.0% current=0.0% delta=-100.0%
    routing accuracy dropped 100.0%, exceeding tolerance 5.0%
```

The gate names the affected case (`broken-prompt-demo-escalation`) and reports both the
absolute required-assertion failure and the routing-accuracy regression versus the baseline run.

## The per-assertion detail

Default prompt (`prompt.md`), scripted to reply — passes every assertion:

```
  PASS not_called:escalate
  PASS final_outcome
  PASS step_budget
  PASS cost_budget
```

`always_escalate.md` variant, scripted to escalate on tone — fails exactly the two assertions the
variant should break:

```
  FAIL not_called:escalate -- [broken-prompt-demo-escalation] step 1 not_called:escalate:
    expected no call to escalate, got called at step(s) [1]
  FAIL final_outcome -- [broken-prompt-demo-escalation] final_outcome:
    expected action='replied' label=None, got action='escalated' labels=[]
  PASS step_budget
  PASS cost_budget
```

## Reproducing this

```
uv run pytest -q -k broken_prompt
```

Runs green (it asserts the *gate* fails, not that the suite does — tasks/todo.md Task 19's own
distinction). To regenerate the trace HTML and gate text shown above from scratch, run the same
`run_case` / `evaluate_gate` calls the test makes, against a `tmp_path`, and open the resulting
`trace.html` — see the test file for the exact scripted client and golden case used; it's the
same code, not a separate fixture to keep in sync.

## What's still needed for the full claim

- Real cassettes recorded against the live model for both the default prompt and
  `always_escalate.md` on the same golden case(s) — confirming the live model actually escalates
  under the variant, not just that the harness would catch it if it did.
- A scratch PR using `--prompt-variant always_escalate` showing a genuinely red CI run
  (`.github/workflows/eval.yml`) with the diagnostic above, once cassettes exist.

Both are blocked by the same Gemini quota constraint tracked in `docs/known-gaps.md`, not by
anything left to build here.
