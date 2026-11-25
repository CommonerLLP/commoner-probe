import json

import pytest

from commoner_probe import github_mirror as gm

SHA_A = "a" * 40
SHA_B = "b" * 40


class _Resp:
    def __init__(self, status, body=b""):
        self.status_code = status
        self.content = body
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=16384):
        yield self._body


class _Session:
    """Serves one branch head and a fixed set of files per commit."""

    def __init__(self, head, files):
        self.head, self.files, self.calls = head, files, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if url.startswith("https://api.github.com/"):
            return _Resp(200, self.head.encode())
        sha, path = url.split("/", 6)[5], url.split("/", 6)[6]
        body = self.files.get((sha, path))
        return _Resp(404) if body is None else _Resp(200, body)


def _fetch(tmp_path, session, paths):
    return gm.fetch("datameet/example", "master", paths, tmp_path,
                    upstream_source="https://example.gov.in/open-data",
                    license="GODL", session=session)


def test_files_come_from_the_resolved_commit_with_provenance(tmp_path):
    s = _Session(SHA_A, {(SHA_A, "bihar/x.zip"): b"zipbytes"})
    [rec] = _fetch(tmp_path, s, ["bihar/x.zip"])
    assert rec["status"] == "downloaded" and rec["commit_sha"] == SHA_A
    assert rec["secondary"] is True
    assert rec["upstream_source"] == "https://example.gov.in/open-data"
    assert rec["bytes"] == 8 and len(rec["sha256"]) == 64
    assert (tmp_path / rec["dest"]).read_bytes() == b"zipbytes"
    assert f"/{SHA_A}/bihar/x.zip" in rec["url"]
    line = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[0])
    assert line == rec


def test_a_moved_branch_records_a_new_sha_and_keeps_the_old_file(tmp_path):
    _fetch(tmp_path, _Session(SHA_A, {(SHA_A, "f.csv"): b"old"}), ["f.csv"])
    [rec] = _fetch(tmp_path, _Session(SHA_B, {(SHA_B, "f.csv"): b"new"}), ["f.csv"])
    assert rec["commit_sha"] == SHA_B
    rows = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert [r["commit_sha"] for r in rows] == [SHA_A, SHA_B]
    assert (tmp_path / rows[0]["dest"]).read_bytes() == b"old"
    assert (tmp_path / rows[1]["dest"]).read_bytes() == b"new"


def test_a_missing_path_is_recorded_not_skipped(tmp_path):
    s = _Session(SHA_A, {(SHA_A, "a.csv"): b"1"})
    recs = _fetch(tmp_path, s, ["a.csv", "gone.csv"])
    assert [r["status"] for r in recs] == ["downloaded", "missing"]
    assert recs[1]["sha256"] is None and recs[1]["http_status"] == 404


def test_an_lfs_pointer_is_not_written_as_data(tmp_path):
    ptr = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 9\n"
    [rec] = _fetch(tmp_path, _Session(SHA_A, {(SHA_A, "big.zip"): ptr}), ["big.zip"])
    assert rec["status"] == "lfs_pointer"
    assert not (tmp_path / rec["dest"]).exists()


def test_only_the_ref_lookup_skips_robots(tmp_path):
    s = _Session(SHA_A, {(SHA_A, "a.csv"): b"1"})
    _fetch(tmp_path, s, ["a.csv"])
    api, raw = s.calls
    assert api[1]["respect_robots"] is False
    assert "respect_robots" not in raw[1]


def test_a_non_sha_answer_raises(tmp_path):
    with pytest.raises(gm.GitHubMirrorError):
        _fetch(tmp_path, _Session("<html>rate limited</html>", {}), ["a.csv"])


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs", "a/../../b", ""])
def test_unsafe_paths_raise(tmp_path, bad):
    with pytest.raises(gm.GitHubMirrorError):
        _fetch(tmp_path, _Session(SHA_A, {}), [bad])


def test_an_unsafe_repo_name_raises(tmp_path):
    with pytest.raises(gm.GitHubMirrorError):
        gm.fetch("../x", "main", ["a"], tmp_path, upstream_source="u", session=_Session(SHA_A, {}))


def test_records_validate_against_the_shipped_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from pathlib import Path
    schema = json.loads((Path(gm.__file__).parent / "schemas" / "manifest_github_mirror_file.schema.json").read_text())
    s = _Session(SHA_A, {(SHA_A, "a.csv"): b"1"})
    for rec in _fetch(tmp_path, s, ["a.csv", "gone.csv"]):
        jsonschema.Draft202012Validator(schema).validate(rec)


def test_paths_that_differ_only_in_unsafe_characters_stay_distinct(tmp_path):
    s = _Session(SHA_A, {(SHA_A, ".data.csv"): b"hidden", (SHA_A, "data.csv"): b"plain",
                         (SHA_A, "a%20b.csv"): b"space", (SHA_A, "a_b.csv"): b"under"})
    recs = _fetch(tmp_path, s, [".data.csv", "data.csv", "a b.csv", "a_b.csv"])
    assert len({r["dest"] for r in recs}) == 4
    for rec in recs:
        assert rec["status"] == "downloaded"
        body = (tmp_path / rec["dest"]).read_bytes()
        assert len(body) == rec["bytes"]


def test_raw_url_percent_encodes_reserved_characters(tmp_path):
    s = _Session(SHA_A, {(SHA_A, "data%231.csv"): b"x"})
    [rec] = _fetch(tmp_path, s, ["data#1.csv"])
    assert rec["status"] == "downloaded"
    assert rec["url"].endswith(f"/{SHA_A}/data%231.csv")
    assert rec["path"] == "data#1.csv"


def test_ref_lookup_bypasses_the_http_cache(tmp_path):
    class _CachedSession(_Session):
        def __init__(self, *a):
            super().__init__(*a)
            self.cache_off = False
            self.api_calls_uncached = []

        def cache_disabled(self):
            from contextlib import contextmanager

            @contextmanager
            def off():
                self.cache_off = True
                try:
                    yield
                finally:
                    self.cache_off = False
            return off()

        def get(self, url, **kw):
            if url.startswith("https://api.github.com/"):
                self.api_calls_uncached.append(self.cache_off)
            return super().get(url, **kw)

    s = _CachedSession(SHA_A, {(SHA_A, "f.csv"): b"x"})
    _fetch(tmp_path, s, ["f.csv"])
    assert s.api_calls_uncached == [True]
