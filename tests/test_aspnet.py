"""Tests for commoner_probe.aspnet.

Every case is a failure that actually occurred against Bihar's Aangan ICDS MIS.
The two that matter most are the ones that fail SILENTLY:

- test_form_fields_never_sends_empty_select: posting "" for an untouched
  dropdown is rejected by EventValidation with HTTP 500 and no explanation.
- test_expect_populated_catches_the_silent_cascade_failure: batching two
  cascade levels into one POST returns HTTP 200 with a complete page whose
  child dropdown is empty. Nothing raises. The crawl yields nothing and looks
  like it worked.
"""

from __future__ import annotations

import pytest

from commoner_probe import aspnet

PAGE = """
<form method="post" action="./Report.aspx" id="aspnetForm">
<input type="hidden" name="__VIEWSTATE" value="HKly/kFS0R2II085" />
<input type="hidden" name="__VIEWSTATEGENERATOR" value="38E0505A" />
<input type="hidden" name="__EVENTVALIDATION" value="OZAA7i9vRy1XhVbR" />
<select name="ctl00$MainContent$ddlFY">
  <option value="2627">2026-2027</option><option value="1718">2017-2018</option>
</select>
<select name="ctl00$MainContent$ddlDistrict">
  <option value="0">--Select--</option><option value="209">ARARIA</option>
</select>
<select name="ctl00$MainContent$ddlProject"><option value="0">--Select--</option></select>
<input type="submit" name="ctl00$MainContent$btnSubmit" value="Get Details" />
<input type="submit" name="ctl00$MainContent$exportINExcel2" value="Export In Excel" />
</form>
"""

ENTRY_FORM = """
<form><input type="submit" name="ctl00$MainContent$BtnSave" value="&#2360;&#2375;&#2357; &#2325;&#2352;&#2375;&#2306;" />
<input type="submit" name="ctl00$MainContent$btnReset" value="Reset" /></form>
"""


def test_hidden_fields_collected():
    h = aspnet.hidden_fields(PAGE)
    assert h["__VIEWSTATE"] == "HKly/kFS0R2II085"
    assert h["__VIEWSTATEGENERATOR"] == "38E0505A"
    assert h["__EVENTVALIDATION"] == "OZAA7i9vRy1XhVbR"
    # present-but-empty is correct; omitting the key entirely is not
    assert "__EVENTARGUMENT" in h and "__LASTFOCUS" in h


def test_form_fields_never_sends_empty_select():
    """EventValidation answers 500 for a value outside the registered set."""
    f = aspnet.form_fields(PAGE)
    for name in aspnet.selects(PAGE):
        assert f[name] != "", f"{name} would 500"
    assert f["ctl00$MainContent$ddlFY"] == "2627"


def test_form_fields_honours_overrides():
    f = aspnet.form_fields(PAGE, selected={"ctl00$MainContent$ddlDistrict": "209"})
    assert f["ctl00$MainContent$ddlDistrict"] == "209"


def test_options_drops_placeholders():
    assert aspnet.options(PAGE, "ctl00$MainContent$ddlDistrict") == [("209", "ARARIA")]
    assert aspnet.options(PAGE, "ctl00$MainContent$ddlProject") == []


def test_expect_populated_catches_the_silent_cascade_failure():
    aspnet.expect_populated(PAGE, "ctl00$MainContent$ddlDistrict")
    with pytest.raises(RuntimeError, match="one at a time"):
        aspnet.expect_populated(PAGE, "ctl00$MainContent$ddlProject")


def test_is_ajax_false_without_scriptmanager():
    """Bihar has no UpdatePanel; sending AJAX headers there wasted a cycle."""
    assert aspnet.is_ajax(PAGE) is False


def test_a_bare_scriptmanager_mention_is_not_evidence_of_ajax():
    """CORRECTED. This assertion previously expected True.

    The script being on the page says nothing; what matters is whether any
    UpdatePanel is registered. Bihar's death-grant form loads the script and
    registers none, and treating it as AJAX broke every request to it.
    """
    assert aspnet.is_ajax('<script>Sys.WebForms.PageRequestManager</script>') is False
    assert aspnet.is_ajax('<div id="up1" class="UpdatePanel">x</div>') is True


def test_write_buttons_detects_a_hindi_save():
    """The death-grant form's intent is only visible in its Hindi value."""
    assert aspnet.write_buttons(PAGE) == []
    found = aspnet.write_buttons(ENTRY_FORM)
    assert found and "BtnSave" in found[0][0]


def test_export_controls_found():
    assert aspnet.export_controls(PAGE)[0][1] == "Export In Excel"


def test_reseat_rewalks_to_position():
    class FakeClient:
        def __init__(self): self.steps = []
        def select(self, c, v): self.steps.append((c, v))
    c = aspnet.reseat(FakeClient, [("d", "209"), ("p", "1020901")])
    assert c.steps == [("d", "209"), ("p", "1020901")]


# ---- added after the Bihar death-grant form cost four debugging cycles ----

PRM_NO_PANELS = (
    "<script>Sys.WebForms.PageRequestManager._initialize("
    "'ctl00$ScriptManager1', 'aspnetForm', [], [], [], 90, 'ctl00');</script>")
PRM_WITH_PANELS = (
    "<script>Sys.WebForms.PageRequestManager._initialize("
    "'ctl00$ScriptManager1', 'aspnetForm', ['tUpdatePanel1',''], [], [], 90, 'ctl00');</script>")


def test_is_ajax_false_when_scriptmanager_registers_no_panels():
    """The exact shape of Bihar's death-grant form.

    The ScriptManager script is present, so a grep-based check says AJAX and the
    resulting X-MicrosoftAjax headers break the request. No panels are
    registered, so these are ordinary full postbacks.
    """
    assert aspnet.is_ajax(PRM_NO_PANELS) is False


def test_is_ajax_true_when_panels_are_registered():
    assert aspnet.is_ajax(PRM_WITH_PANELS) is True


def test_browser_ua_is_available_for_hosts_that_reject_the_library():
    assert "Mozilla/5.0" in aspnet.BROWSER_UA
    assert "commoner-probe" not in aspnet.BROWSER_UA


def test_diagnose_500_names_the_user_agent_as_the_cause():
    """Bihar answers 500 to the library UA and 200 to a browser UA."""
    def factory(ua):
        return ua
    def post(ua):
        if ua is None:
            raise RuntimeError("HTTP 500")
    assert aspnet.diagnose_500(factory, "u", post) == "user-agent"


def test_diagnose_500_blames_the_request_when_both_fail():
    def post(ua):
        raise RuntimeError("HTTP 500")
    assert aspnet.diagnose_500(lambda ua: ua, "u", post) == "request"


def test_diagnose_500_reports_transient_when_a_retry_succeeds():
    assert aspnet.diagnose_500(lambda ua: ua, "u", lambda ua: None) == "transient"

