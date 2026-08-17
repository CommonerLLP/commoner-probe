"""The version doctor reports three numbers, and never invents agreement.

`importlib.metadata` serves the version recorded at INSTALL time, so a stale
editable install silently invalidates every version gate built on it. This repo's
own version test failed for an unknown period reading 0.14.7 against a source of
0.14.6, and one consumer ran 0.13.0 against a declared pin of 0.14.3.

No network, and no reliance on this environment's real metadata except in the one
test that says so.
"""

from __future__ import annotations

from commoner_probe.doctor import (
    VersionReport,
    declared_pins,
    installed_version,
    source_version,
    version_report,
)


def _pyproject(tmp_path, version="0.15.0"):
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "commoner-probe"\nversion = "{version}"\n',
                    encoding="utf-8")
    return path


class TestReadingEachNumber:
    def test_it_reads_the_source_version(self, tmp_path):
        assert source_version(_pyproject(tmp_path, "0.16.0")) == "0.16.0"

    def test_a_missing_pyproject_is_unknown_not_zero(self, tmp_path):
        assert source_version(tmp_path / "absent.toml") is None

    def test_a_pyproject_without_a_version_is_unknown(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text('[project]\nname = "x"\n', encoding="utf-8")
        assert source_version(path) is None

    def test_it_reads_this_environment_s_installed_version(self):
        assert installed_version() is not None, "this package is installed in the venv"

    def test_an_absent_package_is_unknown(self):
        assert installed_version("no-such-package-anywhere") is None


class TestDeclaredPins:
    def test_it_reads_an_exact_pypi_pin(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text("commoner-probe==0.14.3\nrequests>=2\n", encoding="utf-8")
        assert declared_pins(path) == {str(path): "0.14.3"}

    def test_it_reads_a_git_tag_pin(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text(
            "commoner-probe @ git+https://github.com/CommonerLLP/commoner-probe.git@v0.14.9\n",
            encoding="utf-8")
        assert declared_pins(path) == {str(path): "0.14.9"}

    def test_a_missing_file_is_skipped_not_reported_unpinned(self, tmp_path):
        """No pin found and no file are different facts, and only the caller knows
        which files should exist."""
        assert declared_pins(tmp_path / "absent.txt") == {}

    def test_a_file_with_no_pin_on_this_package_yields_nothing(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text("requests==2.32.0\n", encoding="utf-8")
        assert declared_pins(path) == {}


class TestComparing:
    def test_source_and_installed_disagreeing_is_a_mismatch(self):
        report = VersionReport(source="0.14.6", installed="0.14.7")
        assert report.agrees is False
        assert "pip install -e ." in report.report
        assert "0.14.6" in report.report and "0.14.7" in report.report

    def test_agreement_is_stated_plainly(self):
        report = VersionReport(source="0.15.0", installed="0.15.0")
        assert report.agrees is True
        assert "agree" in report.report

    def test_an_unknown_number_is_not_a_mismatch(self):
        """An unknown cannot disagree. Reporting it as a mismatch sends a reader to
        fix a file that is not the problem."""
        report = VersionReport(source=None, installed="0.15.0")
        assert report.agrees is True
        assert "UNKNOWN" in report.report

    def test_a_pin_that_disagrees_with_the_environment_is_a_mismatch(self):
        """A reviewer reads the pin to learn which version a repo runs. When they
        differ, the pin credits the environment with whatever changed between."""
        report = VersionReport(source="0.15.0", installed="0.13.0",
                               pins={"consumer/requirements.txt": "0.14.3"})
        assert report.agrees is False
        assert "0.14.3" in report.report

    def test_a_matching_pin_is_reported_and_is_not_a_mismatch(self):
        report = VersionReport(source="0.15.0", installed="0.15.0",
                               pins={"r.txt": "0.15.0"})
        assert report.agrees is True
        assert "r.txt" in report.report


class TestAgainstThisTree:
    def test_this_checkout_and_this_environment_agree(self):
        """The check the repo has needed since a shared venv reported 0.14.9 for a
        tree that said 0.15.0."""
        report = version_report()
        assert report.source is not None
        assert report.installed is not None
        assert report.agrees, report.report


class TestEveryPinFormTheOrgActuallyUses:
    """Measured against the seven live consumer files on 2026-08-17. The first
    version of this reader found ONE pin where three existed, because it required
    the requirement to start the line and carry no extras."""

    def test_it_reads_a_pin_carrying_extras(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text("commoner-probe[http,pdf]==0.14.3\n", encoding="utf-8")
        assert declared_pins(path) == {str(path): "0.14.3"}

    def test_it_reads_a_pin_inside_a_pyproject_dependency_list(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text('[project]\ndependencies = [\n    "commoner-probe==0.14.3",\n]\n',
                        encoding="utf-8")
        assert declared_pins(path) == {str(path): "0.14.3"}

    def test_it_reads_a_pin_with_extras_inside_a_pyproject(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text('dependencies = ["commoner-probe[budget]==0.15.0"]\n', encoding="utf-8")
        assert declared_pins(path) == {str(path): "0.15.0"}

    def test_a_file_that_names_the_package_and_pins_no_version_is_reported(self, tmp_path):
        """Unpinned is not unmentioned. The org requires an exact pin, so a
        consumer that depends on this package without one is a finding, and
        returning nothing hid it among the files that never mention it."""
        path = tmp_path / "requirements.txt"
        path.write_text("commoner-probe>=0.14\n", encoding="utf-8")
        assert declared_pins(path) == {str(path): "unpinned"}

    def test_a_comment_naming_the_package_is_not_a_pin(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text("# acquisition is delegated to commoner-probe\nrequests==2.32.0\n",
                        encoding="utf-8")
        assert declared_pins(path) == {}
