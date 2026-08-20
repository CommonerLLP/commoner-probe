"""Does this answer print the question number it was fetched for?

sansad.in serves the wrong document under the right URL. Fetched live on
2026-08-05 through this package's own guarded session:

    .../annex/188/AU2549_ma8WCQ.pdf -> 637,244 bytes, md5 5f643c38320b154069b947c919468956
                                       and the document prints QUESTION NO. 2594
    .../annex/188/AU2594_yXlrVE.pdf -> 424,629 bytes, md5 0ca7b4b8fe42f360f884ad5e4c1d8d6a
                                       and the document prints 2549

Both md5s match the copies the filer held, so nothing local mangled them. The
swap is live at source, re-fetching cannot repair it, and every consumer of
Sansad answers shares the exposure. Reported by zero-hour, 2026-08-04.

**The number is only in hand at acquisition.** Downstream the record is
flawless: the key parses, the subject is right, the text is a real government
reply, and a classifier returns a real label with real confidence. So the check
runs here, where the requested number and the document sit together.

**And it is not only a PDF problem.** zero-hour proved the same misassignment on
the LS list API's INLINE `answerText`, where `questionsFilePath` is blank and
there is no document to fall back on: `LS|U|1982|2000-03-07` (subject
INSURGENCY IN NORTH EAST REGION) prints QUESTION NO.1882 and answers about the
Hindi Salahakar Samiti. So the guard reads inline text too.

The reading is zero-hour's, copied with permission from
`zero_hour/corpus_integrity.py` (their PR #31) rather than re-derived. It has
already paid for two OCR traps and a citation trap that a fresh pattern would
re-learn one bug at a time.
"""

from __future__ import annotations

import re

#: Anchored on the word QUESTION so digit runs elsewhere cannot match. Bounded
#: to five digits because OCR turns the answer date into one long run: "TO BE
#: ANSWERED ON 3110712026" is 31|07|2026 with the separators eaten, and an
#: unbounded pattern reads a question number out of it. The punctuation class
#: after NO is deliberately generous: scans render it as "NO.", "No'", "NO:"
#: and "No,". A scan also mangles the letters — "QUESTION N. 7498" and
#: "QUESTION O. 7434" lose one, "QUESTION N0.6723" turns the O into a zero — so
#: `N0` must precede `N` in the alternation, or the pattern takes that zero as
#: the number.
_QUESTION_NUMBER = re.compile(
    r"QUESTION\s*(?:NO|NUMBER|N0|N|O)?['’.,:+\-]*\s*[†#*]*\s*(\d{1,5})\b",
    re.IGNORECASE,
)

#: A reply routinely cites ANOTHER question — "in reply to Starred Question
#: No.3", "answers to which were given against Unstarred Question No.3038".
#: Those numbers identify a different document, so reading one as this
#: document's own manufactures a mismatch out of a correctly-filed record.
#:
#: Every alternative here carries a word that points AWAY from this document.
#: "IN RESPECT OF ... QUESTION NO. X" is deliberately absent and must never be
#: added: an annexure header naming the question it belongs to is the document
#: identifying ITSELF, which is the signal this check exists to read.
_CITATION_CONTEXT = re.compile(
    r"(?:in\s+reply\s+to|referred\s+to\s+in\s+reply|in\s+repl(?:y|ies)\s+(?:of|to)|"
    r"repl(?:y|ied)\s+dated[^.]{0,40}?to|answers?\s+to\s+which|given\s+against|"
    r"reference\s+has\s+been\s+made|as\s+stated\s+in|as\s+informed\s+in|"
    r"similar|vide|raised\s+in|"
    # "with reference to THE ANSWER TO Unstarred Question 233 given in the
    # Rajya Sabha on the 1st August, 1995" — a 1996 RS reply opens by citing
    # the question it follows up. The preposition before `answer` is what
    # keeps a document's own header out: a header opens "ANSWER TO LOK SABHA
    # UNSTARRED QUESTION NO. 2549" and never has `to` in front of it.
    r"to\s+(?:the\s+)?answers?\s+to|"
    r"already\s+(?:been\s+)?(?:given|answered|replied)|part\s+of\s+the\s+reply)"
    # 200 characters, because a reply cites two questions in one sentence and
    # the phrase sits 96 characters before the second number. A period is
    # intra-token only between alphanumerics, so "No.3038" and "20.12.99" do
    # not break the window.
    r"\b(?:[^.]|\.(?=[0-9A-Za-z])){0,200}$",
    re.IGNORECASE,
)

_DETERMINER = r"(?:the|a|an|its|his|her|their|said|above|earlier|previous)"
_TYPE_WORDS = (r"(?:the\s+|said\s+|above\s+)?"
               r"(?:lok\s+sabha\s+|rajya\s+sabha\s+|starred\s+|un[- ]?starred\s+)*")
#: A bare "the reply to ... QUESTION NO. X" points away only when the document
#: is not heading its own annexure, and what separates the two is the annexure
#: word rather than the words "reply to". The `of` branch is narrower because
#: `of` also names the reply's author: "THE REPLY OF THE MINISTER TO LOK SABHA
#: UNSTARRED QUESTION NO. 2549" is a document naming itself.
_BARE_REPLY_CITATION = re.compile(
    rf"\b{_DETERMINER}\s+repl(?:y|ies)\s+to\b(?:[^.]|\.(?=[0-9A-Za-z])){{0,90}}$"
    rf"|\b{_DETERMINER}\s+repl(?:y|ies)\s+of\s+{_TYPE_WORDS}$",
    re.IGNORECASE,
)
#: The exception is a HEADER test, not a keyword test: the header word OPENS
#: its segment, after the text start, a markup tag, a sentence end or a
#: newline. Extracted PDF text prints its boilerplate on its own lines with no
#: period before the header.
#:
#: An answer header names its own document the same way, and it needs the
#: exception for the same reason. The citation window runs 200 characters back
#: and stops only at a period, and extracted PDF text prints its boilerplate on
#: periodless lines, so "…to the answer to Unstarred Question 233\nANSWER TO
#: LOK SABHA UNSTARRED QUESTION NO. 2549" let the citation suppress the header
#: below it.
#:
#: The answer branch is TIGHT on purpose: the header word, then only the house
#: and type words, then the number. A line may open with "Answer to" and still
#: cite somebody else — "Answer to the above matter was given in reply to
#: Unstarred Question No. 3038" — and that line must keep pointing away.
_SELF_NAMING = re.compile(
    r"(?:^|[>.\n\r]\s*)(?:statement|annexure|annex|appendix)\b[^.]{0,200}$"
    rf"|(?:^|[>.\n\r]\s*)answers?\s+to\s+{_TYPE_WORDS}$",
    re.IGNORECASE | re.MULTILINE,
)

#: The digits after `QUESTION NO` are a DATE component when a date tail
#: follows them. The pre-2000 Rajya Sabha answer prints its labels and values
#: out of order and gives the anchor no separator at all:
#:
#:     RAJYA SABHA
#:     QUESTION NO04.09.1996
#:     ANSWERED ON
#:     SLUMP IN THE TEXTILE INDUSTRY
#:     3082
#:
#: The number is 3082 and it sits two lines below, after the subject. Reading
#: `04` returned MISMATCH on 25 of 25 records in a zero-hour run on 0.15.1
#: on 2026-08-18, and the only two values across the run were the day components
#: of the two sitting dates. That layout is UNSUPPORTED rather than
#: misread: this pattern refuses the date, no other reading fires, and the
#: record returns `unreadable`. A mismatch on every row buries the swap the
#: guard exists to catch.
#:
#: "QUESTION NO. 2549 TO BE ANSWERED ON 04.08.2026" is unaffected. The date
#: there follows the words, not the captured digits.
#:
#: The tail must consume the WHOLE date, and the lookahead is what enforces it.
#: OCR also eats the space between a genuine number and its date, as in
#: "QUESTION NO. 2549/04/08/2026". Two separators after the captured digits
#: mean the digits are the day of a date. Three mean the digits are the number
#: and a whole date follows. Without the lookahead the pattern took "/04/08"
#: of the three, discarded a real number and returned `unreadable`.
_DATE_TAIL = re.compile(r"\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}(?![./-]?\d)")

#: `document_qno_status` values. `unreadable` is NOT a finding: 14 of 300
#: probe-fetched LS answer PDFs (4.7%) print no readable number, and a check
#: that flags those gets switched off within a week.
VERIFIED, MISMATCH, UNREADABLE = "verified", "mismatch", "unreadable"


def _points_away(prefix: str) -> bool:
    """Whether the text before a number cites ANOTHER document.

    The self-naming exception governs EVERY citation phrase. zero-hour measured
    it over 8,000 stored answers on 2026-08-16: 836 numbers sat inside a
    self-naming header and 831 carried the record's own key number. Suppressing
    them dropped readability from 15.0% to 4.7% and hid the finding.
    """
    if _SELF_NAMING.search(prefix):
        return False
    if _CITATION_CONTEXT.search(prefix):
        return True
    return bool(_BARE_REPLY_CITATION.search(prefix))


def printed_question_number(text: str) -> str | None:
    """The question number the document prints FOR ITSELF, or None.

    A number inside a citation is ignored, so a document that only ever cites
    other questions returns None — correctly, because it never states its own
    number.
    """
    for match in _QUESTION_NUMBER.finditer(text or ""):
        if _DATE_TAIL.match(text, match.end()):
            continue
        if _points_away(text[:match.start()]):
            continue
        return match.group(1)
    return None


def check_question_number(requested: str | None, text: str | None) -> tuple[str | None, str]:
    """`(printed number, status)` for an answer fetched as question `requested`.

    Returns `UNREADABLE` when the document states no number of its own, and
    both numbers plus `MISMATCH` when it states a different one. The caller
    keeps the document either way: it is a real government reply and belongs to
    SOME question, so a suppressed download would be harder to notice than a
    flagged one.

    Leading zeros are stripped before comparison. `07` and `7` are the same
    question typed two ways, and calling that a swap would bury the real ones.
    """
    printed = printed_question_number(text or "")
    if printed is None:
        return None, UNREADABLE
    if (requested or "").strip().lstrip("0") == printed.lstrip("0"):
        return printed, VERIFIED
    return printed, MISMATCH
