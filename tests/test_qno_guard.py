"""The answer must print the question number it was fetched for.

Reported by zero-hour: sansad.in serves 2594's document at the AU2549 URL, live
and reproducibly, and the same misassignment reaches the LS list API's
inline `answerText`, where there is no PDF at all.

The cases below are zero-hour's — their corpus, their traps — and the tests are
this repo's. No network.
"""

from __future__ import annotations

import pytest

from commoner_probe.qno_guard import (
    MISMATCH,
    UNREADABLE,
    VERIFIED,
    check_question_number,
    printed_question_number,
)


class TestTheDocumentNamesItself:
    def test_a_standard_answer_header_is_read(self):
        text = "GOVERNMENT OF INDIA\nLOK SABHA\nUNSTARRED QUESTION NO. 2549\nTO BE ANSWERED ON..."
        assert printed_question_number(text) == "2549"

    def test_an_annexure_header_is_the_document_naming_itself(self):
        """"IN RESPECT OF ... QUESTION NO. X" must never be read as a citation."""
        text = ("STATEMENT REFERRED TO IN REPLY TO PARTS (a) & (b) OF LOK SABHA "
                "UNSTARRED QUESTION NO. 5782 FOR 03.05.2000")
        assert printed_question_number(text) == "5782"

    def test_the_reply_of_the_minister_is_not_a_citation(self):
        text = "THE REPLY OF THE MINISTER TO LOK SABHA UNSTARRED QUESTION NO. 2549"
        assert printed_question_number(text) == "2549"


class TestACitationIsNotThisDocument:
    @pytest.mark.parametrize("text", [
        "The information was given in reply to Starred Question No.3 on 12.03.2001.",
        "answers to which were given against Unstarred Question No.3038 answered on 20.12.99",
        "The reply to Lok Sabha Starred Question No. 61 was laid on the Table.",
        "subsequent to the reply of Unstarred Question No.825 dated July 28, 1997",
    ])
    def test_a_pointing_away_phrase_suppresses_the_number(self, text):
        assert printed_question_number(text) is None

    def test_a_citation_after_a_self_naming_header_does_not_win(self):
        """The first number the document claims for itself is the answer."""
        text = ("ANNEXURE TO LOK SABHA UNSTARRED QUESTION NO. 646 "
                "which repeats details given in reply to Question No. 3038.")
        assert printed_question_number(text) == "646"


class TestTheTwoOcrTraps:
    """zero-hour's cases. Ours would have to re-learn them one bug at a time."""

    @pytest.mark.parametrize("rendered,expected", [
        ("QUESTION NO. 7498", "7498"),
        ("QUESTION No' 7498", "7498"),
        ("QUESTION NO: 7498", "7498"),
        ("QUESTION No, 7498", "7498"),
        ("QUESTION N. 7498", "7498"),
        ("QUESTION O. 7434", "7434"),
        ("QUESTION N0.6723", "6723"),
        ("Question No.+737", "737"),
    ])
    def test_a_mangled_marker_still_reads(self, rendered, expected):
        assert printed_question_number(rendered) == expected

    def test_a_mangled_date_is_not_a_question_number(self):
        """OCR eats the separators: 31|07|2026 becomes one ten-digit run, and an
        unbounded pattern reads a question number out of it."""
        text = "LOK SABHA UNSTARRED QUESTION NO. 2549\nTO BE ANSWERED ON 3110712026"
        assert printed_question_number(text) == "2549"

    def test_a_document_that_is_only_a_mangled_date_reads_nothing(self):
        assert printed_question_number("QUESTION TO BE ANSWERED ON 3110712026") is None


class TestTheVerdict:
    def test_the_matching_number_verifies(self):
        assert check_question_number("2549", "UNSTARRED QUESTION NO. 2549") == ("2549", VERIFIED)

    def test_the_swapped_pair_is_flagged_and_both_numbers_kept(self):
        """The live pair: AU2549 serves a document printing 2594."""
        printed, status = check_question_number("2549", "LOK SABHA STARRED QUESTION NO. 2594")
        assert (printed, status) == ("2594", MISMATCH)

    def test_an_unreadable_document_is_never_a_mismatch(self):
        """4.7% of probe-fetched LS answer PDFs print no readable number. A check
        that flags those gets switched off within a week."""
        assert check_question_number("2549", "This page intentionally left blank.") == (None, UNREADABLE)

    def test_empty_text_is_unreadable_not_mismatched(self):
        assert check_question_number("2549", "") == (None, UNREADABLE)
        assert check_question_number("2549", None) == (None, UNREADABLE)

    def test_a_leading_zero_is_not_a_swap(self):
        assert check_question_number("07", "QUESTION NO. 7")[1] == VERIFIED
        assert check_question_number("7", "QUESTION NO. 007")[1] == VERIFIED

    def test_a_missing_requested_number_cannot_verify(self):
        assert check_question_number(None, "QUESTION NO. 2549") == ("2549", MISMATCH)


class TestTheInlineCase:
    """The same defect where there is no PDF to read."""

    def test_an_inline_answer_that_prints_another_question_is_flagged(self):
        """`LS|U|1982|2000-03-07`, subject INSURGENCY IN NORTH EAST REGION,
        answering about the Hindi Salahakar Samiti under QUESTION NO.1882."""
        inline = ("<p>GOVERNMENT OF INDIA<br>LOK SABHA<br>UNSTARRED QUESTION NO.1882<br>"
                  "ANSWERED ON 07.03.2000<br>OFFICIAL LANGUAGE</p>")
        assert check_question_number("1982", inline) == ("1882", MISMATCH)

    def test_most_inline_answers_state_no_number_and_that_is_not_a_finding(self):
        """Measured by zero-hour over 8,000 stored answers: a number is readable
        in 15.0%. The other 85% must pass through silently."""
        inline = "<p>(a) and (b): The Government has taken several steps in this regard.</p>"
        assert check_question_number("1982", inline) == (None, UNREADABLE)


def test_a_pre_2000_rs_header_does_not_read_the_date_as_the_number():
    """Reported by a consumer repo, 2026-08-18. The pre-2000 Rajya Sabha answer prints its
    labels and values out of order: `QUESTION NO` runs straight into the
    ANSWERED-ON date with no separator, and the real number sits below the
    subject. A run of 25 records on 0.15.1 returned 25 mismatches, every one
    the day component of the sitting date.

    The layout is unsupported, so `unreadable` is the honest answer. A
    mismatch on every row buries the swap the guard exists to catch.
    """
    from commoner_probe.qno_guard import UNREADABLE, check_question_number

    text = (
        "GOVERNMENT OF INDIA\nMINISTRY OFTEXTILES\nRAJYA SABHA\n"
        "QUESTION NO04.09.1996\nANSWERED ON\nSLUMP IN THE TEXTILE INDUSTRY\n"
        "3082\n\nSHRI\n\nWill the Minister of TEXTILES be pleased to state :-\n")
    assert check_question_number("3082", text) == (None, UNREADABLE)


def test_a_slash_dated_header_is_also_not_a_number():
    from commoner_probe.qno_guard import UNREADABLE, check_question_number

    assert check_question_number(
        "165", "RAJYA SABHA\nQUESTION NO 12/03/1996\nANSWERED ON\n165\n"
    ) == (None, UNREADABLE)


def test_a_real_number_followed_by_a_date_still_reads():
    """The date must not swallow a genuine header. "QUESTION NO. 2549 TO BE
    ANSWERED ON 04.08.2026" states its number and then its date."""
    from commoner_probe.qno_guard import VERIFIED, check_question_number

    assert check_question_number(
        "2549", "LOK SABHA UNSTARRED QUESTION NO. 2549 TO BE ANSWERED ON 04.08.2026"
    ) == ("2549", VERIFIED)


def test_a_reply_citing_the_question_it_follows_up_keeps_its_own_number():
    """A 1996 Rajya Sabha reply opens by citing an earlier question: "... to
    the answer to Unstarred Question 233 given in the Rajya Sabha on the 1st
    August, 1995". Reading 233 as this document's own number manufactured a
    mismatch on a correctly-filed record.

    A document's own header is unaffected. It opens "ANSWER TO LOK SABHA
    UNSTARRED QUESTION NO. 2549" with no preposition in front.
    """
    from commoner_probe.qno_guard import (UNREADABLE, VERIFIED,
                                          check_question_number)

    cited = ("Will the Minister of POWER be pleased to state :to the answer to "
             "Unstarred Question 233 given in the Rajya Sabha on the 1st "
             "August, 1995 and state:\n(a) Whether there is any scheme")
    assert check_question_number("1175", cited) == (None, UNREADABLE)

    own = "ANSWER TO LOK SABHA UNSTARRED QUESTION NO. 2549"
    assert check_question_number("2549", own) == ("2549", VERIFIED)
