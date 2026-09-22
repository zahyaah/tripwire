"""Write/read the corpus to and from `data/corpus/` (SPEC-synthetic-data.md § Corpus).

Kept separate from `generator.py`: generation is pure (seed in, `Corpus` out), I/O is the only
part of this module that touches the filesystem — the split is what makes generation itself
trivially testable without a temp directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tripwire.data.models import Corpus, CorpusManifest, Customer, Order, Thread

_INDENT = 2


def write_corpus(corpus: Corpus, out_dir: Path) -> None:
    """Write `threads.json`, `customers.json`, `orders.json`, `manifest.json`.

    Every file is written with sorted keys and a fixed indent, so two generations of the same
    seed produce byte-identical files — the check the manifest's `content_hash` exists for is
    then also visible as a plain `git diff` (or lack of one).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "threads.json", [t.model_dump(mode="json") for t in corpus.threads])
    _write_json(
        out_dir / "customers.json", [c.model_dump(mode="json") for c in corpus.customers]
    )
    _write_json(out_dir / "orders.json", [o.model_dump(mode="json") for o in corpus.orders])
    _write_json(out_dir / "manifest.json", corpus.manifest.model_dump(mode="json"))


def load_corpus(in_dir: Path) -> Corpus:
    """Read a corpus back from disk. Raises `FileNotFoundError` naming the missing file rather
    than a generic one, so a caller who forgot to run `gen-corpus` gets an actionable message."""
    manifest_path = in_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{manifest_path} not found — run `uv run tripwire gen-corpus` first"
        )
    manifest = CorpusManifest.model_validate(_read_json(manifest_path))
    threads = tuple(Thread.model_validate(t) for t in _read_json(in_dir / "threads.json"))
    customers = tuple(Customer.model_validate(c) for c in _read_json(in_dir / "customers.json"))
    orders = tuple(Order.model_validate(o) for o in _read_json(in_dir / "orders.json"))
    return Corpus(manifest=manifest, threads=threads, customers=customers, orders=orders)


def _write_json(path: Path, obj: object) -> None:
    path.write_text(
        json.dumps(obj, sort_keys=True, indent=_INDENT, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
