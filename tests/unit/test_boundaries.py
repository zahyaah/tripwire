"""Import-boundary enforcement: the agent under test must never import the modules that score
it (SPEC.md § Boundaries: "the thing being measured must not see the measuring instrument").

Walks the actual AST of every file under `agents/` rather than grepping for the string
`tripwire.assertions` — that also catches `import tripwire.assertions as x`,
`from tripwire import assertions`, and similar forms a plain substring search could miss on
awkward formatting, without being fooled by the phrase appearing only in a comment or docstring.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_MODULES = ("tripwire.assertions", "tripwire.judge")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _REPO_ROOT / "agents"
_ASSERTIONS_DIR = _REPO_ROOT / "src" / "tripwire" / "assertions"


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            # `from tripwire import assertions` — module is "tripwire", the forbidden name is
            # one of the imported names.
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _violates_boundary(imported: set[str]) -> list[str]:
    return [
        forbidden
        for forbidden in FORBIDDEN_MODULES
        for name in imported
        if name == forbidden or name.startswith(f"{forbidden}.")
    ]


def test_agents_directory_exists_and_is_non_empty() -> None:
    # A trivially-passing boundary test (nothing imports anything because there's nothing to
    # scan) would be worse than no test at all — assert there's real content to check.
    py_files = list(_AGENTS_DIR.rglob("*.py"))
    assert py_files, f"expected .py files under {_AGENTS_DIR}"


def test_no_file_under_agents_imports_assertions_or_judge() -> None:
    violations: dict[str, list[str]] = {}
    for path in _AGENTS_DIR.rglob("*.py"):
        imported = _imported_module_names(path.read_text(encoding="utf-8"))
        hits = _violates_boundary(imported)
        if hits:
            violations[str(path.relative_to(_REPO_ROOT))] = hits
    assert not violations, f"agents/ imports scoring modules: {violations}"


def test_no_file_under_assertions_imports_agents() -> None:
    # The reverse direction: CAPABILITY-MAP.md is explicit that `assertions` evaluates recorded
    # spans, never a live agent object — it must not import `agents` at all, symmetric to the
    # check above.
    violations: dict[str, list[str]] = {}
    for path in _ASSERTIONS_DIR.rglob("*.py"):
        imported = _imported_module_names(path.read_text(encoding="utf-8"))
        hits = [name for name in imported if name == "agents" or name.startswith("agents.")]
        if hits:
            violations[str(path.relative_to(_REPO_ROOT))] = hits
    assert not violations, f"assertions/ imports agents/: {violations}"


def test_the_boundary_check_itself_actually_catches_a_violation() -> None:
    # Guards against the checker silently doing nothing (e.g. a typo in FORBIDDEN_MODULES, or
    # _imported_module_names returning an empty set for every valid form of import statement).
    samples = [
        "import tripwire.assertions",
        "import tripwire.assertions as a",
        "from tripwire.assertions import matchers",
        "from tripwire import assertions",
        "from tripwire.judge.stats import kappa",
    ]
    for source in samples:
        imported = _imported_module_names(source)
        assert _violates_boundary(imported), f"checker missed: {source!r}"
