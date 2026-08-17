"""The four acquisition invariants, in their general form.

Each is drawn from a defect that produced a plausible, complete-looking result
rather than an error. That is the only failure class worth building machinery
against, because an error announces itself and a wrong answer does not.

`geoserver.py` implements two of these for the GIS case. These tests pin the
general form, so the next acquisition does not relearn them.

No network.
"""

from __future__ import annotations

import pytest

from commoner_probe.invariants import (
    ControlFailed,
    PartialCoverage,
    assert_finds,
    collect,
    require_full_coverage,
    saturation,
    unmapped,
)


class TestEnumerateWhatTheSourceOffers:
    """Bihar's ICDS register offers ten drillable reason columns. The scraper's
    map listed six, so every run silently skipped four — and those four traced
    the payment through the administrative chain. Nothing failed."""

    OFFERED = ["Sevika Name", "Honorarium Not Generated", "Not Send To DPO",
               "Not Send To Head-Quarter", "Not Send To Bank", "Contact"]

    def test_it_names_the_columns_the_mapping_misses(self):
        missing = unmapped(self.OFFERED, {"Sevika Name": "name", "Contact": "phone"})
        assert missing == ["Honorarium Not Generated", "Not Send To DPO",
                           "Not Send To Head-Quarter", "Not Send To Bank"]

    def test_a_full_mapping_leaves_nothing_unmapped(self):
        assert unmapped(["a", "b"], {"a": 1, "b": 2}) == []

    def test_it_ignores_blank_and_repeated_headers(self):
        """GridViews emit empty header cells for the drill-down link column."""
        assert unmapped(["a", "", " ", "a"], {"a": 1}) == []

    def test_requiring_coverage_raises_and_names_the_shortfall(self):
        with pytest.raises(PartialCoverage) as excinfo:
            require_full_coverage(self.OFFERED, {"Contact": "phone"}, source="aangan")
        message = str(excinfo.value)
        assert "offers 6" in message
        assert "covers 1" in message
        assert "Not Send To Bank" in message

    def test_requiring_coverage_is_silent_when_the_map_is_complete(self):
        require_full_coverage(["a"], {"a": 1})

    def test_a_mapping_naming_a_column_the_source_dropped_is_reported(self):
        """The reverse direction matters too. A column that disappeared from the
        source leaves the mapping reading a name nothing serves."""
        with pytest.raises(PartialCoverage) as excinfo:
            require_full_coverage(["a"], {"a": 1, "vanished": 2})
        assert "vanished" in str(excinfo.value)


class TestVerifySaturationWithADifferentQueryShape:
    def test_no_new_features_on_a_different_shape_is_saturation(self):
        report = saturation(known=["a", "b", "c"], got=["a", "b", "c"])
        assert report.new == 0
        assert report.recall == 1.0
        assert report.saturated is True

    def test_a_second_pass_that_finds_more_refuses_saturation(self):
        report = saturation(known=["a"], got=["a", "b"])
        assert report.new == 1
        assert report.new_ids == ["b"]
        assert report.saturated is False

    def test_a_partial_second_pass_cannot_certify_anything(self):
        """An empty `new` proves saturation only when the second pass asked
        every question. A pass with holes produces the same empty set for the
        opposite reason."""
        report = saturation(known=["a"], got=["a"], partial=True)
        assert report.new == 0
        assert report.saturated is False
        assert report.partial is True

    def test_recall_counts_the_first_pass_ids_the_second_found(self):
        report = saturation(known=["a", "b", "c", "d"], got=["a", "b"])
        assert report.recall == 0.5
        assert report.saturated is True, "the second pass found nothing new"

    def test_the_id_sample_is_bounded(self):
        report = saturation(known=[], got=[str(i) for i in range(200)], sample=5)
        assert report.new == 200
        assert len(report.new_ids) == 5


class TestOneBadUnitMustNotZeroTheCollection:
    """A single tile returned a non-JSON body, the exception left the sweep, and
    the run recorded 0 rows for two layers. In a results table, 0 rows reads as
    "this layer is empty" — and those two were the layers the analysis needed."""

    def test_a_failed_unit_degrades_the_result_and_names_itself(self):
        def fetch(unit):
            if unit == "bad":
                raise ValueError("not JSON")
            return [unit]

        result = collect(["one", "bad", "two"], fetch)
        assert result.values == [["one"], ["two"]]
        assert result.partial is True
        assert [u for u, _ in result.failed_units] == ["bad"]
        assert "not JSON" in result.failed_units[0][1]

    def test_a_clean_run_is_not_partial(self):
        result = collect([1, 2], lambda u: u)
        assert result.values == [1, 2]
        assert result.partial is False
        assert result.failed_units == []

    def test_every_unit_failing_is_partial_and_empty_not_a_silent_zero(self):
        result = collect([1, 2], lambda u: (_ for _ in ()).throw(OSError("refused")))
        assert result.values == []
        assert result.partial is True
        assert len(result.failed_units) == 2

    def test_the_report_line_is_loud_enough_to_survive_a_log(self):
        result = collect(["a", "b"], lambda u: u if u == "a" else 1 / 0)
        assert "PARTIAL" in result.report
        assert "1 of 2" in result.report

    def test_a_clean_report_says_so(self):
        assert "complete" in collect([1], lambda u: u).report

    def test_a_keyboard_interrupt_is_not_a_unit_failure(self):
        """An operator stopping the run is not a source-side hole, and burying
        it as one would make a cancelled crawl look like a partial source."""
        def fetch(unit):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            collect([1], fetch)


class TestAPositiveControlPrecedesAnyClaimOfAbsence:
    """A wrong date format returns the blank search form, which is
    indistinguishable from "no such record". The absence was reported for an
    hour before anyone queried a record already held."""

    def test_a_control_that_comes_back_passes_quietly(self):
        assert_finds(lambda q: ["row"], "GO 84")

    def test_a_control_that_returns_nothing_raises(self):
        with pytest.raises(ControlFailed) as excinfo:
            assert_finds(lambda q: [], "GO 84", describe="AP GO register")
        message = str(excinfo.value)
        assert "GO 84" in message
        assert "AP GO register" in message
        assert "query" in message.lower(), "the null is about the query, not the archive"

    def test_a_control_that_raises_is_also_a_failed_control(self):
        with pytest.raises(ControlFailed):
            assert_finds(lambda q: (_ for _ in ()).throw(RuntimeError("500")), "GO 84")

    def test_an_empty_generator_does_not_pass_the_control(self):
        """A lazy result has no length, so a size check accepts it without ever
        asking whether it yields anything. An empty generator would license the
        claim of absence the control exists to block."""
        with pytest.raises(ControlFailed):
            assert_finds(lambda q: (row for row in []), "GO 84")

    def test_a_generator_that_raises_on_first_use_is_a_failed_control(self):
        """A deferred request or a parse error surfaces at the first item, not at
        the call, so a lazy query fails after the guard unless it is consumed
        inside it."""
        def _lazy(q):
            def rows():
                raise RuntimeError("500 on first page")
                yield  # pragma: no cover - unreachable, marks this a generator
            return rows()

        with pytest.raises(ControlFailed):
            assert_finds(_lazy, "GO 84")

    def test_a_generator_that_yields_passes(self):
        assert_finds(lambda q: (row for row in ["one"]), "GO 84")

    def test_a_falsey_but_present_result_counts_as_found(self):
        """A count of zero rows is empty. A returned object that is merely
        falsey — an empty string in a single cell — is still a response."""
        assert_finds(lambda q: {"rows": 0}, "GO 84")

    def test_an_unbounded_generator_is_not_exhausted(self):
        """One yielded item passes the control. Materialising the whole stream
        would hang the guard, or pull a corpus, to learn what the first row
        already said."""
        pulled = []

        def _endless(q):
            def rows():
                n = 0
                while True:
                    pulled.append(n)
                    yield n
                    n += 1
            return rows()

        assert_finds(_endless, "GO 84")
        assert len(pulled) == 1, f"pulled {len(pulled)} items to check one"
