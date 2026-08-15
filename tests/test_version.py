"""Tests for the one-version-per-repository rule.

The integration compares its own version with the daemon's `/health`, so the
two being equal is a property the code relies on rather than a tidiness habit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.version import (
    VersionError,
    check_versions,
    parse_version,
    require_newer,
    sync_version,
)

ROOT = Path(__file__).resolve().parents[1]


def test_the_repository_is_consistent_right_now() -> None:
    """Every version representation in this checkout agrees."""
    expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert check_versions(ROOT) == expected


@pytest.mark.parametrize("bad", ["v1.0.0", "1.0", "1.0.0-rc1", "01.0.0", ""])
def test_only_stable_semver_is_accepted(bad: str) -> None:
    """Pre-releases and v-prefixes are out of scope for this repository."""
    with pytest.raises(VersionError):
        parse_version(bad)


def test_require_newer_rejects_going_backwards() -> None:
    """A published tag is immutable, so a release must move forward."""
    require_newer("0.2.0", "0.1.0")
    with pytest.raises(VersionError):
        require_newer("0.1.0", "0.1.0")
    with pytest.raises(VersionError):
        require_newer("0.0.9", "0.1.0")


def _copy_repo(tmp_path: Path) -> Path:
    """Return a throwaway copy of the version-bearing files."""
    root = tmp_path / "repo"
    (root / "custom_components/cec_control").mkdir(parents=True)
    (root / "daemon").mkdir(parents=True)
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (root / "custom_components/cec_control/manifest.json").write_text(
        json.dumps({"domain": "cec_control", "version": "0.1.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# CEC Control\n\nCurrent version: **0.1.0**.\n", encoding="utf-8"
    )
    (root / "daemon/cec_daemon.py").write_text(
        'VERSION = "0.1.0"\n', encoding="utf-8"
    )
    return root


def test_sync_moves_every_representation_together(tmp_path: Path) -> None:
    """Bumping one product bumps the other; that is the whole point."""
    root = _copy_repo(tmp_path)
    sync_version(root, "0.2.0")
    assert check_versions(root) == "0.2.0"
    assert '"version": "0.2.0"' in (
        root / "custom_components/cec_control/manifest.json"
    ).read_text(encoding="utf-8")
    assert 'VERSION = "0.2.0"' in (root / "daemon/cec_daemon.py").read_text(
        encoding="utf-8"
    )
    assert "Current version: **0.2.0**." in (root / "README.md").read_text(
        encoding="utf-8"
    )


def test_check_catches_a_half_finished_bump(tmp_path: Path) -> None:
    """The failure this script exists to prevent: one product left behind."""
    root = _copy_repo(tmp_path)
    (root / "daemon/cec_daemon.py").write_text('VERSION = "0.1.1"\n', encoding="utf-8")
    with pytest.raises(VersionError, match="mismatch"):
        check_versions(root)
