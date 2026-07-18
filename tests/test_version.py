"""Version-consistency guard for the Python surface.

The package version lives in two places — ``pyproject.toml`` (what PyPI publishes) and
``pullboard.__version__`` (what importers read). They must never disagree, so a stale bump in
one and not the other turns the suite red. The single canonical value across ALL Pullboard
surfaces is tracked in pullboard-node/VERSION_MANIFEST.json and enforced by
pullboard-node/scripts/check-surface-versions.mjs before a release.
"""

import re
import unittest
from pathlib import Path

import pullboard

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    assert match, "no version key found in pyproject.toml"
    return match.group(1)


class VersionConsistencyTests(unittest.TestCase):
    def test_dunder_matches_pyproject(self):
        self.assertEqual(pullboard.__version__, _pyproject_version())


if __name__ == "__main__":
    unittest.main()
