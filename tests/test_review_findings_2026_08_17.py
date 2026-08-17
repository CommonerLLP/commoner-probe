"""Regression tests for the review findings of 2026-08-17."""
from __future__ import annotations

import json

import pytest

from commoner_probe import aspnet, aspnet_cascade, cdn_dashboard, spa_jwt_api
from commoner_probe.http_client import StdlibSession


class TestTheStdlibClientSendsJsonBodies:
    """`json=` was read by the requests path and ignored by the stdlib one, so
    a default install posted an empty body and the OTP flow could not work."""

    def test_a_json_body_reaches_the_wire(self, monkeypatch):
        monkeypatch.setattr("commoner_probe.http_client.is_safe_url", lambda u: True)
        sent = {}

        def fake_open(self, req, timeout=None):
            sent["body"] = req.data
            sent["type"] = req.headers.get("Content-type")
            raise SystemExit  # stop before the network

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
        s = StdlibSession()
        with pytest.raises(SystemExit):
            s.post("https://example.gov.in/api", json={"mobile": "9"},
                   respect_robots=False)
        assert json.loads(sent["body"]) == {"mobile": "9"}
        assert sent["type"] == "application/json"

    def test_an_explicit_data_body_still_wins(self, monkeypatch):
        monkeypatch.setattr("commoner_probe.http_client.is_safe_url", lambda u: True)
        sent = {}

        def fake_open(self, req, timeout=None):
            sent["body"] = req.data
            raise SystemExit

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
        s = StdlibSession()
        with pytest.raises(SystemExit):
            s.post("https://example.gov.in/api", data="a=1", respect_robots=False)
        assert sent["body"] == b"a=1"


class TestWriteButtonDetection:
    """A missed write button means a crawler posts a form that inserts a record
    into a live government system."""

    def test_an_entity_encoded_label_is_decoded_before_matching(self):
        page = ('<input type="submit" name="Button1" '
                'value="&#2360;&#2375;&#2357; &#2325;&#2352;&#2375;&#2306;" />')
        assert aspnet.write_buttons(page), "the Hindi label decodes to a save button"

    def test_a_plain_read_button_is_still_harmless(self):
        page = '<input type="submit" name="btnShow" value="Get Details" />'
        assert aspnet.write_buttons(page) == []


class TestTheGeoFenceIsNotAnEmptyPeriod:
    """Outside the publisher's country every object 403s. Reading that as
    'unpublished' turns a blocked run into a clean empty dataset."""

    class _Resp:
        def __init__(self, status, text=""):
            self.status_code, self.text = status, text

        def json(self):
            return {}

        def raise_for_status(self):
            pass

    class _Session:
        def __init__(self, resp):
            self.resp = resp

        def get(self, url, **kw):
            return self.resp

    def test_a_geo_fence_403_raises(self):
        body = "configured to block access from your country"
        sess = self._Session(self._Resp(403, body))
        place = cdn_dashboard.Place(17, "S", 112, "D", "24", "24445")
        with pytest.raises(cdn_dashboard.GeoFenced):
            cdn_dashboard.fetch(sess, 2026, 6, place, "growth")

    def test_an_ordinary_403_is_still_an_absent_period(self):
        sess = self._Session(self._Resp(403, "<Error><Code>AccessDenied</Code></Error>"))
        place = cdn_dashboard.Place(17, "S", 112, "D", "24", "24445")
        assert cdn_dashboard.fetch(sess, 2026, 6, place, "growth") is None


class TestTheCascadeRecovery:
    def test_reseating_builds_a_new_session(self):
        """The stale state lives in the session cookie. Refetching with the same
        session returns the same 500."""
        built = []

        class _Sess:
            def __init__(self):
                built.append(self)

            def get(self, url, **kw):
                class R:
                    content = b'<input name="__VIEWSTATE" value="v" />'
                return R()

            def post(self, url, **kw):
                raise RuntimeError("HTTP 500")

        crawler = aspnet_cascade.CascadeCrawler(
            "https://x.gov.in/r.aspx", {"a": "ctl00$a"},
            session_factory=lambda: _Sess())
        first = crawler.session
        crawler.reset()
        assert crawler.session is not first, "a poisoned session must be replaced"

    def test_an_injected_session_is_never_replaced(self):
        """A test double, or a caller's authenticated session, must survive."""
        class _Sess:
            def get(self, url, **kw):
                class R:
                    content = b""
                return R()

        mine = _Sess()
        crawler = aspnet_cascade.CascadeCrawler("https://x.gov.in/r.aspx",
                                                {"a": "ctl00$a"}, session=mine)
        crawler.reset()
        assert crawler.session is mine


def test_the_cryptography_extra_is_declared():
    """`decrypt_envelope` imports cryptography. A clean install must be able to
    get it from a named extra rather than by guessing."""
    import pathlib
    import re

    # No tomllib. It arrived in 3.11, and CI runs this suite on 3.10 too.
    root = pathlib.Path(spa_jwt_api.__file__).resolve().parent.parent
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    for extra in ("crypto", "all"):
        block = re.search(rf"^{extra} = (\[.*?\])", text, re.S | re.M)
        assert block, f"no {extra} extra is declared"
        assert "cryptography" in block.group(1), f"{extra} does not carry cryptography"


def test_a_lower_cased_content_type_is_not_duplicated(monkeypatch):
    """Header names are case-insensitive. A caller's own `content-type` must
    survive, rather than gain a second header beside it."""
    sent = {}

    def fake_open(self, req, timeout=None):
        sent["headers"] = dict(req.headers)
        raise SystemExit

    monkeypatch.setattr("commoner_probe.http_client.is_safe_url", lambda u: True)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    s = StdlibSession()
    with pytest.raises(SystemExit):
        s.post("https://example.gov.in/api", json={"a": 1},
               headers={"content-type": "application/vnd.api+json"},
               respect_robots=False)
    types = [v for k, v in sent["headers"].items() if k.lower() == "content-type"]
    assert types == ["application/vnd.api+json"], types


class TestTheSessionContract:
    """Three combinations, three documented behaviours. The middle one was
    contradicted by the docstring until review caught it."""

    class _Sess:
        def __init__(self, tag="x"):
            self.tag = tag

        def get(self, url, **kw):
            class R:
                content = b""
            return R()

    def _crawler(self, **kw):
        return aspnet_cascade.CascadeCrawler("https://x.gov.in/r.aspx",
                                             {"a": "ctl00$a"}, **kw)

    def test_a_session_alone_is_never_replaced(self):
        mine = self._Sess("mine")
        c = self._crawler(session=mine)
        c.reset()
        assert c.session is mine

    def test_a_factory_beside_a_session_wins_on_reseat(self):
        """Passing a factory IS the instruction to rebuild. A caller whose
        session carries a login supplies a factory that re-establishes it."""
        built = []

        def factory():
            s = TestTheSessionContract._Sess("rebuilt")
            built.append(s)
            return s

        mine = self._Sess("mine")
        c = self._crawler(session=mine, session_factory=factory)
        assert c.session is mine, "the injected session is used until a reseat"
        c.reset()
        assert c.session is built[-1]

    def test_the_default_client_is_rebuilt(self):
        c = self._crawler(session_factory=lambda: TestTheSessionContract._Sess())
        first = c.session
        c.reset()
        assert c.session is not first


def test_a_falsey_injected_session_is_still_used():
    """`session or factory()` discarded any session object whose truthiness is
    false. A session with `__len__` is ordinary; silently replacing it hands
    the caller a client they did not build."""
    class _Falsey:
        def __len__(self):
            return 0

        def get(self, url, **kw):
            class R:
                content = b""
            return R()

    mine = _Falsey()
    c = aspnet_cascade.CascadeCrawler("https://x.gov.in/r.aspx",
                                      {"a": "ctl00$a"}, session=mine)
    assert c.session is mine
