# SPDX-License-Identifier: MIT
"""Driving ASP.NET WebForms portals.

Most Indian government MIS portals are WebForms, and the same handful of
behaviours defeat a naive scraper on every one of them. All of these were paid
for in wasted hours against Bihar's Aangan ICDS MIS; none is documented by any
portal.

**1. EventValidation answers 500, not a validation message.**
ASP.NET registers the legal option set for every control and rejects anything
outside it with HTTP 500. The commonest cause is posting ``""`` for dropdowns
you are not changing. Every ``<select>`` on the page must carry a real
registered value, which is what `form_fields` does.

**2. Cascades are sequential, and batching fails SILENTLY.**
Setting year and district in one POST returns HTTP 200 and a complete page
whose district dropdown is populated and whose project dropdown is EMPTY. No
error. Each level must be posted on its own, carrying the ``__VIEWSTATE`` the
previous response returned. `expect_populated` exists because this failure
looks exactly like success.

**3. Session state goes stale, and retrying cannot fix it.**
After a few hundred postbacks every request answers 500 until the session is
rebuilt. HTTP-layer retry (backoff, jitter, Retry-After) makes this worse, not
better, because the fault is in the state rather than the transport. Treat a
persistent 500 as "reseat": new session, re-walk to position. See `reseat`.

**4. Dropdown change and button submit are different mechanisms.**
A dropdown fires ``__doPostBack`` and needs ``__EVENTTARGET`` set. A submit
button posts its own ``name=value`` and NO ``__EVENTTARGET``. Mixing them
yields a page that looks right and contains nothing.

**5. Hidden fields are not optional.**
``__VIEWSTATE``, ``__VIEWSTATEGENERATOR``, ``__EVENTVALIDATION``,
``__EVENTARGUMENT``, ``__LASTFOCUS`` and ``__VIEWSTATEENCRYPTED``. Omitting the
generator or the validation token is another 500.

**6. Cookies must persist.** VIEWSTATE is bound to the session.

**7. Detect AJAX rather than guessing, and check for PANELS not scripts.**
Partial postbacks need ``X-MicrosoftAjax: Delta=true`` and the ScriptManager
field — but only when UpdatePanels are actually registered. Bihar's death-grant
form loads the ScriptManager script and registers ZERO panels
(``PageRequestManager._initialize('ctl00$ScriptManager1','aspnetForm',[],[],[],90,'ctl00')``
— note the empty arrays), so its postbacks are ordinary full postbacks. A check
that merely greps for "PageRequestManager" reports AJAX on that page and sends
headers that break it. `is_ajax` therefore inspects the registered panel list.

**10. AN HONEST USER-AGENT CAN BE REJECTED WITH HTTP 500.**
Bihar's Aangan MIS answers 500 to the default ``commoner-probe/…`` User-Agent
and 200 to a browser string — same URL, same fields, same session, same second.
Not a 403, not a challenge page: a plain 500, indistinguishable from a server
fault or a malformed request. It cost four debugging cycles here, all of them
spent blaming the form construction.

This is a real tension. The library identifies itself so portal operators can
reach us, and that is the right default. But an operator who rejects the
identifier leaves a caller unable to distinguish policy from breakage. So:
`make_session(user_agent=…)` already accepts an override, `BROWSER_UA` is
provided for it, and `diagnose_500` tells you whether the UA is the cause
rather than leaving you to guess. Overriding is a deliberate act; record it
where you do it, as with any robots decision.

**8. Some forms WRITE.** A data-entry form with a Save button will insert a
record into a live government system. `write_buttons` finds them so a crawler
can refuse. This is a correctness and a safety property.

**9. Prefer the export button.** Many GridViews ship an "Export In Excel"
control that returns cleaner data than scraping the rendered table.

Parsing and sanity-checking the resulting tables lives in `gridview`.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any, Callable, Iterable

HIDDEN = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
          "__VIEWSTATEENCRYPTED", "__EVENTARGUMENT", "__LASTFOCUS")

# Buttons whose activation writes to the remote system. Matched case-insensitively
# against the control name AND its visible value, because Hindi-labelled forms
# hide the intent in the value (सेव करें = "save").
WRITE_HINTS = ("save", "insert", "update", "delete", "submitentry", "approve",
               "reject", "सेव", "जमा", "दर्ज")


def hidden_fields(page: str) -> dict[str, str]:
    """The ASP.NET state tokens a postback must echo back."""
    out = {}
    for name in HIDDEN:
        m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), page)
        out[name] = m.group(1) if m else ""
    return out


def selects(page: str) -> dict[str, list[tuple[str, str]]]:
    """Every ``<select>`` on the page as {name: [(value, label), ...]}."""
    return {m.group(1): re.findall(
                r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(0))
            for m in re.finditer(r'<select[^>]*name="([^"]+)".*?</select>', page, re.S)}


def options(page: str, control: str, *, drop=("", "0")) -> list[tuple[str, str]]:
    """Selectable (value, label) pairs, minus placeholder entries."""
    return [(v, _html.unescape(t).strip())
            for v, t in selects(page).get(control, []) if v not in drop]


def form_fields(page: str, *, selected: dict[str, str] | None = None) -> dict[str, str]:
    """A POST body that EventValidation will accept.

    Every select gets its first REGISTERED option unless overridden — never
    ``""``, which is the single commonest cause of an unexplained 500.

    A placeholder is fine as long as the server registered it. On these pages
    `--Select--` usually carries `value="0"`, which is in the option set and so
    passes validation; `options` drops it for DISPLAY, and reusing that filter
    here would leave an un-populated select with nothing to send. Only a truly
    empty value is unsafe, so only that is skipped. A select carrying nothing
    but an empty value still yields `""`: there is no registered value to send,
    and inventing one would trade a documented 500 for a silently wrong query.
    """
    fields = hidden_fields(page)
    for name, opts in selects(page).items():
        registered = [v for v, _ in opts if v != ""]
        fields[name] = registered[0] if registered else ""
    fields.update(selected or {})
    return fields


# A browser User-Agent, for hosts that reject the library's own identifier.
# See failure mode 10 in the module docstring before reaching for this.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# PageRequestManager._initialize('ctl00$ScriptManager1','aspnetForm',[...panels...],...)
_PRM_INIT = re.compile(
    r"PageRequestManager\._initialize\(\s*'[^']*'\s*,\s*'[^']*'\s*,\s*\[(?P<panels>.*?)\]",
    re.S)


def is_ajax(page: str) -> bool:
    """True only when UpdatePanels are actually REGISTERED.

    Presence of the ScriptManager script is not enough. Bihar's death-grant form
    loads it and registers no panels, so its postbacks are full postbacks; a
    grep-for-the-script check reports AJAX and the resulting headers break the
    request. Falls back to looking for panel markup when the initialiser is
    absent.
    """
    m = _PRM_INIT.search(page)
    if m:
        return bool(m.group("panels").strip())
    low = page.lower()
    return "asyncpostbacktrigger" in low or "updatepanel" in low


def ajax_headers() -> dict[str, str]:
    return {"X-MicrosoftAjax": "Delta=true", "X-Requested-With": "XMLHttpRequest"}


def diagnose_500(session_factory: Any, url: str, post: Callable[[Any], Any]) -> str:
    """Say WHY a request 500s, instead of leaving the caller to guess.

    `session_factory(user_agent)` must return a session; `post(session)` must
    perform the failing request and raise on error. Distinguishes the three
    causes that all present identically as HTTP 500:

      "user-agent"  the host rejects the library's identifier (retry with
                    BROWSER_UA and record the override)
      "request"     it fails under both identities — the fault is yours:
                    an empty select, an unregistered __EVENTTARGET, a missing
                    field, or a batched cascade
      "transient"   it succeeded on retry; the earlier failure was noise or a
                    stale session (see failure mode 3)
    """
    try:
        post(session_factory(None))
        return "transient"
    except Exception:
        pass
    try:
        post(session_factory(BROWSER_UA))
        return "user-agent"
    except Exception:
        return "request"


def submit_buttons(page: str) -> list[tuple[str, str]]:
    """(name, value) for every submit control."""
    return re.findall(
        r'<input[^>]*type="submit"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page, re.I)


def write_buttons(page: str) -> list[tuple[str, str]]:
    """Submit controls that would WRITE to the remote system.

    A data-entry form looks like a report until you post it. Checking this
    before submitting anything is the difference between reading a government
    database and inserting a false record into one.
    """
    # The label arrives as numeric HTML entities on Hindi-labelled forms, so a
    # literal match against the Devanagari hints never fires and a live write
    # control reads as harmless. Decode first.
    return [(n, v) for n, v in submit_buttons(page)
            if any(h in _html.unescape(n + " " + v).lower() for h in WRITE_HINTS)]


def export_controls(page: str) -> list[tuple[str, str]]:
    """Export-to-Excel/CSV controls, which usually beat scraping the grid."""
    return [(n, v) for n, v in submit_buttons(page)
            if re.search(r"excel|csv|export|download", n + " " + v, re.I)]


def expect_populated(page: str, control: str, *, context: str = "") -> None:
    """Raise if `control` came back empty.

    A batched cascade returns HTTP 200 with a full page and an empty child
    dropdown. Nothing else in the stack will tell you that the request failed,
    so the check has to be explicit at the call site.
    """
    if not options(page, control):
        raise RuntimeError(
            f"{control} is empty after postback{' (' + context + ')' if context else ''} — "
            "cascade levels must be posted one at a time, each carrying the "
            "__VIEWSTATE returned by the previous response")


def reseat(build: Callable[[], Any], steps: Iterable[tuple[str, str]]) -> Any:
    """Rebuild a poisoned session and re-walk to position.

    `build` returns a fresh client; `steps` are the (control, value) selections
    needed to get back to where the crawl was. Use on a persistent 500 — the
    HTTP layer's retry cannot help, because the state is what is broken.
    """
    client = build()
    for control, value in steps:
        client.select(control, value)
    return client
