"""Every old module path still imports, and reaches the same objects.

The modules were renamed so their names state the mechanism a caller must
implement rather than the scheme whose data comes out. Sibling repos import
this package by module path, so each old name stays as a shim.

This file is the contract. Deleting a shim fails here, which is the point: it
must be a deliberate major-version act with a migration note, not a quiet
removal that surfaces as a crawl failing in another repo.
"""

from __future__ import annotations

import importlib
import warnings

import pytest

#: old path -> new path. Kept explicit rather than derived, so a typo in the
#: package is a failure here rather than a test that agrees with itself.
ALIASES = {
    "abhilekh_patal": "catalogue_search_api",
    "attendance": "attendance_json_api",
    "bills": "bill_catalog_api",
    "cag": "audit_pdf_index",
    "census": "ogd_resource_api",
    "dchb_town": "ogd_town_release",
    "committees": "committee_report_api",
    "courts": "case_law_api",
    "ddg": "ministry_pdf_index",
    "debates": "verbatim_pdf_api",
    "doe": "pay_report_index",
    "indiacode": "statute_dspace",
    "mospi": "rest_dataset_api",
    "myneta": "affidavit_pages",
    "neva": "assembly_portal",
    "neva_portals": "assembly_portal_registry",
    "neva_text": "two_column_pdf_qa",
    "niti": "annual_report_index",
    "prs": "drupal_publication_index",
    "questions_list": "question_list_api",
    "sansad": "parliament_qa_api",
}


@pytest.mark.parametrize("old,new", sorted(ALIASES.items()))
def test_the_old_path_is_the_new_module_itself(old, new):
    """Not a copy. A test that patches `commoner_probe.<old>.X` must reach the
    object the running code uses, or the patch silently does nothing."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert importlib.import_module(f"commoner_probe.{old}") is \
            importlib.import_module(f"commoner_probe.{new}")


@pytest.mark.parametrize("old", sorted(ALIASES))
def test_the_old_path_says_it_is_deprecated(old):
    """Re-import the shim to see its warning, then put `sys.modules` back.

    Dropping the TARGET and re-importing would leave a second copy of it in the
    interpreter. A later test patching `commoner_probe.<new>.f` would then patch
    one copy while the code under test used the other, and the patch would
    silently do nothing. That happened here: six unrelated tests failed in the
    full run and passed alone.
    """
    import sys

    saved = {name: sys.modules[name]
             for name in (f"commoner_probe.{old}", f"commoner_probe.{ALIASES[old]}")
             if name in sys.modules}
    sys.modules.pop(f"commoner_probe.{old}", None)
    try:
        with pytest.warns(DeprecationWarning, match=ALIASES[old]):
            importlib.import_module(f"commoner_probe.{old}")
    finally:
        sys.modules.update(saved)


def test_the_consumers_named_imports_all_resolve():
    """The exact module paths five sibling repos import today, verified against
    their working trees before the rename. Each must keep working untouched."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for path in ("commoner_probe.answers", "commoner_probe.bills",
                     "commoner_probe.committees", "commoner_probe.debates",
                     "commoner_probe.members", "commoner_probe.base",
                     "commoner_probe.http_client", "commoner_probe.sansad",
                     "commoner_probe.topics"):
            assert importlib.import_module(path) is not None


def test_no_alias_shadows_a_real_module():
    """A shim whose target does not exist would import as itself forever."""
    from pathlib import Path

    import commoner_probe

    package = Path(commoner_probe.__file__).parent
    for old, new in ALIASES.items():
        assert (package / f"{new}.py").exists(), f"{old} points at a missing {new}"
