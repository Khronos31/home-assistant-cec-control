#!/usr/bin/env python3
"""Synchronize and validate repository release versions.

One repository, one stable semantic version, one tag — covering both products:
the Home Assistant integration and the daemon. The daemon reports its version
over ``GET /health``, and the integration can compare it with its own, so the
two halves being out of step is a detectable condition rather than a puzzling
bug. That only holds if this script keeps them equal.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
README_VERSION_PATTERN = re.compile(
    r"^Current version: \*\*(?P<version>[^*]+)\*\*\.$", re.MULTILINE
)
DAEMON_VERSION_PATTERN = re.compile(
    r'^VERSION = "(?P<version>[^"]+)"$', re.MULTILINE
)
README_UNRELEASED = (
    "This repository is under active development and has no released version yet."
)


class VersionError(ValueError):
    """Raised when repository release versions are invalid or inconsistent."""


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse one stable semantic version without a v prefix."""
    match = SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise VersionError(
            f"Invalid stable version {value!r}; expected X.Y.Z without a v prefix"
        )
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _paths(root: Path) -> tuple[Path, Path, Path, Path]:
    return (
        root / "VERSION",
        root / "custom_components/cec_control/manifest.json",
        root / "README.md",
        root / "daemon/cec_daemon.py",
    )


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise VersionError(f"Unable to read {path}: {err}") from err
    if not isinstance(manifest, dict) or not isinstance(manifest.get("version"), str):
        raise VersionError(f"{path} must contain a string version field")
    return manifest


def _single(pattern: re.Pattern[str], text: str, label: str) -> str:
    """Return the only version captured by one required representation."""
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise VersionError(f"{label} must contain exactly one version")
    return matches[0].group("version")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise VersionError(f"Unable to read {path}: {err}") from err


def sync_version(root: Path, version: str) -> None:
    """Set every repository version field to one validated stable version."""
    parse_version(version)
    version_path, manifest_path, readme_path, daemon_path = _paths(root)
    manifest = _load_manifest(manifest_path)
    readme = _read(readme_path)
    daemon = _read(daemon_path)

    release_line = f"Current version: **{version}**."
    matches = list(README_VERSION_PATTERN.finditer(readme))
    unreleased_count = readme.count(README_UNRELEASED)
    if len(matches) == 1 and unreleased_count == 0:
        updated_readme = README_VERSION_PATTERN.sub(release_line, readme, count=1)
    elif not matches and unreleased_count == 1:
        updated_readme = readme.replace(README_UNRELEASED, release_line, 1)
    else:
        raise VersionError(
            "README.md must contain exactly one current-version line or the "
            "single unreleased bootstrap sentence"
        )

    _single(DAEMON_VERSION_PATTERN, daemon, "daemon VERSION")
    updated_daemon = DAEMON_VERSION_PATTERN.sub(
        f'VERSION = "{version}"', daemon, count=1
    )

    manifest["version"] = version
    version_path.write_text(f"{version}\n", encoding="utf-8")
    manifest_path.write_text(
        f"{json.dumps(manifest, indent=2, ensure_ascii=False)}\n", encoding="utf-8"
    )
    readme_path.write_text(updated_readme, encoding="utf-8")
    daemon_path.write_text(updated_daemon, encoding="utf-8")


def check_versions(root: Path) -> str:
    """Return the canonical version after verifying every representation."""
    version_path, manifest_path, readme_path, daemon_path = _paths(root)
    version = _read(version_path).strip()
    parse_version(version)

    versions = {
        "VERSION": version,
        "manifest.json": _load_manifest(manifest_path)["version"],
        "README.md": _single(README_VERSION_PATTERN, _read(readme_path), "README.md"),
        "daemon": _single(DAEMON_VERSION_PATTERN, _read(daemon_path), "daemon VERSION"),
    }
    if any(candidate != version for candidate in versions.values()):
        details = ", ".join(f"{name}={value}" for name, value in versions.items())
        raise VersionError(f"Version mismatch: {details}")
    return version


def require_newer(candidate: str, current: str) -> None:
    """Require candidate to be newer than the current stable release."""
    if parse_version(candidate) <= parse_version(current):
        raise VersionError(f"{candidate} must be newer than {current}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync", help="synchronize all version files")
    sync_parser.add_argument("version")
    subparsers.add_parser("check", help="verify all version files agree")
    newer_parser = subparsers.add_parser(
        "newer", help="verify a candidate is newer than a current version"
    )
    newer_parser.add_argument("candidate")
    newer_parser.add_argument("current")
    return parser


def main() -> int:
    """Run the version command-line interface."""
    args = _parser().parse_args()
    try:
        if args.command == "sync":
            sync_version(args.root, args.version)
        elif args.command == "check":
            print(check_versions(args.root))
        else:
            require_newer(args.candidate, args.current)
    except VersionError as err:
        print(f"version error: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
