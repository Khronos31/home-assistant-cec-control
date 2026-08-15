# Release contract

## Objective

Publish the Home Assistant integration and the CEC Control daemon from one
repository under exactly one stable semantic version and one `vX.Y.Z` Git
tag/GitHub Release.

The version is not bookkeeping. HACS installs the integration by tag and reads
its version out of `manifest.json`, and the daemon announces its own version
from `GET /health` so the integration can tell when the two halves have drifted
apart. Both of those only mean anything while every representation agrees.

## Acceptance criteria

1. `python scripts/version.py check` exits zero only when `VERSION`, the Home
   Assistant manifest, the README status line and the daemon's `VERSION`
   constant all contain the same stable version.
2. `python scripts/version.py sync X.Y.Z` updates every representation above,
   including both products even if only one product changed.
3. `./scripts/validate.sh` runs the version check, the test suite, a
   byte-compile of both products and the linter, and exits zero.
4. The Validate workflow additionally runs Home Assistant's `hassfest` and the
   HACS action, so the integration's declarations are checked by their owners
   rather than only by this repository.
5. The test suite fails if the daemon serves an endpoint the contract does not
   document, or documents one it does not serve, and if any relative link in
   the documentation does not resolve.
6. The Release workflow refuses to run from anywhere but `main`, refuses if
   `origin/main` moved after dispatch, synchronizes all version files, refuses
   if synchronization touched a file outside the versioned set, validates,
   then atomically publishes the version commit and annotated `vX.Y.Z` tag,
   then publishes and verifies one non-draft, non-prerelease GitHub Release.
7. Dispatching `0.1.0` from the reviewed `main` commit produces tag `v0.1.0`
   and a matching GitHub Release.

## Non-goals

- Separate tags, repositories or release trains for the integration and the
  daemon.
- Pre-release versions, or moving or replacing a published stable tag.
- Publishing the daemon to PyPI, or shipping any built artefact as a Release
  asset. Both products are source.
- Testing against CEC hardware in CI. There is none, and pretending otherwise
  would make the suite lie. What was measured against real hardware is written
  down in [`cec-findings.md`](cec-findings.md) with dates instead.

## Constraints

- The public tag is immutable. A bad publication is corrected with a newer
  patch release, never by moving the tag.
- `scripts/version.py` is the only thing that writes a version anywhere. A
  version literal added by hand elsewhere will not be synchronized, and
  criterion 1 is what catches it.
- The test suite pins `homeassistant==2026.2.3`, the newest release that still
  installs on Python 3.13. This is a property of the machine this repository is
  developed on, not a statement about what the integration supports; see
  [`cec-findings.md`](cec-findings.md).

## Rollback

Before publication, revert the release commit locally — nothing has left the
machine. After a public tag or Release, keep the tag immutable and publish a
corrected patch release whose version is synchronized across both products.

## Maintenance

This repository is not actively used by its author, and the README says so.
That does not weaken this contract: a release that happens rarely is exactly
the one where nobody remembers the manual steps, which is why they are all in
the workflow.
