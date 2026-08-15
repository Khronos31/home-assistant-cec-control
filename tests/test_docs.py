"""Tests that keep the documentation honest.

This repository exists because its predecessor's README described endpoints the
code did not have — `GET /power_on` and `GET /standby`, neither of which
existed. Nobody noticed for a year, because nothing checked. These tests check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from daemon.cec_daemon import CecAdapter, build_app

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = sorted([*ROOT.glob("*.md"), *ROOT.glob("docs/*.md")])

# Links written as [text](target). Anchors and external URLs are left alone.
LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")

# Endpoint headings in the daemon contract, e.g. "### `POST /transmit`".
CONTRACT_ENDPOINT = re.compile(r"^#+\s+`(GET|POST)\s+(/[a-z_]*)`", re.MULTILINE)


def _routes() -> set[tuple[str, str]]:
    """Return the (method, path) pairs the daemon actually serves."""
    app = build_app(CecAdapter())
    found = set()
    for route in app.router.routes():
        path = getattr(route.resource, "canonical", "")
        # Skip the catch-all that turns unknown paths into the contract's 404
        # shape, and HEAD, which aiohttp adds for free alongside every GET.
        if not path or "{" in path or route.method == "HEAD":
            continue
        found.add((route.method, path))
    return found


def test_the_repository_has_documentation_to_check() -> None:
    """Guard against these tests silently passing on an empty glob."""
    assert len(MARKDOWN) >= 3


@pytest.mark.parametrize("document", MARKDOWN, ids=lambda path: path.name)
def test_every_relative_link_resolves(document: Path) -> None:
    """A dead link in a repository nobody maintains stays dead."""
    # Relative links resolve against the document that contains them, not the
    # repository root — docs/ links to its own neighbours by bare filename.
    broken = [
        target
        for target in LINK.findall(document.read_text(encoding="utf-8"))
        if not (document.parent / target.split("#", 1)[0]).exists()
    ]
    assert not broken, f"{document.name} links to missing files: {broken}"


def test_the_contract_documents_every_endpoint_the_daemon_serves() -> None:
    """An endpoint nobody wrote down is an endpoint nobody can use."""
    contract = (ROOT / "docs/daemon-contract.md").read_text(encoding="utf-8")
    documented = set(CONTRACT_ENDPOINT.findall(contract))
    undocumented = _routes() - documented
    assert not undocumented, f"served but undocumented: {sorted(undocumented)}"


def test_the_daemon_serves_every_endpoint_the_contract_documents() -> None:
    """This is the direction that broke the previous repository."""
    contract = (ROOT / "docs/daemon-contract.md").read_text(encoding="utf-8")
    documented = set(CONTRACT_ENDPOINT.findall(contract))
    missing = documented - _routes()
    assert not missing, f"documented but not served: {sorted(missing)}"


def test_the_readme_points_at_the_files_it_promises() -> None:
    """The README sends readers to these two by name; they carry the findings
    that make the rest of the repository safe to touch."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("docs/cec-findings.md", "docs/daemon-contract.md"):
        assert name in readme
        assert (ROOT / name).exists()


def test_the_status_notice_survives_edits() -> None:
    """The repository is unmaintained by design; saying so is not optional."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Status" in readme
    assert "no longer use this myself" in readme
