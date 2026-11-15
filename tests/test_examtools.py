"""validate / merge / stats on synthetic exam files (the old merge_exam.py, ported)."""

import shutil
from pathlib import Path

import pytest

from notes_cli import examtools
from notes_cli.cli import main
from notes_cli.examtools import ExamNotFound, find_exams, merge, stats, validate

OPTIONS = "a. first\nb. second\nc. third\nd. fourth\ne. fifth\n"


def exam_text(total=20, mcq=None, multi=8, *, present=None, code=0, maths=0, stems=None,
              options=OPTIONS, header=True, unlabelled=0):
    """An exam in the template's format.

    `total`/`mcq` go in the header; `present` questions are written (default `total`), the
    first `mcq` of them multiple choice (`multi` of those multi-answer, the last
    `unlabelled` without a Select label), the rest written. The first `code` questions
    get a code block, the next `maths` an inline formula.
    """
    mcq = total if mcq is None else mcq
    present = total if present is None else present
    pct = round(100 * mcq / total) if total else 0
    parts = ["# FM2 Practice Exam 1\n\n"]
    if header:
        parts.append(f"**Total Questions: {total}**\n"
                     f"**Format: {pct}% Multiple Choice ({mcq} questions), "
                     f"{100 - pct}% Written ({total - mcq} questions)**\n")
    parts.append("**Suggested Time: 40 minutes**\n\n---\n\n## Part A: Multiple Choice\n\n---\n\n")
    for k in range(1, present + 1):
        stem = (stems or {}).get(k, f"A process in state S{k} receives signal Y. "
                                    "What state does it move to?")
        body = f"{stem}\n\n"
        if k <= code:
            body += "```\nint f(int n) { return n + 1; }\n```\n\n"
        elif k <= code + maths:
            body += "Let \\(T(n) = 2T(n/2) + n\\).\n\n"
        if k <= mcq:
            if k > mcq - unlabelled:
                label = ""
            elif k <= multi:
                label = "Select **two**:\n\n"
            else:
                label = "Select one:\n\n"
            parts.append(f"**Question {k}**\n\n{body}{label}{options}\n---\n\n")
        else:
            parts.append(f"**Question {k}: Short Answer** (5 marks)\n\n{body}---\n\n")
    return "".join(parts)


def write_exam(folder, n, text, answers=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"exam{n}.md").write_text(text, encoding="utf-8")
    if answers is not None:
        (folder / f"exam{n}_answers.md").write_text(answers, encoding="utf-8")
    return folder / f"exam{n}.md"


# --- validate ----------------------------------------------------------------------------


def test_validate_all_mcq_pass(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=20, multi=8, code=4))

    check = validate(tmp_path, 1)

    assert check.errors == [] and check.warnings == []
    assert (check.n, check.questions, check.single, check.multi, check.code_blocks) == (1, 20, 12, 8, 4)
    assert (check.expected_total, check.expected_mcq) == (20, 20)


def test_validate_part_written_counts_mcq_only(tmp_path):
    # 12 MCQ + 8 written, 5 multi-answer: 5/12 >= 40% passes, although 5/20 would not.
    write_exam(tmp_path, 1, exam_text(total=20, mcq=12, multi=5, code=4))

    check = validate(tmp_path, 1)

    assert check.errors == []
    assert check.questions == 20                    # written "**Question 13: Short Answer**" counted
    assert (check.single, check.multi, check.expected_mcq) == (7, 5, 12)


def test_validate_multi_share_below_40_percent_of_mcq(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=20, mcq=12, multi=4, code=4))

    check = validate(tmp_path, 1)

    assert len(check.errors) == 1
    assert "Multi-answer" in check.errors[0] and "4/12" in check.errors[0]


def test_validate_exactly_40_percent_passes(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=10, mcq=5, multi=2, code=2))
    assert validate(tmp_path, 1).errors == []


def test_validate_all_written_skips_mcq_checks(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=6, mcq=0, multi=0, code=2))

    check = validate(tmp_path, 1)

    assert check.errors == [] and check.questions == 6


def test_validate_fewer_select_labels_than_mcq(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=20, mcq=12, multi=6, code=4, unlabelled=2))

    check = validate(tmp_path, 1)

    assert any("Select labels" in e and "12" in e for e in check.errors)


def test_validate_select_label_error_names_questions_and_forms(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=20, mcq=12, multi=6, code=4, unlabelled=2))

    [error] = [e for e in validate(tmp_path, 1).errors if "Select labels" in e]

    assert error.startswith("Only 10 Select labels for 12 multiple-choice questions; no Select "
                            "label on questions 11, 12. Label each")
    for form in ('"Select one:"', '"Select **two**:"', '"Select **two** options:"',
                 '"Select two:"', '"**Select two:**"', '"Select **all that apply**:"',
                 '"Select one or more:"', '"Select **one or more**:"'):
        assert form in error


def test_validate_theory_module_low_code_is_warning(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=20, multi=8, code=0))

    check = validate(tmp_path, 1)

    assert check.errors == []
    assert len(check.warnings) == 1 and "20%" in check.warnings[0]


def test_validate_maths_counts_as_notation(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=20, multi=8, code=1, maths=3))
    check = validate(tmp_path, 1)
    assert check.warnings == [] and check.maths == 3 and check.code_blocks == 1

    # A question with both code and maths is counted once.
    both = exam_text(total=10, multi=4, code=0).replace(
        "What state does it move to?\n\n",
        "What state does it move to?\n\n```\nx\n```\n\nWith \\(n\\) steps.\n\n", 1)
    write_exam(tmp_path, 2, both)
    check = validate(tmp_path, 2)
    assert (check.code_blocks, check.maths) == (1, 0)
    assert len(check.warnings) == 1 and "1/10" in check.warnings[0]


def test_validate_dollar_maths_counts_but_prices_do_not(tmp_path):
    stems = {1: "Let $T(n) = 2T(n/2) + n$. What is T?", 2: "It costs $5 and $10. Which?"}
    write_exam(tmp_path, 1, exam_text(total=5, multi=2, stems=stems))
    assert validate(tmp_path, 1).maths == 1


def test_validate_reports_lazy_stem_and_banned_option(tmp_path):
    # (Question 3 of exam_text has a code block, which makes its stem a scenario: the lazy
    # stem goes on question 10, which has none.)
    stems = {10: "Which of the following is true about deadlock?"}
    options = OPTIONS.replace("e. fifth", "e. None of the above")
    write_exam(tmp_path, 1, exam_text(total=20, multi=8, code=4, stems=stems, options=options))

    check = validate(tmp_path, 1)

    assert len(check.errors) == 2
    assert check.errors[0].startswith("1 lazy stem (rules 5 and 6") and \
        check.errors[0].endswith(": Question 10")
    # The message says what fixes it.
    assert "put" in check.errors[0] and "concrete scenario, code or data before the question" \
        in check.errors[0] and "a specific constrained case" in check.errors[0]
    assert check.errors[1].startswith("20 banned options (rule 3")
    assert "Question 1 option e, Question 2 option e" in check.errors[1]


def lazy_numbers(tmp_path, stem, **kw):
    write_exam(tmp_path, 1, exam_text(total=5, multi=2, stems={5: stem}, **kw))
    errors = [e for e in validate(tmp_path, 1).errors if "lazy stem" in e]
    return errors[0].rsplit(": ", 1)[1] if errors else None


@pytest.mark.parametrize("stem", [
    "Which of the following is true?",
    "Which of the following statements is correct?",
    "Which of the following are false?",
    "Which one of the following is true?",
    "Which of these is correct?",
    "Which is true?",
    "Which of the following statements are NOT true?",
    "**Which of the following is true?**",
    "Which of the following is true?\n\nSelect one:",
    "Which of the following is true? Select **two**:",
    "Which of the following is true about TCP's three-way handshake?",   # rule 5's "Bad"
    # "... statements about X ..." and close rewordings (review 2026-11-15).
    "Which of the following statements about deadlock is true?",
    "Which of the following statements about TCP's three-way handshake are correct?",
    "Which of the following statement(s) is/are correct?",
    "Which of the following claims regarding paging is false?",
    "Which of the following options is not true about virtual memory?",
    "Which of the following are correct statements about paging?",
    "Which statement about mutexes is incorrect?",
    "Select the true statement about mutexes.",
    "*Which of the following is true of a binary search tree?*",
    "Which of the following statements is correct?",        # nothing else in the question
    "Which of the following is true about paging with TLBs?",   # "with" + one word
    "Which of the following statements below is true?",
    "Which of the following is true about this topic?",     # not shared material
])
def test_lazy_stem_forms_flagged(tmp_path, stem):
    assert lazy_numbers(tmp_path, stem) == "Question 5"


@pytest.mark.parametrize("stem", [
    "A client sends SYN and receives SYN-ACK. Which of the following is true?",
    "Consider the code below.\n\n```\nx = fork()\n```\n\nWhich of the following is true?",
    "Which of the following is true about the code `fork(); fork();`? It runs on Linux.",
    "Which of the following is true?\n\n```\nint x = 1;\n```",
    "A process in state S receives signal Y. What state does it move to?",
    "Which of the following best describes a deadlock?",
    "Which of the following sorting algorithms is stable?",
    # A concrete example or constraint in the topic (review 2026-11-15).
    "Which of the following is true of a binary search tree built by inserting 4, 2, 6, 1, "
    "3, 5, 7 in that order?",
    "Which of the following is true in a system where two threads increment a shared "
    "counter without a lock?",
    "Which of the following statements about a heap with n = 7 elements is true?",
    "Which statement about the scheduler that the lecture described is correct?",
    "Which of the following is true about the relationship between processes and threads "
    "in modern operating systems generally?",
    "Which of the following is true when a page fault occurs during a TLB miss?",
    # Review round 2: shared material, and a constrained topic.
    "Which of the following is true regarding the diagram above?",
    "Which of the following statements about the schedule above is correct?",
    "Which of the following is true about the code below?",
    "Which of the following is true about the table shown?",
    "Which of the following is true of Dijkstra's algorithm on graphs with negative edges?",
    "Which statement about a mutex under heavy contention is correct?",
    "Which of the following is true about TCP without the slow start phase?",
    # Shared material named with "this/these/the resulting/the given", and "the scenario".
    "Which of the following statements about this schedule are correct?",
    "Which of the following statements about the scenario are true?",
    "Which of the following is true of the resulting tree?",
    "Which of the following is true about these transactions?",
    "Which of the following statements about the given code is correct?",
    # Scenario text, a code block or a table before the question, in the same question.
    "Two transactions T1 and T2 run under 2PL.\n\nWhich of the following statements is correct?",
    "```\nx = 1\n```\n\nWhich of the following statements is correct?",
    "| T1 | T2 |\n|----|----|\n| R(A) | W(A) |\n\nWhich of the following statements is "
    "correct?",
])
def test_lazy_stem_forms_allowed(tmp_path, stem):
    assert lazy_numbers(tmp_path, stem) is None


def test_lazy_stem_long_stem_skipped_quickly(tmp_path):
    """A stem over 400 characters is never lazy, and is not run through the (quadratic)
    lazy-stem pattern."""
    import time
    stem = "Which of the following is true about " + "a " * 5000 + "?"
    start = time.monotonic()
    assert lazy_numbers(tmp_path, stem) is None
    assert time.monotonic() - start < 2
    assert not examtools._lazy("Which is true about " + "x " * 250)


def test_lazy_stem_phrase_in_code_or_written_part_is_not_a_stem(tmp_path):
    text = exam_text(total=5, multi=2) + (
        "\n```\n**Question 9**\n\nWhich of the following is true?\n```\n")
    write_exam(tmp_path, 1, text)
    assert not any("lazy" in e for e in validate(tmp_path, 1).errors)


def banned_of(tmp_path, options, stem=None):
    """Question 4 gets `options` (and `stem`); the banned-option error's list, or None."""
    text = exam_text(total=5, multi=2, stems={4: stem} if stem else None)
    head, rest = text.split("**Question 4**", 1)
    q4, tail = rest.split("**Question 5**", 1)
    write_exam(tmp_path, 1, head + "**Question 4**" + q4.replace(OPTIONS, options)
               + "**Question 5**" + tail)
    errors = [e for e in validate(tmp_path, 1).errors if "banned option" in e]
    return errors[0].rsplit("): ", 1)[1] if errors else None


@pytest.mark.parametrize("option, banned", [
    ("e. All of the above", True),
    ("e. None of the above", True),
    ("e. **None of the above.**", True),
    ("e. None of the above is correct", True),
    ("e. Both a and c", True),
    ("e. Both (b) and (d)", True),
    ("e. Both options a and b", True),
    ("e. Both first and third", True),          # the texts of options a and c
    ("e. Both of the above", True),
    ("e) None of the above", True),
    # Emphasis and code markers do not hide it (review 2026-11-15).
    ("e. *None of the above*", True),
    ("e. _All of the above_", True),
    ("e. `None of the above`", True),
    # Other option forms.
    ("**e.** None of the above", True),
    ("**(e)** All of the above", True),
    ("(e) None of the above", True),
    ("e - None of the above", True),
    ("e: None of the above", True),
    ("- e. None of the above", True),
    ("e. None of\n   the above", True),            # continued on the next line
    ("e. All of these", True),
    # "Both X and Y" with a tail, or with bold letters.
    ("e. Both b and d are correct", True),
    ("e. Both a and c only", True),
    ("e. Both **a** and **c**", True),
    ("e. Both (a) and (c) are true", True),
    ("e. Both TCP and UDP use port numbers", False),
    ("e. Both a and b are even", False),
    ("e. None of the above-mentioned methods terminates", False),
    ("e. Calls all of the above functions in order", False),
    ("e. Neither a nor c", False),
    # Review round 2: the nouns only as "all/none of the <noun>", and only on their own.
    ("e. All of the options", True),
    ("e. None of the statements", True),
    ("e. None of the answers apply", True),
    ("e. All of the choices are correct", True),
    ("e. None of the options of these", True),
    ("e. None of the statements is true.", True),
    ("e. All the statements are executed in a single transaction until COMMIT.", False),
    ("e. All the options are parsed before `optind` is advanced", False),
    ("e. None of the options are inherited by the child process", False),
    ("e. All the answers are stored in the cache", False),
    ("e. All the choices are logged", False),
    ("e. All of the statements run inside one transaction", False),
    ("e. None of the previous", True),
    ("e. All of the preceding are correct", True),
    ("e. All the others", True),
    ("e. None of these", True),
    # "Both" with backticked identifiers, or with letters the question does not use.
    ("e. Both `a` and `b` are incremented", False),
    ("e. Both `a` and `b`", False),
    ("e. Both f and g", False),
    ("e. Both a and c", True),
])
def test_banned_option_forms(tmp_path, option, banned):
    options = OPTIONS.replace("e. fifth", option)
    assert banned_of(tmp_path, options) == ("Question 4 option e" if banned else None)


@pytest.mark.parametrize("options, banned", [
    ("i. first\nii. Both `a` and `c`\n", False),             # no option letters at all: code
    (OPTIONS.replace("e. fifth", "e. fifth\n\nf] Both `a` and `c`"), False),
    (OPTIONS.replace("e. fifth", "e. fifth\n\nf] Both a and c"), True),
    ("(i) first\n(ii) Both a and c\n", True),                # no parsed options: any letters
])
def test_both_letters_in_unparsed_lines(tmp_path, options, banned):
    assert (banned_of(tmp_path, options) is not None) == banned


def test_banned_phrases_outside_options_are_fine(tmp_path):
    stem = ("None of the above-mentioned processes holds a lock, and all of the above is "
            "prose. Explanation: none of the above.\n\n```\n// None of the above\n```\n\n"
            "Which process runs first?")
    assert banned_of(tmp_path, OPTIONS, stem=stem) is None


def test_banned_option_fallback_when_options_not_in_template_form(tmp_path):
    odd = "(i) first\n(ii) None of the above\n"
    text = exam_text(total=3, multi=2).replace(OPTIONS, odd)
    write_exam(tmp_path, 1, text)
    errors = [e for e in validate(tmp_path, 1).errors if "banned" in e]
    assert errors and "options not in the 'a. ...' form" in errors[0]
    assert 'Question 1 line "(ii) None of the above"' in errors[0]


@pytest.mark.parametrize("options", [
    OPTIONS + "\n- None of the above\n",                 # an extra bullet after the options
    OPTIONS.replace("e. fifth", "e. fifth\n\ng) All of the above"),
    OPTIONS.replace("e. fifth", "e. fifth\n\nf] Both a and c"),
    "i. first\nii. second\niii. None of the above\n",
])
def test_banned_phrase_not_hidden_by_parsed_options(tmp_path, options):
    """A banned option in a form that does not parse is still found, even when the other
    options of the question parsed."""
    assert banned_of(tmp_path, options) is not None


def test_banned_phrase_unlabelled_question_odd_options(tmp_path):
    # No Select label and options in another form: the option-like lines are searched.
    text = exam_text(total=5, multi=2, unlabelled=1)
    assert text.count("Select") == 4
    text = text.replace("**Question 5**\n\nA process in state S5 receives signal Y. What "
                        "state does it move to?\n\n" + OPTIONS,
                        "**Question 5**\n\nA process in state S5 receives signal Y. What "
                        "state does it move to?\n\n(1) first\n(2) None of the above\n")
    write_exam(tmp_path, 1, text)
    assert any("Question 5 line" in e for e in validate(tmp_path, 1).errors if "banned" in e)


def test_heading_before_stem_still_checked(tmp_path):
    # A "####" heading before the stem, and the rest of a Markdown question heading's line.
    for stem in ("#### Scenario\n\nWhich of the following is true?",
                 "### Part 1\n\n**Which of the following is true?**"):
        assert lazy_numbers(tmp_path, stem) == "Question 5"
    text = exam_text(total=5, multi=2, stems={5: "Which of the following is true?"}).replace(
        "**Question 5**", "### Question 5 — Deadlock")
    write_exam(tmp_path, 1, text)
    assert any(e.endswith(": Question 5") for e in validate(tmp_path, 1).errors
               if "lazy stem" in e)


def test_lazy_and_banned_fallback_without_question_headings(tmp_path):
    text = ("**Total Questions: 1**\n**Format: 100% Multiple Choice (1 questions)**\n\n"
            "Q1. Which of the following is true?\nSelect one:\na. x\nb. None of the above\n")
    write_exam(tmp_path, 1, text)
    errors = validate(tmp_path, 1).errors
    assert any(e.startswith("1 lazy stems found") and "whole file" in e for e in errors)
    assert any(e.startswith("1 banned 'All/None of the above' options found") for e in errors)


def test_validate_question_count_from_header(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=30, multi=12, code=6, present=25))
    check = validate(tmp_path, 1)
    assert any("25 questions found" in e and "30" in e for e in check.errors)

    # No hardcoded minimum of 20: a 15-question exam whose header says 15 passes.
    write_exam(tmp_path, 2, exam_text(total=15, multi=6, code=3))
    assert validate(tmp_path, 2).errors == []


def test_validate_unparseable_header(tmp_path):
    write_exam(tmp_path, 1, exam_text(total=20, multi=8, code=4, header=False))

    check = validate(tmp_path, 1)

    assert any("**Total Questions: N**" in e for e in check.errors)
    assert any("**Format: X% Multiple Choice (M questions)" in e for e in check.errors)
    assert check.expected_total is None and check.expected_mcq is None

    # The errors show the exact template form to use.
    assert any("**Total Questions: [TOTAL]**" in e for e in check.errors)
    assert any("**Format: [X]% Multiple Choice ([MCQ_COUNT] questions), [Y]% Written "
               "([WRITTEN_COUNT] questions)**" in e for e in check.errors)

    # Only the Format line missing: the count check still runs on the Total line.
    text = "\n".join(line for line in exam_text(total=20, multi=8, code=4).splitlines()
                     if not line.startswith("**Format:")) + "\n"
    write_exam(tmp_path, 2, text)
    check = validate(tmp_path, 2)
    assert len(check.errors) == 1 and "**Format:" in check.errors[0]
    assert "not found" in check.errors[0]
    assert check.expected_total == 20

    # A Format line without a readable multiple-choice count.
    text = exam_text(total=20, multi=8, code=4).replace(
        "**Format: 100% Multiple Choice (20 questions), 0% Written (0 questions)**",
        "**Format: mostly multiple choice**")
    write_exam(tmp_path, 3, text)
    check = validate(tmp_path, 3)
    assert len(check.errors) == 1 and "no readable multiple-choice count" in check.errors[0]


@pytest.mark.parametrize("total_line, format_line, mcq", [
    ("**Total Questions: 20**", "**Format: 60% Multiple Choice (12 questions), 40% Written (8 questions)**", 12),
    ("**Total Questions**: 20", "**Format**: 60% Multiple Choice (12 questions), 40% Written (8 questions)", 12),
    ("**Total Questions:** 20", "**Format:** 60% Multiple Choice (12 questions), 40% Written (8 questions)", 12),
    ("  **Total Questions: 20**", "  **Format: 60% Multiple Choice (12 questions), 40% Written (8 questions)**", 12),
    ("- **Total Questions: 20**", "- **Format: 60% Multiple Choice (12 MCQ), 40% Written (8 questions)**", 12),
    ("* **Total Questions: 20**", "* **Format: 60% MCQ (12 questions), 40% Written (8 questions)**", 12),
    ("**Total Questions: 20**", "**Format: 60% multiple-choice (12 question), 40% written (8)**", 12),
    ("**Total Questions: 20**", "**Format: 100% Written (20 questions)**", 0),
    ("**Total Questions: 20**", "**Format: 0% Multiple Choice (0 questions), 100% Written (20 questions)**", 0),
])
def test_validate_header_variants(tmp_path, total_line, format_line, mcq):
    text = exam_text(total=20, mcq=mcq, multi=min(mcq, 5), code=4)
    lines = text.splitlines()
    lines[2], lines[3] = total_line, format_line
    write_exam(tmp_path, 1, "\n".join(lines) + "\n")

    check = validate(tmp_path, 1)

    assert check.errors == []
    assert (check.expected_total, check.expected_mcq) == (20, mcq)


@pytest.mark.parametrize("heading", [
    "**Question {k}: Short Answer** (5 marks)",
    "**Question {k} (5 marks)**",
    "**Question {k} — Short Answer**",
    "**Question {k} – Short Answer**",
    "**Question {k} - Short Answer**",
    "### Question {k}",
    "## Question {k}: Proof (6 marks)",
])
def test_validate_written_question_heading_forms(tmp_path, heading):
    text = exam_text(total=10, mcq=5, multi=2, code=2)
    for k in range(6, 11):
        text = text.replace(f"**Question {k}: Short Answer** (5 marks)", heading.format(k=k))
    write_exam(tmp_path, 1, text)

    check = validate(tmp_path, 1)

    assert check.questions == 10 and check.errors == []
    assert "10" in stats(tmp_path).splitlines()[4].split()


def test_select_labels_ignore_sql_in_code_and_prose(tmp_path):
    # 20 MCQ, only 4 genuinely multi-answer: the old pattern counted the SQL below as
    # multi-answer labels (and reported a pass).
    stems = {
        5: "Given:\n\n```sql\nSELECT allocation_id FROM allocations;\nSELECT ALL name FROM t;\n"
           "Select **two** columns\n```\n\nWhat does the query return?",
        6: "The query `SELECT allocation_id, ALL FROM x` runs. What happens?",
        7: "An analyst wants to select all rows; she writes SELECT all_rows FROM r. Select "
           "one or more tables? What fails?",
        8: "Inline code ``Select **two**: x`` with double backticks. What fails?",
        9: "~~~\nSELECT one: x\n~~~\n\nWhat fails?",
    }
    write_exam(tmp_path, 1, exam_text(total=20, multi=4, code=4, stems=stems))

    check = validate(tmp_path, 1)

    assert (check.single, check.multi) == (16, 4)
    assert any("Multi-answer questions: 4/20 = 20%" in e for e in check.errors)


@pytest.mark.parametrize("label, kind", [
    ("Select one:", "single"),
    ("Select **one**:", "single"),
    ("Select one or more:", "multi"),
    ("Select **two**:", "multi"),
    ("Select **all** that apply:", "multi"),
    ("Select all that apply:", "multi"),
    ("Select **three:**", "multi"),
    ("Select **two** options:", "multi"),
    ("Select **all that apply**:", "multi"),
    ("Select **one or more**:", "multi"),
    ("**Select two:**", "multi"),
    ("Select two:", "multi"),
    ("**Select one:**", "single"),
    ("Select **one** option:", "single"),
    ("SELECT two:", None),
    ("Select two", None),
    ("SELECT ALL:", None),
    ("select one:", None),
    ("Select all rows", None),
])
def test_select_label_forms(tmp_path, label, kind):
    text = exam_text(total=1, multi=0).replace("Select one:", label)
    write_exam(tmp_path, 1, text)
    check = validate(tmp_path, 1)
    assert (check.single, check.multi) == {"single": (1, 0), "multi": (0, 1), None: (0, 0)}[kind]


def test_validate_header_more_mcq_than_total(tmp_path):
    text = exam_text(total=10, multi=4, code=2).replace("(10 questions)", "(12 questions)")
    write_exam(tmp_path, 1, text)
    assert any("12 multiple-choice" in e for e in validate(tmp_path, 1).errors)


def test_validate_missing_exam_raises(tmp_path):
    with pytest.raises(ExamNotFound, match="exam7.md"):
        validate(tmp_path, 7)


# --- find_exams, merge, stats ------------------------------------------------------------


def test_find_exams_naming(tmp_path):
    for name in ("exam1.md", "exam10.md", "exam2.md", "exam1_answers.md", "exam1_full.md",
                 "exam_template.md", "concept_matrix.md", "exam01.md", "Exam3.md", "exam4.txt"):
        (tmp_path / name).write_text("x")
    (tmp_path / "exam5.md").mkdir()

    assert find_exams(tmp_path) == [1, 2, 10]
    assert find_exams(tmp_path / "missing") == []


def test_merge_and_stats(tmp_path):
    q1 = exam_text(total=20, multi=8, code=4)
    write_exam(tmp_path, 1, q1, answers="# Answers\n**Q1:** b\n")
    q2 = exam_text(total=12, mcq=8, multi=4, code=3)
    write_exam(tmp_path, 2, q2)
    (tmp_path / "exam_template.md").write_text("template")

    out1 = merge(tmp_path, 1)
    out2 = merge(tmp_path, 2)

    assert out1 == tmp_path / "exam1_full.md"
    assert out1.read_text("utf-8") == q1 + "\n\n---\n\n" + "# Answers\n**Q1:** b\n"
    assert out2.read_text("utf-8") == q2                      # no answers file: questions only
    assert find_exams(tmp_path) == [1, 2]                     # _full files are not exams

    text = stats(tmp_path)
    lines = text.splitlines()
    assert lines[0] == "Found 2 exam(s): [1, 2]"
    assert lines[2] == f"{'Exam':<8} {'Chars':>8} {'~Tokens':>8} {'Questions':>10} {'Has Answers':>12}"
    row1 = next(line for line in lines if line.startswith("exam1 "))
    row2 = next(line for line in lines if line.startswith("exam2 "))
    assert row1.split() == ["exam1", f"{len(q1):,}", f"{len(q1) // 4:,}", "20", "YES"]
    assert row2.split() == ["exam2", f"{len(q2):,}", f"{len(q2) // 4:,}", "12", "NO"]
    assert lines[-1].split() == ["Total", f"{len(q1) + len(q2):,}", f"{(len(q1) + len(q2)) // 4:,}"]


def test_stats_inline_answers_and_no_exams(tmp_path):
    assert stats(tmp_path) == "No exam files found."
    write_exam(tmp_path, 3, exam_text(total=5, multi=2) + "\n# ANSWERS\n")
    assert "INLINE" in stats(tmp_path)


def test_merge_missing_exam_raises(tmp_path):
    with pytest.raises(ExamNotFound):
        merge(tmp_path, 4)
    assert list(tmp_path.iterdir()) == []


def test_merge_overwrites_its_own_output(tmp_path):
    write_exam(tmp_path, 1, "questions\n", answers="answers\n")
    (tmp_path / "exam1_full.md").write_text("stale")
    assert merge(tmp_path, 1).read_text() == "questions\n\n\n---\n\nanswers\n"
    assert (tmp_path / "exam1.md").read_text() == "questions\n"


def test_merge_refuses_symlinked_folder(tmp_path):
    real = tmp_path / "elsewhere"
    write_exam(real, 1, "questions\n")
    link = tmp_path / "Practice Exams"
    link.symlink_to(real)
    with pytest.raises(examtools.ExamDirError, match="not a plain folder"):
        merge(link, 1)
    assert sorted(p.name for p in real.iterdir()) == ["exam1.md"]


def test_merge_preserves_bytes(tmp_path):
    raw = "Größe → \\(x\\)\n".encode() + b"\xff odd byte\n"
    (tmp_path / "exam1.md").write_bytes(raw)
    (tmp_path / "exam1_answers.md").write_bytes(b"answers\n")
    assert merge(tmp_path, 1).read_bytes() == raw + b"\n\n---\n\nanswers\n"


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def exams(cfg):
    folder = cfg.module_dir("FM2") / "Practice Exams"
    write_exam(folder, 1, exam_text(total=20, multi=8, code=4), answers="answers 1\n")
    write_exam(folder, 2, exam_text(total=20, multi=2, code=4))       # 10% multi: error
    return folder


def run(cfg, *argv):
    return main(["--data", str(cfg.data_root), "exam", *argv])


def test_cli_exam_validate_exit_codes(cfg, exams, capsys):
    assert run(cfg, "validate", "fm2", "all") == 1
    out = capsys.readouterr().out
    assert "exam1.md — All checks passed" in out
    assert "exam2.md — 1 error(s)" in out and "Multi-answer" in out
    assert "2 exams checked: 1 passed, 1 with errors" in out

    assert run(cfg, "validate", "FM2", "1") == 0
    assert run(cfg, "validate", "FM2", "2") == 1
    assert run(cfg, "validate", "FM2") == 1                     # N defaults to all
    capsys.readouterr()

    # A missing exam, or no exams at all: one line on stderr, exit 1.
    assert run(cfg, "validate", "FM2", "7") == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "exam7.md" in err and "Traceback" not in err
    assert run(cfg, "validate", "IAS", "all") == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "no exams" in err and "Practice Exams" in err


def test_cli_exam_validate_warning_only_exits_0(cfg, capsys):
    write_exam(cfg.module_dir("FM2") / "Practice Exams", 1, exam_text(total=20, multi=8))
    assert run(cfg, "validate", "FM2", "1") == 0
    out = capsys.readouterr().out
    assert "passed with 1 warning(s)" in out and "warning:" in out


@pytest.mark.parametrize("argv", [
    ["validate"],
    ["validate", "FM2", "0"],
    ["validate", "FM2", "-1"],
    ["validate", "FM2", "x"],
    ["validate", "FM2", "1.5"],
    ["validate", "FM2", "1", "2"],
    ["validate", "FM2", "1", "--mcq", "50"],
    ["merge"],
    ["merge", "FM2", "none"],
    ["merge", "FM2", "all", "--questions", "10"],
    ["stats"],
    ["stats", "FM2", "1"],
])
def test_cli_exam_tool_usage_errors_exit_2(cfg, exams, capsys, argv):
    before = sorted(p.name for p in exams.iterdir())
    assert run(cfg, *argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("notes: ") and err.count("\n") == 1
    assert sorted(p.name for p in exams.iterdir()) == before


def test_cli_exam_merge_and_stats(cfg, exams, capsys, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)

    assert run(cfg, "merge", "FM2", "all") == 0
    out = capsys.readouterr().out
    assert "exam1_full.md created" in out
    assert "exam2_answers.md not found, using questions only" in out
    assert "pandoc is not installed" in out
    assert (exams / "exam1_full.md").read_text("utf-8").endswith("\n\n---\n\nanswers 1\n")
    assert (exams / "exam2_full.md").is_file()

    assert run(cfg, "merge", "FM2", "1") == 0
    assert run(cfg, "merge", "FM2", "3") == 1
    capsys.readouterr()

    assert run(cfg, "stats", "FM2") == 0
    out = capsys.readouterr().out
    assert out.startswith("Found 2 exam(s): [1, 2]")


def test_cli_exam_merge_names_pandoc_command_when_installed(cfg, exams, capsys, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert run(cfg, "merge", "FM2", "2") == 0
    out = capsys.readouterr().out
    assert "pandoc 'exam2_full.md' -o 'exam2_full.pdf'" in out
    assert "not installed" not in out


def test_cli_exam_merge_all_names_real_files_in_pandoc_hint(cfg, exams, capsys, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert run(cfg, "merge", "FM2", "all") == 0
    out = capsys.readouterr().out
    assert "pandoc 'exam1_full.md' -o 'exam1_full.pdf', and the same for exam2_full.md" in out
    assert "<N>" not in out


def test_cli_exam_merge_refuses_symlinked_folder(cfg, tmp_path, capsys):
    real = tmp_path / "elsewhere"
    write_exam(real, 1, exam_text(total=5, multi=2))
    module = cfg.module_dir("FM2")
    module.mkdir(parents=True)
    (module / "Practice Exams").symlink_to(real)

    assert run(cfg, "merge", "FM2", "all") == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "not a plain folder" in err
    assert sorted(p.name for p in real.iterdir()) == ["exam1.md"]
    assert run(cfg, "validate", "FM2", "1") == 0          # reading through it is fine


def test_cli_exam_stats_and_merge_without_exams(cfg, capsys):
    assert run(cfg, "stats", "FM2") == 0
    assert "No exam files found" in capsys.readouterr().out
    assert run(cfg, "merge", "FM2", "all") == 1
    assert "no exams" in capsys.readouterr().err
    assert not (cfg.module_dir("FM2") / "Practice Exams").exists()


def test_exam_tools_module_exports():
    assert examtools.EXAM_DIR == "Practice Exams"


# --- finished: a real answer key (review 2026-11-15) ---------------------------------------

AUDIT = ("\n---\n\n## Answer Key Distribution Audit\n\n**Single-answer questions (12):**\n"
         "- a: 3, b: 2, c: 3, d: 2, e: 2\n")


def answer_key(total=20, form="**Q{k}:** b — Because. (Lecture 1)\n  - a: Confuses X.\n",
               audit=AUDIT, skip=()):
    """An answers file in the template's format, with an entry for questions 1..total."""
    body = "".join(form.format(k=k) + "\n" for k in range(1, total + 1) if k not in skip)
    return "# FM2 Practice Exam 1 — Answer Key\n\n## Part A: Multiple Choice\n\n" + body + audit


def finished_errors(tmp_path, answers, questions=None):
    write_exam(tmp_path, 1, questions or exam_text(total=20, multi=8, code=4), answers=answers)
    return examtools.finished_check(tmp_path, 1).errors


def test_finished_complete_answer_key_passes(tmp_path):
    assert finished_errors(tmp_path, answer_key()) == []


@pytest.mark.parametrize("form", [
    "**Q{k}:** b — x\n",
    "**Q{k}**: b\n",
    "Q{k}: b\n",
    "Q{k}. b\n",
    "**Q {k}:** b\n",
    "## Q{k}\n\nb\n",
    "### Question {k}\n\nb\n",
    "**Question {k}:** b\n",
    "**Question {k}: Short Answer** (5 marks)\n\n**Model Answer:** x\n",
    "**Q{k}: Short Answer** (5 marks)\n\n**Model Answer:** x\n",
    "- **Q{k}:** b\n",
    "Answer {k}: b\n",
    "**Q{k}.** b\n",
    "Q{k} — b\n",
])
def test_finished_answer_entry_forms(tmp_path, form):
    assert finished_errors(tmp_path, answer_key(form=form)) == []


@pytest.mark.parametrize("form", ["{k}. b — x\n", "**{k}.** b\n", "{k}) b\n",
                                  "Q{k} is about paging\n", "Questions {k}: b\n"])
def test_finished_bare_numbers_are_not_answer_entries(tmp_path, form):
    """Review round 2: a numbered-list line ("2.", "3.") is not an answer entry, so an
    explanation's list cannot stand in for missing answers."""
    errors = finished_errors(tmp_path, answer_key(form=form))
    assert any("has no answer for questions 1, 2" in e for e in errors), errors


def test_finished_answer_key_total_capped(tmp_path):
    # No question headings: the header's total is used, capped (no huge range in memory).
    questions = "**Total Questions: 999999999999**\n**Format: 100% Multiple Choice (3 questions)**\n"
    write_exam(tmp_path, 1, questions, answers=answer_key(3))
    errors = examtools.finished_check(tmp_path, 1).errors
    missing = next(e for e in errors if "has no answer" in e)
    assert "questions 4, 5, 6" in missing and "and 485 more" in missing


def test_finished_written_questions_need_answers_too(tmp_path):
    questions = exam_text(total=10, mcq=5, multi=2, code=2)
    answers = answer_key(total=5) + "".join(
        f"\n**Q{k}: Short Answer** (5 marks)\n\n**Model Answer:** x\n" for k in range(6, 10))
    errors = finished_errors(tmp_path, answers, questions)
    assert errors == ["exam1_answers.md has no answer for question 10: give every question "
                      "its own entry, as in exam_template.md (**Q1:** b — …)"]


@pytest.mark.parametrize("answers, wanted", [
    ("TODO\n", "has no answer for questions 1, 2, 3"),
    ("# Answers\n**Q1:** b\n", "has no answer for questions 2, 3, 4"),
    (answer_key(skip=(7, 13)), "has no answer for questions 7, 13:"),
    (answer_key().replace("**Q4:** b — Because.", "**Q4:** TODO"),
     "still has a TODO, TBD or FIXME placeholder on line 14: finish those answers"),
    (answer_key(audit=""), "has no Answer Key Distribution Audit section"),
    ("  \n", "exam1_answers.md is empty"),
])
def test_finished_incomplete_answer_key_fails(tmp_path, answers, wanted):
    errors = finished_errors(tmp_path, answers)
    assert any(wanted in e for e in errors), errors


def test_finished_answer_key_ignores_code(tmp_path):
    # Entries and TODOs inside code blocks do not count.
    code = "```\n" + "".join(f"Q{k}: x\n" for k in range(1, 21)) + "// TODO\n```\n"
    errors = finished_errors(tmp_path, code + AUDIT)
    assert any("has no answer for questions 1, 2" in e for e in errors)
    assert not any("TODO" in e for e in errors)
    assert finished_errors(tmp_path, answer_key() + "\n```\n// TODO: write\n```\n") == []


def with_line(text, k=4):
    """answer_key() with line `text` added after question k's entry."""
    return answer_key().replace(f"**Q{k + 1}:**", f"{text}\n\n**Q{k + 1}:**", 1)


@pytest.mark.parametrize("answers, line", [
    (answer_key().replace("**Q4:** b — Because.", "**Q4:** TODO"), 14),
    (answer_key().replace("**Q4:** b — Because.", "**Q4:** b — TBD."), 14),
    (answer_key().replace("**Q4:** b — Because.", "**Q4:** b — FIXME: check the slide"), 14),
    (answer_key().replace("**Q4:** b — Because.", "**Q4:** b — Because [TODO]."), 14),
    (answer_key().replace("**Q4:** b — Because.", "**Q4:** b — [TBD: reference]"), 14),
    (with_line("  - c: TODO"), 17),
    (with_line("TODO: write the rest"), 17),
    (with_line("- TODO: c and d"), 17),
    (with_line("**Model Answer:** TBD"), 17),
    (with_line("| TODO |"), 17),                                  # a row of one cell
    (with_line("| Edge cases | TODO: marks | Full: handles all |"), 17),
])
def test_finished_todo_placeholders_fail_with_line_number(tmp_path, answers, line):
    errors = finished_errors(tmp_path, answers)
    assert any(f"placeholder on line {line}:" in e for e in errors), errors


@pytest.mark.parametrize("text", [
    "  - c: The TODO comment is self-admitted technical debt.",
    "  - c: Requirements marked TBD are incomplete, so they cannot be tested.",
    "  - d: FIXME and TODO markers are found by static analysis.",
    "  - e: Confuses a todo list with a backlog.",
    "  - e: `TODO:` comments are ignored by the compiler.",
    # A table whose row has other cells: the word is content, not a placeholder.
    "| Marker | Meaning |\n|--------|---------|\n| TODO | unfinished work |\n| FIXME | known bug |",
    "| Edge cases | TODO | Full: handles all |",
])
def test_finished_todo_in_prose_passes(tmp_path, text):
    assert finished_errors(tmp_path, with_line(text)) == []


@pytest.mark.parametrize("audit, ok", [
    (AUDIT, True),
    ("\n**Answer Key Distribution Audit**\n\n- a: 3\n", True),
    ("\n### 4. answer key distribution audit\n", True),
    ("\n**Q21:** a — An audit log records every change.\n", False),
    ("\n## Audit\n\n- a: 3\n", False),
    ("\nThe Answer Key Distribution Audit is below.\n", False),
    ("\n**Note:** see the Answer Key Distribution Audit in exam 1\n", False),
])
def test_finished_audit_section_needs_its_heading(tmp_path, audit, ok):
    errors = finished_errors(tmp_path, answer_key(audit=audit))
    assert (not any("Audit" in e for e in errors)) == ok, errors


TEMPLATE = (Path(examtools.__file__).parent / "prompts" / "exam_template.md").read_text("utf-8")


def test_template_placeholders_derived_from_template_and_prompt():
    words, phrases = examtools.template_placeholders()
    assert {"[Option]", "[count]", "[TOTAL]", "[MCQ_COUNT]", "[letters]"} <= words
    assert {"[Pass/Fail]", "[one sentence]", "[misconception name]",
            "[1-2 sentence explanation. Reference: Lecture X, slide Y.]",
            "[1-2 sentence explanation of why b is correct, with lecture reference]",
            "[code — 5-15 lines]"} <= phrases
    assert not ({"[X]", "[N]", "[n]", "[%]", "[N+1]", "[A]"} & (words | phrases))
    # The template itself is full of them.
    found = {token for token, _ in examtools.placeholder_lines(TEMPLATE)}
    assert {"[Option]", "[count]", "[Pass/Fail]", "[X]%", "Q[n]", "[X] marks", "Q[N+1]",
            "| **[X]** |"} <= found


@pytest.mark.parametrize("where, text, wanted", [
    ("questions", "e. [Option]", "exam1.md still has template placeholders: [Option] (line"),
    ("questions", "Consider the following [code/system/configuration].", "[code/system/"),
    ("questions", "```\n[code — 5-15 lines]\n```", "[code — 5-15 lines]"),
    ("answers", "- a: [count], b: [count]", "[count] (line 17)"),
    ("answers", "- Consecutive: max 2 same letter. [Pass/Fail].", "[Pass/Fail] (line 17)"),
    ("answers", "  - a: [misconception name]. [one sentence max]", "[misconception name]"),
    ("answers", "**Q[n]:** b", "Q[n] (line 17)"),
    ("answers", "- Multi-answer: 8/20 = [%]% (>=40%). Pass.", "[%]% (line 17)"),
    ("answers", "**Multi-answer questions (8):** Q[n]([letters]), Q[n]([letters])",
     "Q[n] (line 17)"),
    ("answers", "**Q[N+1]: Short Answer** (5 marks)", "Q[N+1] (line 17)"),
    ("answers", "| **Total** | **[X]** | |", "| **[X]** | (line 17)"),
])
def test_template_placeholders_left_fail(tmp_path, where, text, wanted):
    questions = exam_text(total=20, multi=8, code=4)
    answers = answer_key()
    if where == "questions":
        questions = questions.replace("e. fifth", text, 1)
        errors = finished_errors(tmp_path, answers, questions)
    else:
        errors = finished_errors(tmp_path, with_line(text))
    assert any(wanted in e for e in errors), errors


@pytest.mark.parametrize("text", [
    "  - c: `arr[i]` is read before it is written.",
    "  - c: arr[i] and arr[count] are swapped; [0, 1] is returned.",
    "  - c: See [the lecture notes](notes.md) and [x] the box.",
    "  - c: A[X] is the element at row X; [N] in the reference is the paper count.",
    "  - c: The link [Option](https://example.org) and `[Option]` in code.",
    "  - c: Rubric row 2 [marks: 3].",
    # Signals and matrices: Q[n] in maths, a bold matrix name.
    "  - c: The quantised output is $y[n] = x[n] + Q[n]$, so the error is bounded.",
    "  - c: The error \\(e[n] = Q[n] - x[n]\\) is uniform; Q[n] in prose is a signal too.",
    "  - c: Multiply **[X]** by its transpose to get a symmetric matrix.",
])
def test_legitimate_brackets_pass(tmp_path, text):
    assert finished_errors(tmp_path, with_line(text)) == []


def test_finished_missing_or_odd_files(tmp_path):
    (tmp_path / "exam3_answers.md").write_text(answer_key())
    assert examtools.finished_check(tmp_path, 3).errors == [
        "exam3.md does not exist (only exam3_answers.md does)"]
    (tmp_path / "exam3.md").mkdir()
    assert examtools.finished_check(tmp_path, 3).errors == ["exam3.md is a folder, not a file"]
    (tmp_path / "exam4.md").symlink_to(tmp_path / "nowhere.md")
    assert examtools.finished_check(tmp_path, 4).errors == ["exam4.md is a link to nothing"]
    write_exam(tmp_path, 5, exam_text(total=20, multi=8, code=4))
    assert examtools.finished_check(tmp_path, 5).errors == [
        "exam5_answers.md is missing: an exam is not finished without its answer key"]
    (tmp_path / "exam5_answers.md").mkdir()
    assert examtools.finished_check(tmp_path, 5).errors == [
        "exam5_answers.md is a folder, not a file: an exam is not finished without its "
        "answer key"]


# --- exam numbers and stray exam-like files ------------------------------------------------


def test_exam_numbers_by_kind(tmp_path):
    for name in ("exam1.md", "exam2_answers.md", "exam3_full.md", "exam03.md", "Exam4.md",
                 "exam_template.md", ".notes-exam-session.json"):
        (tmp_path / name).write_text("x")
    (tmp_path / "exam5.md").mkdir()
    assert examtools.exam_numbers(tmp_path) == {"exam": {1, 5}, "answers": {2}, "full": {3}}


def test_stray_exams(tmp_path):
    for name in ("exam1.md", "exam1_answers.md", "exam1_full.md", "exam_template.md",
                 "concept_matrix.md", "EXAM_PROMPT.md", "exam3_notes.md", ".exam1_full.md.tmp",
                 ".notes-exam-session.json", "Exam3.md", "exam03.md", "exam 4.md",
                 "exam5-answers.md", "practice_exam_6.md", "exam7.MD", "batch1/exam8.md",
                 "batch1/deeper/exam9_answers.md", "batch1/notes.md", ".pages/exam10.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert examtools.stray_exams(tmp_path) == [
        "Exam3.md", "batch1/deeper/exam9_answers.md", "batch1/exam8.md", "exam 4.md",
        "exam03.md", "exam5-answers.md", "exam7.MD", "practice_exam_6.md"]


def test_stray_exams_does_not_follow_links(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Exam3.md").write_text("x")
    exams = tmp_path / "exams"
    exams.mkdir()
    (exams / "linked").symlink_to(elsewhere)
    assert examtools.stray_exams(exams) == []
    assert examtools.stray_exams(tmp_path / "missing") == []
