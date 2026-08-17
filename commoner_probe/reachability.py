# SPDX-License-Identifier: MIT
"""Measure whether an Indian government host is geo-fenced, per host.

This module drives nothing. It answers three questions about a host before any
acquisition code is written against it. Does the host answer from where this
process runs. Does it answer from Indian egress. Is either answer trustworthy.

THE MEASUREMENT, and the setup it wasted
========================================
Both measurements are from Canadian egress on 2026-08-14. `goir.ap.gov.in`
returned 000, which is no response at all. The same host served normally from an
ap-south-1 host. `apsac.ap.gov.in` served 529 GeoServer layers from BOTH sides.
The session generalised from the first host to the second. It provisioned Indian
egress for `apsac` and needed none of it.

**Geo-fencing is a property of the host. It is not a property of the state, the
department or the domain.** Two hosts under `ap.gov.in` behaved oppositely.
Therefore this module measures one host, and reports each side separately.

A 404 IS NOT A DOWN HOST, AND A HEAD IS NOT A PROBE
===================================================
`goir.ap.gov.in` answers 404 to a HEAD on every path. Some of those paths serve
the register to a GET. `curl -I` reported the site dead. A GET on the same URL
returned the Government Orders Issue Register. Therefore
:func:`status_via_session` issues a GET. Any HTTP status counts as reachable,
including 404, 403 and 503, because the status proves the host answered. Only "no
response" is unreachable.

A NULL RESULT NEEDS A POSITIVE CONTROL, ON EACH SIDE
====================================================
"No response" also happens when the egress is broken. On 2026-08-14 a transient
DNS failure on the operator's laptop killed a sweep that ran for hours. The
resolver said ``nodename nor servname provided``. Read as a reachability
measurement, that failure says nothing about the host.

So each side probes a control URL after a failure. Control passes and the host
did not answer → :data:`UNREACHABLE`. Control also failed → :data:`INCONCLUSIVE`.
INCONCLUSIVE is the honest answer, and it never reads as unreachable.

NOTHING ASKED FROM INDIA IS NOT "UNREACHABLE FROM INDIA"
========================================================
The India side needs a relay the caller supplies. This package declares
``dependencies = []`` and holds no Indian host of its own. With no relay the India
side is :data:`NOT_MEASURED`. Then :attr:`Reachability.needs_indian_egress` is
``None`` rather than ``False``. An unmeasured side must never read as a finding.

ONE KNOWN LIMIT
===============
On the requests-backed session, a host answering 5xx costs the retry ladder in
`http_client`. That can surface as a raised error rather than a status. This
module then reports UNREACHABLE with a passing control, which understates the
host. Read a 5xx host from the ``detail`` string, not from the status alone.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "INCONCLUSIVE",
    "NOT_MEASURED",
    "REACHABLE",
    "UNREACHABLE",
    "Probe",
    "Reachability",
    "reachability",
    "status_via_session",
]

#: The host answered with an HTTP status.
REACHABLE = "reachable"
#: The host did not answer, and the control on the same side did.
UNREACHABLE = "unreachable"
#: Nothing was asked from this side.
NOT_MEASURED = "not_measured"
#: The host did not answer, and neither did the control, so the egress is suspect.
INCONCLUSIVE = "inconclusive"

#: Control URL for both sides. IANA reserves the domain, it serves worldwide, it
#: is not rate-limited against research traffic, and it is not an Indian host —
#: which matters, because a control that is itself geo-fenced would fail exactly
#: where the measurement is interesting.
DEFAULT_CONTROL_URL = "https://example.com/"

DEFAULT_TIMEOUT = 20


@dataclass(frozen=True)
class Probe:
    """One side's answer, with the control that licenses it."""

    side: str
    status: str
    code: int | None = None
    control_code: int | None = None
    detail: str = ""

    @property
    def line(self) -> str:
        if self.status == REACHABLE:
            served = f"HTTP {self.code}" if self.code is not None else self.detail
            return f"{self.side}: reachable ({served})"
        if self.status == UNREACHABLE:
            # The detail carries the host's own words. Printing "no response" and
            # dropping it told a reader nothing was received, when a 5xx or a
            # throttle had been received and read.
            return (
                f"{self.side}: UNREACHABLE — {self.detail or 'no response'}"
            )
        if self.status == NOT_MEASURED:
            return f"{self.side}: not measured — {self.detail}"
        return f"{self.side}: INCONCLUSIVE — {self.detail}"


@dataclass(frozen=True)
class Reachability:
    """Both sides of one host's reachability, and what they do and do not settle."""

    host: str
    url: str
    here: Probe
    india: Probe

    @property
    def needs_indian_egress(self) -> bool | None:
        """``True``, ``False``, or ``None`` for not established.

        ``None`` is the value the wasted setup needed. It means the two probes
        together do not answer the question, so no host should be provisioned on
        the strength of them.
        """
        if self.here.status == REACHABLE:
            return False
        if self.here.status == UNREACHABLE and self.india.status == REACHABLE:
            return True
        return None

    @property
    def report(self) -> str:
        verdict = {
            True: "Indian egress IS required for this host",
            False: "Indian egress is NOT required for this host",
            None: "whether Indian egress is required is not established",
        }[self.needs_indian_egress]
        return f"{self.url}\n  {self.here.line}\n  {self.india.line}\n  {verdict}"


def status_via_session(session: Any, url: str, *, timeout: int = DEFAULT_TIMEOUT) -> int:
    """GET ``url`` and return the HTTP status, whatever it is.

    A GET, never a HEAD: `goir.ap.gov.in` answers 404 to a HEAD on paths that
    serve. ``respect_robots=False`` because this retrieves no record — it asks
    only whether the host answers — and a robots fetch against a host that may
    not answer is the same measurement twice.
    """
    return int(session.get(url, timeout=timeout, respect_robots=False).status_code)


def _answered(code: int | None) -> bool:
    """A host answered if it returned any HTTP status.

    ``curl`` reports 000 when nothing answered, and callers pass that through as
    0. It is not an HTTP status.
    """
    return code is not None and code != 0


def _control_passed(code: int | None) -> bool:
    """A control passes on a success or a redirect, and on nothing else.

    A control answering 503 proves a server is unhappy, not that this egress
    works, so it cannot license a claim of unreachability elsewhere.
    """
    return code is not None and 200 <= code < 400


#: Failures raised BEFORE a request leaves this process. They measure the
#: caller's own policy, not the host: the SSRF guard, robots.txt, and the response
#: cap. Reporting them as "the host did not answer" claims a measurement that was
#: never taken.
_NEVER_ASKED = (ValueError, PermissionError)


def _status_in(text: str) -> int | None:
    """An HTTP status named in a failure message, if there is one.

    A refusal is an ANSWER. `RetrySession` retries 429 and every 5xx and then
    raises `RuntimeError("HTTP 429 <url>")`, and reading that as silence flipped
    the verdict to "Indian egress IS required" for a host that had just answered
    from here — the wasted provisioning this module exists to prevent.
    """
    match = re.search(r"\bHTTP\s+(\d{3})\b", text)
    return int(match.group(1)) if match else None


def _probe(side: str, url: str, control_url: str, fetch: Callable[[str], int] | None,
           absent: str) -> Probe:
    if fetch is None:
        return Probe(side=side, status=NOT_MEASURED, detail=absent)
    try:
        code: int | None = int(fetch(url))
        failure = ""
    except _NEVER_ASKED as exc:
        return Probe(
            side=side, status=INCONCLUSIVE,
            detail=(f"{type(exc).__name__}: {exc} — this request never left the "
                    "process, so nothing is established about the host"),
        )
    except Exception as exc:  # noqa: BLE001 - any failure to answer is the measurement
        code, failure = None, f"{type(exc).__name__}: {exc}"
    if _answered(code):
        return Probe(side=side, status=REACHABLE, code=code)
    answered = _status_in(failure)
    if answered is not None:
        return Probe(side=side, status=REACHABLE, code=answered,
                     detail=f"the host answered {failure}")

    try:
        control: int | None = int(fetch(control_url))
        control_failure = ""
    except Exception as exc:  # noqa: BLE001 - a failed control is the point
        control, control_failure = None, f"{type(exc).__name__}: {exc}"
    said = failure or f"HTTP {code}"
    if _control_passed(control):
        return Probe(
            side=side, status=UNREACHABLE, code=code, control_code=control,
            detail=f"the host said {said}; the control {control_url} answered HTTP {control}",
        )
    return Probe(
        side=side, status=INCONCLUSIVE, code=code, control_code=control,
        detail=(
            f"the host said {said}, and the control {control_url} said "
            f"{control_failure or f'HTTP {control}'} — this egress is broken, so nothing "
            "is established about the host"
        ),
    )


def reachability(
    host: str,
    *,
    fetch: Callable[[str], int] | None = None,
    relay: Callable[[str], int] | None = None,
    control_url: str = DEFAULT_CONTROL_URL,
) -> Reachability:
    """Report a host's reachability from here and from India, with controls.

    ``host`` is a bare hostname, or a full URL when a specific path answers.

    ``fetch`` and ``relay`` both take a URL and return an HTTP status, and both
    raise when nothing answered. ``fetch`` defaults to this package's HTTP client
    from this process's egress. ``relay`` has no default: the India side is
    measured only through a caller-supplied Indian host, and is reported
    :data:`NOT_MEASURED` otherwise.

    ::

        r = reachability("goir.ap.gov.in", relay=via_mumbai)
        print(r.report)
        if r.needs_indian_egress is None:
            ...          # provision nothing on this measurement
    """
    name = (host or "").strip()
    if not name:
        raise ValueError("reachability() needs a host; a blank host probes nothing")
    url = name if "://" in name else f"https://{name}/"

    if fetch is None:
        from .http_client import make_session

        session = make_session()

        def fetch(target: str) -> int:  # noqa: F811 - the documented default
            return status_via_session(session, target)

    return Reachability(
        host=name,
        url=url,
        here=_probe(
            "from here", url, control_url, fetch,
            absent="no local fetch was given, so nothing was asked from this egress",
        ),
        india=_probe(
            "from India", url, control_url, relay,
            absent="no relay was given, so nothing was asked from Indian egress",
        ),
    )
