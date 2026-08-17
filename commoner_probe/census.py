# SPDX-License-Identifier: MIT
"""Deprecated name for :mod:`commoner_probe.ogd_resource_api`.

The module moved because its old name stated the scheme whose data comes out
rather than the mechanism a caller must implement. Nothing else changed: this
re-exports the same module, so an existing import keeps working.

Removal needs a major version and a migration note, not a quiet deletion:
sibling repos import this package by module path.
"""

from __future__ import annotations

import sys as _sys
import warnings as _warnings

from . import ogd_resource_api as _target

_warnings.warn(
    "commoner_probe.census is deprecated; import commoner_probe.ogd_resource_api instead. "
    "The new name states the mechanism rather than the scheme.",
    DeprecationWarning,
    stacklevel=2,
)

# Bind the target module itself, so `from commoner_probe.census import X`,
# `commoner_probe.census.X` and `monkeypatch.setattr("commoner_probe.census.X", ...)`
# all reach the SAME objects the new module holds. Re-exporting a copy would
# give a patcher one object and the running code another.
_sys.modules[__name__] = _target
