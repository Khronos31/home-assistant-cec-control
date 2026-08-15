"""Test configuration.

Both products live in this one repository, so the repository root goes on the
import path and the tests import them the way they are laid out on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
