import pytest

from notes_cli.anki import check_file, header

HEADER = (
    "#separator:tab\n"
    "#html:true\n"
    "#notetype:Basic\n"
    "#deck:CS Degree::DBA::Week 03\n"
    "#guid column:1\n"
    "#tags column:4\n"
)

ROWS = (
    "DBA-W03-0001\tWhat does SQL stand for?\tStructured Query Language\tDBA week03 sql\n"
    "DBA-W03-0002\tWhen is \\(R\\) in BCNF?\tWhen every non-trivial FD \\(X \\to Y\\) has "
    "\\(X\\) a superkey.<br>Stricter than 3NF.\tDBA week03 normalisation\n"
    "DBA-W03-0003\tWhat is \\(\\sigma_{a &lt; 5}(R)\\)?\tThe rows of \\(R\\) with "
    "\\(a &lt; 5\\) &amp; nothing else.\\[ |\\sigma(R)| \\le |R| \\]\t\n"
)

GOOD = HEADER + ROWS


def test_header_exact():
    assert header("DBA", 3) == HEADER


def test_header_pads_week_and_keeps_module():
    assert "#deck:CS Degree::FM2::Week 11\n" in header("FM2", 11)
    assert header("FM2", 0).splitlines()[3] == "#deck:CS Degree::FM2::Week 00"
    assert len(header("FM2", 1).splitlines()) == 6


@pytest.mark.parametrize("week", [-1, 100])
def test_header_rejects_week_out_of_range(week):
    with pytest.raises(ValueError):
        header("FM2", week)


def write(tmp_path, text, name="DBA Week 03.txt", raw: bytes | None = None,
          last_id: str | None = "DBA-W03-0003"):
    """Write a card file; also the week's last-id record (as a session would) unless
    `last_id` is None."""
    path = tmp_path / name
    path.write_bytes(raw if raw is not None else text.encode("utf-8"))
    if last_id is not None:
        (tmp_path / "DBA Week 03 last-id.txt").write_text(last_id + "\n")
    return path


def test_check_accepts_good_file(tmp_path):
    assert check_file(write(tmp_path, GOOD)) == []


def test_check_accepts_crlf_and_trailing_blank_line(tmp_path):
    assert check_file(write(tmp_path, GOOD.replace("\n", "\r\n") + "\r\n")) == []


def test_check_accepts_any_file_name_with_matching_deck(tmp_path):
    # A file whose name is not "<MODULE> Week NN.txt" is still checked; only the name is reported.
    problems = check_file(write(tmp_path, GOOD, name="cards.txt"))
    assert len(problems) == 1 and "file name" in problems[0]


def lines_of(text):
    return text.splitlines(keepends=True)


ROW1 = lines_of(ROWS)[0]


@pytest.mark.parametrize(
    "content, expected",
    [
        # The plan's checks.
        (HEADER.replace("#html:true\n", "") + ROWS, ["missing header line '#html:true'"]),
        (HEADER.replace("#guid column:1\n", "") + ROWS, ["missing header line '#guid column:1'"]),
        (HEADER + "#html:true\n" + ROWS, ["line 7", "duplicate header line"]),
        (HEADER + "DBA-W03-0009\tonly a front\n", ["line 7", "4 tab-separated fields", "found 2"]),
        (HEADER + "DBA-W03-0009\ta\tb\tc\textra\n", ["line 7", "found 5"]),
        (HEADER + "\tfront\tback\t\n", ["line 7", "empty id"]),
        (HEADER + "DBA-W03-0009\t \tback\t\n", ["line 7", "empty front"]),
        (HEADER + "DBA-W03-0009\tfront\t\t\n", ["line 7", "empty back"]),
        (GOOD + ROW1.replace("SQL stand", "SQL mean"), ["line 10", "duplicate id 'DBA-W03-0001'", "line 7"]),
        (HEADER + "DBA-W03-0009\tWhat is it?\tfirst line\nsecond line\ttag\n",
         ["lines 7-8", "literal line break", "<br>"]),
        (HEADER + "DBA-W03-0009\tWhat is $x^2$?\tA square\t\n", ["line 7", "$", "\\( \\)"]),
        (HEADER + "DBA-W03-0009\tWhat is it?\t$$x^2$$\t\n", ["line 7", "$"]),
        # Extra checks.
        (HEADER.replace("Week 03", "Week 3") + ROWS, ["line 4", "#deck:CS Degree::<MODULE>::Week NN"]),
        (HEADER.replace("::DBA::", "::FM2::") + ROWS, ["line 4", "FM2", "DBA Week 03.txt"]),
        (HEADER.replace("Week 03", "Week 04") + ROWS, ["line 4", "Week 04", "DBA Week 03.txt"]),
        (HEADER + "#columns:id\tfront\tback\ttags\n" + ROWS, ["line 7", "unexpected header line"]),
        (HEADER + "DBA W03 0009\tfront\tback\t\n", ["line 7", "id", "whitespace"]),
        (HEADER + "DBA-W03-0009\tWhat is \\(x^2?\tA square\t\n", ["line 7", "unbalanced", "\\("]),
        (HEADER + "DBA-W03-0009\tWhat is it?\t\\[ x \\)\t\n", ["line 7", "unbalanced"]),
        (HEADER + "DBA-W03-0009\tIs \\(a < b\\)?\tyes\t\n", ["line 7", "'<'", "&lt;"]),
        (HEADER + "DBA-W03-0009\tWhat is List<String>?\tA list\t\n", ["line 7", "&lt;"]),
        (HEADER + "DBA-W03-0009\tWhat is a && b?\tand\t\n", ["line 7", "'&'", "&amp;"]),
        (HEADER + "DBA-W03-0009\t\"Quoted\" means?\tx\t\n", ["line 7", "double quote", "&quot;"]),
        (HEADER + "DBA-W03-0009\tfront\tback\twith\ttab\n", ["line 7", "found 5"]),
        (HEADER + "DBA-W03-0009\tfront\rmore\tback\t\n", ["line 7", "carriage return"]),
        (HEADER, ["no cards"]),
        (HEADER + ROWS + "DBA-W03-0009\tWhat does SQL stand for?\tagain\t\n",
         ["line 10", "same front as line 7"]),
        ("", ["missing header line '#separator:tab'"]),
    ],
)
def test_check_reports_each_problem(tmp_path, content, expected):
    problems = check_file(write(tmp_path, content))

    assert problems, "expected at least one problem"
    joined = "\n".join(problems)
    for fragment in expected:
        assert fragment in joined, f"{fragment!r} not in {problems!r}"


def test_check_misordered_header_lines(tmp_path):
    swapped = HEADER.replace("#separator:tab\n#html:true\n", "#html:true\n#separator:tab\n")
    problems = check_file(write(tmp_path, swapped + ROWS))

    assert any("line 1" in p and "out of order" in p for p in problems)
    assert any("line 2" in p and "out of order" in p for p in problems)


def test_check_bom(tmp_path):
    problems = check_file(write(tmp_path, "", raw=b"\xef\xbb\xbf" + GOOD.encode()))
    assert any("byte-order mark" in p for p in problems)


def test_check_not_utf8(tmp_path):
    problems = check_file(write(tmp_path, "", raw=GOOD.encode() + b"DBA-W03-0009\tcaf\xe9\tx\t\n"))
    assert len(problems) == 1
    assert "UTF-8" in problems[0] and "line 10" in problems[0]


def test_check_row_starting_with_hash(tmp_path):
    problems = check_file(write(tmp_path, GOOD + "#DBA-W03-0009\tfront\tback\t\n"))
    assert any("line 10" in p and "'#'" in p for p in problems)


def test_check_id_used_in_another_week_file(tmp_path):
    other = HEADER.replace("Week 03", "Week 04") + "DBA-W03-0002\tOther\tcard\t\n"
    write(tmp_path, other, name="DBA Week 04.txt")
    write(tmp_path, other, name="FM2 Week 04.txt")  # another module's file is not compared

    problems = check_file(write(tmp_path, GOOD))

    assert len(problems) == 1
    assert "line 8" in problems[0] and "DBA-W03-0002" in problems[0]
    assert "DBA Week 04.txt" in problems[0]


def test_check_problems_are_single_lines(tmp_path):
    bad = HEADER + "DBA-W03-0009\tWhat is it?\tfirst line\nsecond line\ttag\n" + "x\n"
    for problem in check_file(write(tmp_path, bad)):
        assert "\n" not in problem


def test_check_every_problem_has_a_line_number_or_names_the_file(tmp_path):
    bad = HEADER.replace("#notetype:Basic\n", "") + "id\tfront\tback\t\n" + "id\t\tback\t\n"
    for problem in check_file(write(tmp_path, bad)):
        assert problem.startswith(("line ", "lines ", "file ", "missing header"))


# --- false positives that must not be reported --------------------------------------


@pytest.mark.parametrize(
    "front, back",
    [
        ("Write the matrix", "\\[ \\begin{pmatrix} a \\\\[4pt] b \\end{pmatrix} \\]"),
        ("Cases?", "\\( f(x) = x \\\\(b) \\)"),
        ("What does <code>print(\")\")</code> do?", "Prints a closing paren"),
        ("Regex for a closing paren?", "<code>\\)</code>"),
        ("Shell variables?", "<code>echo $HOME $PATH</code>"),
        ("Multi-line code?", "<pre>if (a &lt; b) {<br>&nbsp;&nbsp;x = 1;<br>}</pre>"),
    ],
)
def test_check_no_false_positive_in_fields(tmp_path, front, back):
    row = f"DBA-W03-0004\t{front}\t{back}\tDBA week03\n"
    problems = check_file(write(tmp_path, GOOD + row, last_id="DBA-W03-0004"))
    assert problems == []


def test_check_tags_column_not_html_checked(tmp_path):
    row = "DBA-W03-0004\tWhat is it?\tA thing\tDBA week03 c++ a&b x<y\n"
    assert check_file(write(tmp_path, GOOD + row, last_id="DBA-W03-0004")) == []


# --- misses that must be reported ----------------------------------------------------


@pytest.mark.parametrize("text", ["the encoding <B>", "<A> and <P>", "<Code>x</Code>"])
def test_check_upper_case_angle_brackets_are_text(tmp_path, text):
    row = f"DBA-W03-0004\tWhat is {text}?\tSomething\t\n"
    problems = check_file(write(tmp_path, GOOD + row, last_id="DBA-W03-0004"))
    assert any("line 10" in p and "&lt;" in p for p in problems)


@pytest.mark.parametrize("char, code", [("\x00", "U+0000"), ("\x07", "U+0007"),
                                        ("\x1b", "U+001B"), ("\x85", "U+0085")])
def test_check_control_characters(tmp_path, char, code):
    row = f"DBA-W03-0004\tWhat is{char} it?\tA thing\t\n"
    problems = check_file(write(tmp_path, GOOD + row, last_id="DBA-W03-0004"))
    assert any("line 10" in p and code in p and "remove" in p for p in problems)


def test_check_blank_line_after_header(tmp_path):
    problems = check_file(write(tmp_path, HEADER + "\n" + ROWS))
    assert problems == ["line 7: blank line between the header and the first card; remove it"]


def test_check_blank_lines_between_cards_and_at_end_are_fine(tmp_path):
    rows = ROWS.splitlines(keepends=True)
    assert check_file(write(tmp_path, HEADER + rows[0] + "\n" + "".join(rows[1:]) + "\n\n")) == []


# --- backups: cards are never deleted ------------------------------------------------


def backup(tmp_path, stamp, text):
    folder = tmp_path / "backups"
    folder.mkdir(exist_ok=True)
    (folder / f"DBA Week 03 {stamp}.txt").write_text(text, encoding="utf-8")


def test_check_reports_cards_missing_since_backup(tmp_path):
    extra = "DBA-W03-0004\tWhat is a view?\tA stored query\tDBA\n"
    backup(tmp_path, "20261115-100000", GOOD + extra)

    problems = check_file(write(tmp_path, GOOD.replace(ROWS.splitlines()[1] + "\n", ""),
                                last_id="DBA-W03-0004"))

    assert sorted(p.split(" was ")[0] for p in problems) == ["card DBA-W03-0002",
                                                             "card DBA-W03-0004"]
    assert all("is missing: cards are never deleted" in p for p in problems)
    assert all("backups/DBA Week 03 20261115-100000.txt" in p for p in problems)
    assert all("proposed removal" in p for p in problems)


def test_check_uses_newest_backup_only(tmp_path):
    backup(tmp_path, "20261101-090000", GOOD + "DBA-W03-0004\tGone\tby the user\t\n")
    backup(tmp_path, "20261115-100000", GOOD)
    other_week = tmp_path / "backups" / "DBA Week 04 20261120-100000.txt"
    other_week.write_text(GOOD.replace("W03", "W04"))

    assert check_file(write(tmp_path, GOOD, last_id="DBA-W03-0004")) == []


def test_check_with_backup_and_new_cards_is_fine(tmp_path):
    backup(tmp_path, "20261115-100000", GOOD)
    row = "DBA-W03-0004\tWhat is a view?\tA stored query\tDBA\n"
    assert check_file(write(tmp_path, GOOD + row, last_id="DBA-W03-0004")) == []


# --- the last-id record: ids are never reissued ---------------------------------------


def test_check_requires_last_id_record(tmp_path):
    problems = check_file(write(tmp_path, GOOD, last_id=None))
    assert problems == ["file 'DBA Week 03 last-id.txt' is missing; create it next to the card "
                        "file, containing DBA-W03-0003, the highest id issued so far for this "
                        "week"]


def test_check_last_id_record_may_be_ahead(tmp_path):
    # The user removed the newest cards from the file; their ids stay used.
    assert check_file(write(tmp_path, GOOD, last_id="DBA-W03-0042")) == []


def test_check_id_above_last_id_record(tmp_path):
    row = "DBA-W03-0004\tWhat is a view?\tA stored query\tDBA\n"
    problems = check_file(write(tmp_path, GOOD + row, last_id="DBA-W03-0003"))
    assert problems == ["line 10: id DBA-W03-0004 is higher than the last issued id recorded "
                        "in 'DBA Week 03 last-id.txt' (DBA-W03-0003); write DBA-W03-0004 into "
                        "that file"]


def test_check_malformed_last_id_record(tmp_path):
    problems = check_file(write(tmp_path, GOOD, last_id="3"))
    assert len(problems) == 1 and "must contain one id of the form DBA-W03-NNNN" in problems[0]


def test_check_no_record_needed_without_scheme_ids(tmp_path):
    rows = "my-own-id\tWhat is SQL?\tA language\t\n"
    assert check_file(write(tmp_path, HEADER + rows, last_id=None)) == []


# --- every message says how to fix it without deleting a card ------------------------


FIX_WORDS = ("write", "remove", "add", "rename", "replace", "reword", "give", "put", "join",
             "use", "save", "make", "create", "copy", "close")


def test_every_problem_says_how_to_fix(tmp_path):
    bad = (
        "#separator:tab\n#notetype:Basic\n#html:true\n#deck:CS Degree::FM2::Week 3\n"
        "#guid column:1\n#tags column:4\n#tags column:4\n#extra\n\n"
        "DBA-W03-0001\tfront\t\t\n"
        "DBA-W03-0001\tfront\tback\t\n"
        "\tx\ty\t\n"
        "A B\t\"q\"\t$x$ a < b & c \\(\t\n"
        "#DBA-W03-0009\tq\ta\t\n"
        "DBA-W03-0002\tbroken\tfirst\nsecond\t\n"
        "DBA-W03-0003\tonly two\n"
        "DBA-W03-0099\tctrl\x01\tcr\rx\t\n"
    )
    backup(tmp_path, "20261115-100000", GOOD + "DBA-W03-0050\tq\ta\t\n")
    (tmp_path / "DBA Week 04.txt").write_text(HEADER + "DBA-W03-0001\tq\ta\t\n")
    problems = check_file(write(tmp_path, bad, name="DBA Week 03.txt", last_id="nonsense"))
    assert len(problems) > 15
    for problem in problems:
        lowered = problem.lower()
        assert any(word in lowered for word in FIX_WORDS), problem
        assert "delete" not in lowered.replace("never deleted", ""), problem
