"""Static checks for the weekly source-freshness workflow and its README section.

The checks read the files as text, so they run without a YAML parser.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _ROOT / ".github" / "workflows" / "source-freshness.yml"
_README = _ROOT / "README.md"

_STATUSES = ("fresh", "stale", "broken", "down", "unreachable", "geo-fenced")


def _text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_triggers_include_schedule_and_manual_dispatch():
    text = _text()
    assert re.search(r'^on:\n\s+schedule:\n\s+- cron: "30 3 \* \* 1"', text, re.M)
    assert re.search(r"^\s+workflow_dispatch:", text, re.M)


def test_job_timeout_is_90_minutes():
    assert re.search(r"^\s+timeout-minutes: 90$", _text(), re.M)


def test_permissions_let_the_job_open_a_pr():
    match = re.search(r"^permissions:\n((?:\s+\S.*\n)+)", _text(), re.M)
    assert match
    block = match.group(1)
    assert re.search(r"^\s+contents: write$", block, re.M)
    assert re.search(r"^\s+pull-requests: write$", block, re.M)
    assert re.search(r"^\s+actions: read$", block, re.M)


def test_check_step_passes_state_and_github_output():
    match = re.search(r"- name: Check sources\n(?:\s+.*\n)+?\s+run: >\n((?:\s+\S.*\n)+)", _text())
    assert match
    command = " ".join(match.group(1).split())
    assert command.startswith("python scripts/check_sources.py")
    assert "--state state.json" in command
    assert '--github-output "$GITHUB_OUTPUT"' in command


def test_pr_step_uses_the_bot_branch_and_only_the_two_artifacts():
    text = _text()
    assert re.search(r"^\s+branch: bot/source-freshness$", text, re.M)
    match = re.search(r"^\s+add-paths: \|\n((?:\s+[^\s:]+\n)+)", text, re.M)
    assert match
    assert match.group(1).split() == ["SOURCES.md", "staleness/sources.json"]


def test_state_download_is_tolerant_of_failure_and_authenticated():
    text = _text()
    assert re.search(r"^\s+GH_TOKEN: \$\{\{ github\.token \}\}$", text, re.M)
    assert re.search(r"gh run download .*--name source-state .*\\\n\s+\|\| echo ", text)
    assert "continue-on-error" not in text


def test_workflow_text_documents_the_repository_setting():
    assert "Allow GitHub Actions to create and approve pull requests" in _text().replace(
        "\n# ", " "
    )


def test_readme_documents_source_freshness_statuses():
    readme = _README.read_text(encoding="utf-8")
    assert "## Source freshness" in readme
    section = readme.split("## Source freshness", 1)[1].split("\n## ", 1)[0]
    for status in _STATUSES:
        assert f"`{status}`" in section
