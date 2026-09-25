#!/usr/bin/env python3
"""Record cassettes for every golden case, with rate-limit-aware retries and resume support.

Gemini free tier (live-verified 2026-09-24): 20 requests/minute, per-model. Each golden
case needs 3-6 model calls. This script records one case at a time, spaces calls to stay
under the limit, retries transient 429s, and skips cases that already replay cleanly so it
can be re-run to resume an interrupted recording.

Usage:
    uv run python scripts/record_all_cassettes.py
    # key is loaded from .env automatically
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

# Add repo root to sys.path so we can import agents.*
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from openai import OpenAI  # noqa: E402

from tripwire.assertions import run_case  # noqa: E402
from tripwire.core.golden import load_golden_set  # noqa: E402
from tripwire.data import load_corpus  # noqa: E402

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CASSETTES_DIR = REPO_ROOT / "fixtures" / "cassettes"
GOLDEN_DIR = REPO_ROOT / "data" / "golden"
CORPUS_DIR = REPO_ROOT / "data" / "corpus"
RUNS_DIR = REPO_ROOT / "runs"
MODEL = "gemini-3.1-flash-lite"

# Inter-call delay for safety
INTER_CALL_DELAY_S = 3
# A retry-after this large means a daily (not per-minute) quota; abort rather than nap for hours.
DAILY_QUOTA_THRESHOLD_S = 600.0


def _load_dotenv() -> None:
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k not in os.environ:
                os.environ[k] = v


def _patch_gateway_with_rate_limiting() -> None:
    """Monkey-patch ModelGateway._call_api to space calls and retry transient 429s/503s."""
    from tripwire.llm.gateway import ModelGateway

    original_call_api = ModelGateway._call_api

    def _rate_limited_call_api(self, request):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                result = original_call_api(self, request)
                print(f"    API call ok, sleeping {INTER_CALL_DELAY_S}s...")
                time.sleep(INTER_CALL_DELAY_S)
                return result
            except Exception as exc:
                exc_str = str(exc)
                is_rate_limit = (
                    "429" in exc_str
                    or "RESOURCE_EXHAUSTED" in exc_str
                    or "rate" in exc_str.lower()
                )
                is_server_error = (
                    "503" in exc_str
                    or "UNAVAILABLE" in exc_str
                    or "high demand" in exc_str.lower()
                )
                if not (is_rate_limit or is_server_error):
                    raise
                if is_server_error:
                    delay = 10.0 * (attempt + 1)
                    print(f"    server unavailable 503 (attempt {attempt + 1}/{max_retries}), "
                          f"waiting {delay:.0f}s...")
                    time.sleep(delay)
                    continue
                match = re.search(r"retry in ([\d.]+)s", exc_str)
                delay = float(match.group(1)) + 5 if match else 65.0
                if delay > DAILY_QUOTA_THRESHOLD_S:
                    raise RuntimeError(
                        f"retry-after {delay:.0f}s looks like a daily quota, not per-minute; "
                        "abort and resume later. Cassettes recorded so far are already on disk."
                    ) from exc
                print(f"    rate limited (attempt {attempt + 1}/{max_retries}), "
                      f"waiting {delay:.0f}s...")
                time.sleep(delay)
        return original_call_api(self, request)

    ModelGateway._call_api = _rate_limited_call_api


def _replays_cleanly(case, corpus) -> bool:
    """True if the case replays without a cassette miss (all its cassettes are present)."""
    try:
        result = run_case(
            case,
            corpus=corpus,
            mode="replay",
            model=MODEL,
            cassettes_dir=CASSETTES_DIR,
            runs_dir=RUNS_DIR,
        )
    except Exception:
        return False
    return not (
        result.loop_outcome == "error"
        and result.loop_error
        and "CassetteMissError" in result.loop_error
    )


def main() -> None:
    _load_dotenv()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set (check .env or environment)")
        sys.exit(1)

    _patch_gateway_with_rate_limiting()

    client = OpenAI(
        api_key=api_key,
        base_url=GEMINI_BASE_URL,
        timeout=30.0,
        max_retries=2,
    )
    corpus = load_corpus(CORPUS_DIR)
    cases = load_golden_set(GOLDEN_DIR)

    print(f"Recording cassettes for {len(cases)} golden cases...")
    print(f"Model: {MODEL}")
    print(f"Cassettes dir: {CASSETTES_DIR}")
    print(f"Inter-call delay: {INTER_CALL_DELAY_S}s")
    print()

    recorded = []
    skipped = []
    failed = []
    errors = []

    for i, case in enumerate(cases, 1):
        if _replays_cleanly(case, corpus):
            print(f"[{i}/{len(cases)}] SKIP {case.case_id} (already replays cleanly)")
            skipped.append(case.case_id)
            continue

        print(f"[{i}/{len(cases)}] Recording {case.case_id}...")
        start = time.monotonic()
        try:
            result = run_case(
                case,
                corpus=corpus,
                mode="record",
                model=MODEL,
                cassettes_dir=CASSETTES_DIR,
                runs_dir=RUNS_DIR,
                client=client,
            )
            elapsed = time.monotonic() - start
            status = "PASS" if result.passed else "FAIL"
            print(f"  {status}: outcome={result.loop_outcome}, passed={result.passed}, "
                  f"cost={result.total_micro_dollars}u$, time={elapsed:.1f}s")
            if result.loop_error:
                print(f"  loop_error: {result.loop_error[:200]}")
            recorded.append(case.case_id)
            if not result.passed:
                failed.append((case.case_id, result.loop_outcome, result.loop_error))
        except Exception as exc:
            elapsed = time.monotonic() - start
            print(f"  EXCEPTION after {elapsed:.1f}s: {exc}")
            errors.append((case.case_id, str(exc)[:200]))

    print("\n" + "=" * 70)
    print(f"DONE: {len(recorded)} recorded, {len(skipped)} skipped, "
          f"{len(failed)} failed assertions, {len(errors)} exceptions")
    print(f"Cassettes on disk: {len(list(CASSETTES_DIR.glob('*.json')))} files")

    if failed:
        print("\nRecorded but assertions failed (cassettes still saved):")
        for case_id, outcome, error in failed:
            print(f"  {case_id}: outcome={outcome}" + (f" — {error[:150]}" if error else ""))

    if errors:
        print("\nException cases (re-run this script to resume):")
        for case_id, msg in errors:
            print(f"  {case_id}: {msg}")


if __name__ == "__main__":
    main()
