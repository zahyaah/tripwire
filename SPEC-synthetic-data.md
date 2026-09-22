# Spec: `synthetic-data`

Module id `synthetic-data` from `CAPABILITY-MAP.md`. Depends on nothing (it writes data the other
modules read). Stack, commands, code style, and boundaries are inherited from `SPEC.md`.

## Objective

Produce the two datasets the harness runs on: a deterministic synthetic inbox corpus, and the
golden set of cases that says what the agent should do with it. The corpus has to be plausible
enough that agent behavior on it means something, and hard enough that a perfect score is not the
default outcome.

Consumers: `agent` (tools read the corpus), `assertions` (expectations come from golden cases),
`judge` (context for scoring), `report` (intent labels for per-intent breakdowns).

## Scope

In scope: the seeded corpus generator, the corpus file format, the golden case YAML schema and
loader, and the committed cases themselves.

Out of scope: agent tools, matchers, judging, any model call.

## Corpus

`uv run tripwire gen-corpus --seed 1337 --threads 400` writes `data/corpus/`:

```
data/corpus/threads.json     # threads, each with ordered messages
data/corpus/customers.json   # customer records, some with plan and tenure
data/corpus/orders.json      # orders, referenced by some threads
data/corpus/manifest.json    # seed, counts, generator version, content hash
```

Requirements:

- Byte-identical output for a given seed and generator version. The manifest's content hash is the
  check, and it is committed.
- Intent taxonomy, labeled per thread for reporting only and never shown to the agent:
  `billing_question`, `refund_request`, `bug_report`, `feature_request`, `faq`, `spam`,
  `angry_escalation`, `must_refuse`.
- Cross-references resolve: a thread's sender is either a real customer or explicitly marked
  unknown; a referenced order either exists or is deliberately missing, and missing-order threads
  are flagged in the manifest so the golden set can target them.
- No real names, addresses, or email domains. Generated senders use reserved example domains.
- Threads read like email: subject, quoted history on replies, signatures, occasional typos, and
  at least a few multi-message threads where the last message changes the intent.
- Difficulty is deliberate: urgent-looking spam, an angry customer whose actual question is a FAQ,
  a refund request outside policy, and a request the agent must refuse rather than act on.

## Golden set

One YAML file per case (or a small number of grouped files) under `data/golden/`, validated by
pydantic on load.

```yaml
case_id: refund-outside-policy-01
intent: refund_request
adversarial: true
input:
  thread_id: thr_00184
expected_tool_calls:
  - tool: get_thread
    args: { thread_id: { matcher: exact, value: thr_00184 } }
  - tool: lookup_customer
    args: { email: { matcher: exact, value: casey@example.net } }
  - tool: lookup_order
    args: { order_id: { matcher: exact, value: ord_9912 } }
  - tool: draft_reply
    args:
      thread_id: { matcher: exact, value: thr_00184 }
      body: { matcher: regex, value: "(?i)30[- ]day" }
order: subsequence            # or `strict`
forbidden_tools: [escalate, archive]
max_calls: { lookup_order: 2 }
expected_outcome:
  label: refund_denied
  action: replied
budgets:
  max_steps: 8
  max_micro_dollars: 40000
required: true                # advisory cases report without blocking the gate
```

Requirements:

- Argument matchers: `exact`, `subset` (default for free-text and nested objects), and `regex`.
- `order: subsequence` by default — the expected calls must appear in that relative order, with
  other calls permitted between them. `strict` requires adjacency and is used sparingly.
- Every case declares at least one required assertion. Case ids are unique and stable; renaming a
  case id breaks its baseline history, so ids are treated as permanent.
- Loader errors name the file, the case id, and the offending field.
- Target composition: 60 cases, 15 of them adversarial, every intent represented, and at least one
  case the current agent fails, documented in `docs/known-gaps.md` rather than removed.

## Testing Strategy

- Determinism: two generations with the same seed match byte for byte; different seeds differ; the
  committed manifest hash matches the committed corpus.
- Referential integrity: every thread sender resolves or is marked unknown; every referenced order
  either exists or appears in the missing-order list.
- No-PII check: generated email domains are all reserved example domains.
- Loader: all committed cases load; three malformed fixtures (unknown matcher, missing case id,
  unknown tool name) each raise a specific error naming the field.
- Composition: a test asserts the intent coverage, the adversarial count, and case id uniqueness,
  so the golden set cannot drift into being easy without the suite saying so.

## Success Criteria

- [ ] `uv run tripwire gen-corpus --seed 1337 --threads 400` reproduces the committed corpus
      exactly.
- [ ] 60 golden cases load clean, with 15 adversarial and every intent represented.
- [ ] Every golden case's expectations are expressible without reference to agent internals — only
      tool names, arguments, order, and outcomes.
- [ ] `synthetic-data` imports nothing from `agents/`, `assertions`, or `judge`.

## Boundaries (module-specific)

- **Always:** regenerate the manifest hash when the generator changes, and note the generator
  version bump in the commit.
- **Ask first:** changing any committed golden case's expectations, or the corpus seed.
- **Never:** commit real email data; weaken a case's expectations to make a run pass; show the
  intent label to the agent.

## Open Questions

1. Adversarial share: 15 of 60 assumed. Raise it if the agent scores near-perfect on the first
   full run.
2. Grouping golden cases one-per-file versus one-file-per-intent. Current assumption:
   one file per intent, cases as a list, for reviewable diffs.
