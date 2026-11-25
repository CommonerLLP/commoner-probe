# SPDX-License-Identifier: MIT
"""Fetch files from a public GitHub repository, pinned to a commit.

Some government datasets reach us only through community mirrors on GitHub,
for example when the publisher's own host refuses the fetch. A mirror is a
SECONDARY source. Every record therefore says so (`secondary: true`), names
the upstream source the mirror claims to copy, and keeps the commit SHA, so a
later fetch of the same SHA returns the same bytes.

**A branch name is not a pin.** `ref` is resolved to a 40-character commit SHA
before any file is fetched, and every file is fetched from that SHA. A branch
that moves between two runs gives two different SHAs in the manifest, rather
than two different files under one name.

**A Git LFS pointer is not the file.** For an LFS-tracked path,
raw.githubusercontent.com serves a ~130-byte text pointer with HTTP 200. It is
recorded as `status: "lfs_pointer"` and not written as data.

**robots.txt.** api.github.com publishes `Disallow: /` for all agents. The
commits endpoint is GitHub's documented machine interface, so the one call that
resolves a ref passes `respect_robots=False`, as `ckan_resource_api` does for
the CKAN Action API. File downloads keep the robots check.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote

from .base import _private_tmp_path, safe_filename_segment
from .http_client import _uncached, make_session, read_capped_response

API_COMMIT = "https://api.github.com/repos/{repo}/commits/{ref}"
RAW_FILE = "https://raw.githubusercontent.com/{repo}/{sha}/{path}"
KIND = "github_mirror_file"

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"


class GitHubMirrorError(RuntimeError):
    """A ref did not resolve to a commit, or an argument is unsafe."""


def resolve_ref(session: Any, repo: str, ref: str) -> str:
    """The 40-character commit SHA that `ref` names right now."""
    if not _REPO.match(repo) or any(part in (".", "..") for part in repo.split("/")):
        raise GitHubMirrorError(f"not an owner/repo name: {repo!r}")
    # A ref moves, so a cached answer can name an old commit.
    with _uncached(session):
        resp = session.get(API_COMMIT.format(repo=repo, ref=ref),
                           headers={"Accept": "application/vnd.github.sha"},
                           respect_robots=False)
    resp.raise_for_status()
    sha = resp.content.decode("ascii", "replace").strip()
    if not _SHA.match(sha):
        raise GitHubMirrorError(f"{repo}@{ref} did not resolve to a commit SHA: {sha[:80]!r}")
    return sha


def _safe_path(path: str) -> PurePosixPath:
    p = PurePosixPath(path)
    if p.is_absolute() or not p.parts or any(part in ("", ".", "..") for part in p.parts):
        raise GitHubMirrorError(f"unsafe repository path: {path!r}")
    return p


def fetch(repo: str, ref: str, paths: Iterable[str], out_dir: Path | str, *,
          upstream_source: str, license: str | None = None,
          session: Any | None = None) -> list[dict]:
    """Fetch `paths` from `repo` at the commit `ref` resolves to.

    Files land under `out_dir/github/<owner>/<repo>/<sha>/<path>`, and one
    record per path is appended to `out_dir/manifest.jsonl`. A path the commit
    does not hold is recorded as `missing`, never skipped in silence.
    """
    session = session if session is not None else make_session()
    out_dir = Path(out_dir)
    sha = resolve_ref(session, repo, ref)
    owner, name = repo.split("/")
    root = out_dir / "github" / safe_filename_segment(owner) / safe_filename_segment(name) / sha
    records = []
    for path in paths:
        rel = _safe_path(path)
        url = RAW_FILE.format(repo=repo, sha=sha, path=quote(rel.as_posix(), safe="/"))
        # _safe_path already refused traversal, so the path is kept verbatim.
        # Sanitising it maps distinct paths (".data.csv", "data.csv") to one file.
        dest = root.joinpath(*rel.parts)
        record: dict[str, Any] = {
            "key": f"GITHUB|{repo}|{sha}|{rel.as_posix()}",
            "kind": KIND, "record_type": KIND, "source_family": "github_mirror",
            "repo": repo, "ref": ref, "commit_sha": sha, "path": rel.as_posix(),
            "url": url, "dest": dest.relative_to(out_dir).as_posix(),
            "secondary": True, "upstream_source": upstream_source, "license": license,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sha256": None, "bytes": None, "http_status": None, "error": None,
        }
        try:
            resp = session.get(url, stream=True)
            record["http_status"] = resp.status_code
            if resp.status_code == 404:
                record["status"] = "missing"
            else:
                resp.raise_for_status()
                body = read_capped_response(resp)
                if body.startswith(_LFS_POINTER):
                    record["status"] = "lfs_pointer"
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = _private_tmp_path(dest)
                    tmp.write_bytes(body)
                    tmp.replace(dest)
                    record.update(status="downloaded", bytes=len(body),
                                  sha256=hashlib.sha256(body).hexdigest())
        except Exception as exc:  # one failed path must not lose the others' records
            record.update(status="error", error=f"{type(exc).__name__}: {exc}")
        records.append(record)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "manifest.jsonl").open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return records
