"""The dependent-dropdown walk: postback rules, resumption, recovery.

The fake server reproduces the behaviours that decide whether such a crawl
finishes — a 500 for an unregistered control value, one level populated per
postback, and a session that expires after N posts and only a rebuild fixes.

No network.
"""

from __future__ import annotations

import pytest

from commoner_probe import aspnet_cascade as ac

CONTROLS = {"district": "ctl00$ddlDistrict", "project": "ctl00$ddlProject",
            "sector": "ctl00$ddlSector"}
LEVELS = ["district", "project", "sector"]

TREE = {
    "d1": {"p1": ["s1", "s2"], "p2": ["s3"]},
    "d2": {"p3": ["s4"]},
}


class _Response:
    def __init__(self, body: str, status: int = 200):
        self.content = body.encode()
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Portal:
    """One level populated per postback, and a session that can expire.

    `expire_after` makes every request fail until `reset` is called, which is
    how the real server behaves once its VIEWSTATE goes stale: retrying the
    same request cannot help, because the fault is in the state.
    """

    def __init__(self, *, expire_after: int | None = None, dead: set[str] | None = None):
        self.posts: list[dict] = []
        self.gets = 0
        self.expire_after, self.dead = expire_after, (dead or set())
        self._since_reset = 0
        self._state: dict[str, str] = {}

    def _page(self) -> str:
        d, p = self._state.get("district"), self._state.get("project")
        opts = {
            "district": list(TREE),
            "project": list(TREE.get(d, {})) if d else [],
            "sector": TREE.get(d, {}).get(p, []) if d and p else [],
        }
        out = ('<input name="__VIEWSTATE" value="vs" />'
               '<input name="__VIEWSTATEGENERATOR" value="gen" />'
               '<input name="__EVENTVALIDATION" value="ev" />')
        for level, control in CONTROLS.items():
            body = "".join(f'<option value="{v}">{v.upper()}</option>' for v in opts[level])
            # The placeholder carries value="0", not "": it IS registered, so
            # posting it passes EventValidation.
            out += f'<select name="{control}"><option value="0">--Select--</option>{body}</select>'
        return out

    def get(self, url, **kw):
        self.gets += 1
        self._since_reset = 0
        self._state.clear()
        return _Response(self._page())

    def post(self, url, data=None, **kw):
        data = data or {}
        self.posts.append(data)
        self._since_reset += 1
        if self.expire_after is not None and self._since_reset > self.expire_after:
            return _Response("session expired", 500)
        for name, value in data.items():
            if name.startswith("ctl00") and value == "":
                return _Response("unregistered value", 500)
        target = data.get("__EVENTTARGET")
        for level, control in CONTROLS.items():
            if control == target:
                value = data[control]
                if value in self.dead:
                    return _Response("branch error", 500)
                self._state[level] = value
                # Choosing a level clears everything under it.
                for lower in LEVELS[LEVELS.index(level) + 1:]:
                    self._state.pop(lower, None)
        return _Response(self._page())


def _crawler(portal) -> ac.CascadeCrawler:
    return ac.CascadeCrawler("https://portal.example/report.aspx", CONTROLS, session=portal)


class TestThePostbackRules:
    def test_no_control_is_ever_posted_empty(self):
        """The server registers each control's option set and answers HTTP 500
        — not a validation message — for anything outside it."""
        portal = _Portal()
        list(ac.walk(_crawler(portal), LEVELS))
        assert portal.posts
        for post in portal.posts:
            for name, value in post.items():
                assert not (name.startswith("ctl00") and value == ""), name

    def test_the_state_tokens_of_the_last_response_are_echoed(self):
        portal = _Portal()
        crawler = _crawler(portal)
        crawler.select("district", "d1")
        assert portal.posts[-1]["__VIEWSTATE"] == "vs"
        assert portal.posts[-1]["__EVENTVALIDATION"] == "ev"

    def test_one_level_is_populated_per_postback(self):
        """Two levels in one POST returns a page whose second list is empty,
        which is indistinguishable from a working request that found nothing."""
        crawler = _crawler(_Portal())
        assert crawler.options("project") == []
        crawler.select("district", "d1")
        assert [v for v, _ in crawler.options("project")] == ["p1", "p2"]

    def test_the_placeholder_is_not_offered_as_a_choice(self):
        crawler = _crawler(_Portal())
        assert [v for v, _ in crawler.options("district")] == ["d1", "d2"]


class TestTheWalk:
    def test_every_leaf_is_reached_once(self):
        rows = list(ac.walk(_crawler(_Portal()), LEVELS))
        assert [r["sector_code"] for r in rows] == ["s1", "s2", "s3", "s4"]
        assert len({(r["district_code"], r["project_code"], r["sector_code"])
                    for r in rows}) == 4

    def test_each_row_carries_its_whole_path(self):
        rows = list(ac.walk(_crawler(_Portal()), LEVELS))
        assert rows[0]["district_code"] == "d1" and rows[0]["district"] == "D1"
        assert rows[0]["project_code"] == "p1"
        assert rows[0]["_key"] == "d1|p1"

    def test_only_restricts_a_level(self):
        rows = list(ac.walk(_crawler(_Portal()), LEVELS, only={"district": ["d2"]}))
        assert {r["district_code"] for r in rows} == {"d2"}

    def test_skip_resumes_without_refetching_a_collected_branch(self):
        portal = _Portal()
        rows = list(ac.walk(_crawler(portal), LEVELS, skip={"d1|p1"}))
        assert [r["sector_code"] for r in rows] == ["s3", "s4"]
        assert not any(p.get(CONTROLS["project"]) == "p1"
                       for p in portal.posts if p.get("__EVENTTARGET") == CONTROLS["project"])

    def test_a_cascade_needs_two_levels(self):
        with pytest.raises(ValueError, match="at least one level"):
            list(ac.walk(_crawler(_Portal()), ["district"]))


class TestRecovery:
    def test_an_expired_session_is_rebuilt_and_the_walk_continues(self):
        """Retrying the request cannot help — only a rebuild can. A walk that
        treated 500 as fatal would report a partial crawl as a complete one."""
        portal = _Portal(expire_after=2)
        rows = list(ac.walk(_crawler(portal), LEVELS))
        assert [r["sector_code"] for r in rows] == ["s1", "s2", "s3", "s4"]
        assert portal.gets > 1, "the session was never rebuilt"

    def test_a_dead_branch_does_not_end_the_crawl(self):
        portal = _Portal(dead={"p1"})
        rows = list(ac.walk(_crawler(portal), LEVELS))
        assert [r["sector_code"] for r in rows] == ["s3", "s4"], "the rest must survive"

    def test_a_dead_branch_is_not_recorded_as_collected(self):
        """It stays out of the resume set, so a later pass retries it rather
        than treating the hole as an empty branch."""
        portal = _Portal(dead={"p1"})
        rows = list(ac.walk(_crawler(portal), LEVELS))
        assert "d1|p1" not in {r["_key"] for r in rows}


class TestTheWorkedInstance:
    def test_the_preset_names_a_live_host_and_its_levels(self):
        """A blanket rename once rewrote this host into one that does not exist.
        That surfaces only as a connection error at crawl time."""
        preset = ac.BIHAR_ICDS_ANGANWADI
        assert preset["report_url"].startswith("https://icdsaangan.bihar.gov.in/")
        assert preset["report_url"].endswith(".aspx")
        assert preset["levels"] == ["district", "project", "sector", "awc"]
        assert all(preset["controls"][level].startswith("ctl00$")
                   for level in preset["levels"])

    def test_the_centre_code_is_anchored_to_the_end_of_the_label(self):
        """Centre names carry hyphens and digits of their own, so the second
        field is not the code."""
        assert ac.parse_awc_label("BALUAA MOTI TIKKAR-10209010101-S01") == {
            "awc_name": "BALUAA MOTI TIKKAR", "awc_code": "10209010101",
            "awc_sector": "S01"}
        assert ac.parse_awc_label("WARD-12 NAYA TOLA-10209010102-S02")["awc_name"] == \
            "WARD-12 NAYA TOLA"

    def test_an_unparseable_label_keeps_its_text(self):
        assert ac.parse_awc_label("SOME CENTRE") == {
            "awc_name": "SOME CENTRE", "awc_code": "", "awc_sector": ""}

    def test_crawler_for_builds_from_a_preset(self):
        portal = _Portal()
        crawler = ac.crawler_for(ac.BIHAR_ICDS_ANGANWADI, session=portal)
        assert crawler.controls["district"] == "ctl00$MainContent$ddlDistrict"
