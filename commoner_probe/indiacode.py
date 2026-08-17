# SPDX-License-Identifier: MIT
"""Deprecated name for :mod:`commoner_probe.statute_dspace`.

The module moved because its old name stated the scheme whose data comes out
rather than the mechanism a caller must implement. Nothing else changed: this
re-exports the same module, so an existing import keeps working.

Removal needs a major version and a migration note, not a quiet deletion:
sibling repos import this package by module path.
"""

from __future__ import annotations

import sys as _sys
import warnings as _warnings

from . import statute_dspace as _target

_warnings.warn(
    "commoner_probe.indiacode is deprecated; import commoner_probe.statute_dspace instead. "
    "The new name states the mechanism rather than the scheme.",
    DeprecationWarning,
    stacklevel=2,
)

# Bind the target module itself, so `from commoner_probe.indiacode import X`,
# `commoner_probe.indiacode.X` and `monkeypatch.setattr("commoner_probe.indiacode.X", ...)`
# all reach the SAME objects the new module holds. Re-exporting a copy would
# give a patcher one object and the running code another.
_sys.modules[__name__] = _target
