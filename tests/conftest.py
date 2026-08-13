"""One session-wide property: running the suite must not change the tree.

Each individual assertion passed while `pytest` rewrote the committed
dist/rates.json, so nothing went red — every test was green while the suite
quietly modified the artefact it exists to check. A per-test
assertion cannot state this — the property is about the suite as a whole — so it
lives in a session fixture and is checked at teardown.

It compares two snapshots rather than demanding a clean tree, so an edit made
before the run is not reported as damage the suite did.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest
from validate import REPO_ROOT


def _tree_state() -> str | None:
    """`git status --porcelain`, or None where git cannot answer."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


@pytest.fixture(scope="session", autouse=True)
def the_suite_leaves_the_tree_as_it_found_it() -> Iterator[None]:
    before = _tree_state()
    yield
    after = _tree_state()
    if before is None or after is None:
        return  # not a git checkout, or no git — nothing to compare against
    assert after == before, (
        "the test suite changed the working tree.\n"
        f"before:\n{before or '(clean)'}\n"
        f"after:\n{after or '(clean)'}"
    )
