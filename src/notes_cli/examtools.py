"""Validate, merge and summarise practice exams: the user's old `merge_exam.py`, ported.

The old script worked on `/workspace`; these functions take the exam folder
(`MODULE/Practice Exams/`). File names are unchanged: `exam<N>.md` holds the questions,
`exam<N>_answers.md` the answers, `merge` writes `exam<N>_full.md`; `exam_template.md`,
`concept_matrix.md` and the `_answers`/`_full` files are not exams.

What changed from the old script (spec, "Exam session"; plan, Task 10):

- The expected totals come from the exam's header, as `exam_template.md` writes it:
  `**Total Questions: N**` and `**Format: X% Multiple Choice (M questions), ...**`, instead
  of a hardcoded 20. A header line that cannot be found is an error naming that line.
- Questions are counted as `**Question k**` and also in the forms written questions take
  (`**Question k: Short Answer**` as in the template, `**Question k (5 marks)**`,
  `**Question k — ...**`, `### Question k`); the old pattern missed those.
- "Select" labels and the 40% multi-answer share are checked against the M multiple-choice
  questions only, and only when M > 0 (the old script divided by every Select label and
  failed every exam of a written or part-written format).
- Select labels are counted only in the forms the prompt prescribes, case-sensitively and
  outside code: the old multi-answer pattern ignored case and had no word boundary, so
  SQL such as `SELECT allocation_id` in a code block counted as a multi-answer label, and
  "Select **one**:" counted as multi-answer.
- The 20% code rule counts code blocks plus questions that contain maths (`\\(`, `\\[`,
  `$...$`, `$$`), and falling short is a warning, not an error (theory modules).
- Lazy stems and banned options follow the prompt's rules instead of matching anywhere
  in the file (user request 2026-11-15, now that validation is enforced). The file is split
  into questions (at the question headings), each into a stem (the text before its first
  Select label or option line; Markdown headings before it are skipped) and options (lines
  in the template's form `a. …`, also `a) …`, `(a) …`, `a: …`, `a - …`, with the label in
  bold or italics, plus the lines that continue an option), with fenced code blocks
  masked out:
  - a **lazy stem** (rules 5 and 6) is a stem that consists of nothing but a "Which of the
    following is true?"-type question ("Which is true?", "Which of the following
    statements are false?", "Which of the following statements about X is/are correct?",
    "Select the true statement about X.", "... is NOT true?"), where the topic X, if any,
    is short and generic (at most 8 words, no digits, commas, code or clause such as
    "where …", "built by …", "given …", no reference to shared material such as "the
    diagram above" and no constraint such as "on graphs with negative edges"; rule 5's own
    "Bad" example is lazy). A stem with a scenario, code, a table, data or any other
    sentence is not lazy, and nor is one over 400 characters;
  - a **banned option** (rule 3) is an option containing "All of the above"/"None of the
    above" (also "previous", "preceding", "the others", with emphasis or backticks
    removed) as its subject; "All/None of the options/statements/answers/choices" only in
    that form and on its own (followed by nothing but punctuation, "apply" or "is/are
    correct/true/…", so "All the statements are executed …" is a real option); "All/None
    of these"; or "Both X and Y"/"Both of the above" where X and Y are other options of
    the same question (by letter, not in backticks, or by their text; also "Both b and d
    are correct", "Both a and c only").
  Fallback, where the file does not follow the template well enough to tell stems from
  options: with no question headings at all, the old whole-file patterns are used for
  both checks; in a question, every line that might be an option but does not parse as
  one (`(i) …`, `g) …`, `- …` after the options) is searched for those phrases as plain
  text, whether or not the question's other options parsed. Prose in a stem is not
  searched, so explanation that mentions "none of the above" is not an error.
- Errors and warnings are returned in `ExamCheck`; the CLI prints them and exits 1 if any
  exam has errors. Files are read and written as UTF-8 (bytes that are not UTF-8 are kept
  as they are), and `merge` writes through a temp file and a rename.
- New for `notes exam MODULE` (user request 2026-11-15, "I never want invalid exams"):
  `finished_check` adds "has a finished answer key" to `validate` (an explicit entry such
  as `**Q3:**` for every question number, no TODO/TBD/FIXME placeholder, no template
  placeholder, and the Answer Key Distribution Audit heading the prompt makes
  mandatory); `validate` itself rejects template placeholders (`[Option]`) left in the
  questions; `exam_numbers` and `stray_exams` are what a session's snapshot records
  (`examsession`); `delete_exam` removes an invalid exam's files (exact names only, through
  a checked folder descriptor, never a file that existed before the session). Only the
  post-session check of `notes exam MODULE` deletes; `validate` and the Stop hook never do.

Unchanged: how merge joins the files (questions, a `---` rule, answers; questions only if
there is no answers file), and what `stats` prints.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

EXAM_DIR = "Practice Exams"

_EXAM_FILE = re.compile(r"exam([1-9]\d*)\.md")      # exam1.md, exam10.md; not exam01.md
# A question heading: "**Question 3**"; the template's written form "**Question 13: Short
# Answer**"; also "**Question 13 (5 marks)**", "**Question 13 — Short Answer**" (em or en
# dash or hyphen) and a Markdown heading "### Question 13".
QUESTION = re.compile(r"\*\*Question (\d+)(?:\s*[:—–(-][^*\n]*)?\*\*"
                      r"|^[ \t]*#{1,6}[ \t]+Question (\d+)\b", re.MULTILINE)
# The old script's patterns: now only the fallback (see the module docstring).
LAZY_STEM = re.compile(r"Which of the following (?:is|are) (?:true|correct|false)", re.IGNORECASE)
BANNED_OPTION = re.compile(r"(?:All|None) of the above", re.IGNORECASE)
# A whole stem that is only "Which (one) (of the following/these) (statements) (about X)
# is/are (NOT) true/correct/false… (about X)", or "Select/Identify the true statement (about
# X)", then ?, . or : . The topic X is checked separately (_generic_topic): the stem is lazy
# only if X is short and generic.
_LAZY_PREP = (r"(?:about|regarding|concerning|on|of|for|in|with\s+respect\s+to"
              r"|in\s+relation\s+to)")
_LAZY_WHOLE_STEM = re.compile(
    r"(?:which(?:\s+one)?"
    r"(?:\s+of\s+(?:the\s+following|these|those"
    r"|the\s+(?:statements|options|answers|choices|claims)(?:\s+below)?))?"
    r"(?:\s+(?:statements?|statement\(s\)|options?|claims?|assertions?|answers?|choices?))?"
    r"(?:\s+(?:below|listed(?:\s+below)?))?"
    r"(?:\s+" + _LAZY_PREP + r"\s+(?P<t1>[^?\n]+?))?"
    r"\s+(?:is|are|is/are|are/is|is\s+or\s+are)(?:\s+not)?\s+"
    r"(?:true|correct|false|incorrect|accurate|inaccurate|valid|right|wrong)"
    r"(?:\s+(?:statements?|claims?))?"
    r"(?:\s+" + _LAZY_PREP + r"\s+(?P<t2>[^?\n]+?))?"
    r"|(?:select|identify|choose|pick)(?:\s+(?:the|all|each|every|any))?\s+"
    r"(?:true|correct|false|incorrect|accurate|valid)\s+"
    r"(?:statements?|statement\(s\)|options?|claims?|answers?)"
    r"(?:\s+" + _LAZY_PREP + r"\s+(?P<t3>[^?\n]+?))?)"
    r"\s*[?.:]?", re.IGNORECASE)
# A topic that makes the stem concrete: digits, punctuation that brings in data or code,
# or a clause ("... in a system where ...", "... a tree built by inserting ...").
_TOPIC_CONCRETE = re.compile(r"[\d,;:()\[\]{}`$\\=<>+*/|\x00]")
_TOPIC_CLAUSE = re.compile(r"\b(?:where|when|whenever|if|that|which|who|whose|while|built|"
                           r"given|after|before|using|once|unless|until|because|since)\b",
                           re.IGNORECASE)
LAZY_TOPIC_WORDS = 8
# Material the question refers to ("regarding the diagram above", "about the schedule
# below", "about this schedule", "of the resulting tree", "about the scenario"), and a
# constraint on the topic ("Dijkstra's algorithm on graphs with negative edges", "a mutex
# under heavy contention"): a preposition, then at least two words.
_SHARED_NOUNS = (r"diagram|figure|table|code|schedule|graph|tree|listing|output|trace|"
                 r"snippet|program|query|schema|scenario|example|data|algorithm|function|"
                 r"circuit|automaton|grammar|relation|plot|chart|image|network|log|history|"
                 r"matrix|excerpt|transactions?|pseudocode|class|method|system|design|model")
_TOPIC_SHARED = re.compile(r"\b(?:" + _SHARED_NOUNS + r")s?\b.*\b(?:above|below|shown)\b"
                           r"|\b(?:this|these|the\s+resulting|the\s+given)\s+(?:"
                           + _SHARED_NOUNS + r")s?\b|\bthe\s+scenarios?\b", re.IGNORECASE)
_TOPIC_CONSTRAINT = re.compile(r"\S\s+(?:on|with|under|without)\s+\S+\s+\S", re.IGNORECASE)
LAZY_STEM_MAX = 400  # longer stems are never lazy (and the lazy pattern is quadratic)
# Banned options. "All/None of the above" (also "of the options/choices/…") anywhere in an
# option, when it is the option's subject: followed by the end, punctuation or "is/are/
# apply" ("None of the above-mentioned methods terminates" is not banned).
_ALL_NONE = re.compile(
    r"\b(?:all|none)\s+(?:of\s+)?the\s+(?:above|previous|preceding|others)"
    r"(?:\s+(?:options?|answers?|choices?|statements?))?"
    r"(?=\s*(?:$|[.,;:!?)\]]|(?:is|are|apply|applies)\b))", re.IGNORECASE)
# "All/None of the options/statements/answers/choices" only in the form "all/none OF the
# <noun>" (optionally "of these"), and only when nothing follows but the end, punctuation,
# "apply" or "is/are correct/true/…": "All the statements are executed in a single
# transaction" and "None of the options are inherited by the child" are real options.
_ALL_NONE_NOUN = re.compile(
    r"\b(?:all|none)\s+of\s+the\s+(?:options|statements|answers|choices)(?:\s+of\s+these)?"
    r"(?=\s*(?:$|[.,;:!?)\]]|(?:apply|applies)\b"
    r"|(?:is|are)\s+(?:correct|true|false|incorrect|right|wrong|valid)\b))", re.IGNORECASE)
_ALL_NONE_THESE = re.compile(r"(?:all|none)\s+of\s+(?:these|them|those)", re.IGNORECASE)
_BOTH_ABOVE = re.compile(r"\bboth\s+(?:of\s+)?the\s+(?:above|previous|preceding)(?![-\w])",
                         re.IGNORECASE)
_BOTH_SUFFIX = re.compile(r"\s+(?:(?:are|is)(?:\s+both)?\s+(?:correct|true|right|valid)"
                          r"(?:\s+answers?)?|only|are\s+the\s+(?:correct\s+)?answers?)$",
                          re.IGNORECASE)
_BOTH_OPTION = re.compile(r"both\s+(.+?)\s+(?:and|&)\s+(.+?)", re.IGNORECASE)
_LETTER_WORD = r"(?:(?:options?|answers?|choices?)\s+)?[(\[]?([a-f])[)\]]?"
# "Both a and c" where the options themselves could not be parsed (letters only; a letter
# in backticks is code, `_flat(keep_code=True)` keeps it marked).
_BOTH_LETTERS = re.compile(
    r"\bboth\s+" + _LETTER_WORD + r"\s+(?:and|&)\s+" + _LETTER_WORD +
    r"(?=\s*(?:$|[.,;:!?)\]]|(?:are|is)(?:\s+both)?\s+(?:correct|true|right|valid)\b|only\b))",
    re.IGNORECASE)
# An option line: "a. text" as in the template, also "a) text", "(a) text", "a: text",
# "a - text", with a list marker or checkbox before it and the label in bold or italics
# ("**e.** text", "**(e)** text"). Upper case only with ".", ")" or "(…)", so prose such as
# "A - B is ..." or "A: ..." in a stem is not an option.
_OPTION_LINE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?(?:\[[ xX]?\][ \t]*)?(?:\*\*|__|\*|_)?"
    r"(?:\(([a-fA-F])\)|([a-fA-F])[.)]|([a-f])(?::|[ \t]+[-–—](?=[ \t])))"
    r"(?:\*\*|__|\*|_)?[ \t]+(\S.*?)[ \t]*$")
# A line that might be an option in some other form ("(i) ...", "ii. ...", "g) ...",
# "[a] ...", "1) ...", "- ..." ): if one does not parse as an option, the question's
# non-stem lines are searched for banned phrases as plain text.
_OPTION_LIKE = re.compile(
    r"^[ \t]*(?:[-*+•○◯☐□][ \t]+\S"
    r"|(?:\[[ xX]?\][ \t]*)?(?:\*\*|__|\*|_)?[(\[]?(?:[a-zA-Z]|[ivxIVX]{2,4}|\d{1,2})"
    r"(?:[.):\]]|[ \t]+[-–—])(?:\*\*|__|\*|_)?[ \t]+\S)")
_EMPHASIS = re.compile(r"(?<!\w)[*_]+|[*_]+(?!\w)")
_CODE_MARK = "\x00code\x00"
# Select labels, in the forms the prompt and template prescribe ("Select one:", "Select one
# or more:", "Select **two**:", "Select **all** that apply:"), plus variants: unbolded
# ("Select two:", "Select all that apply:"), bold around more words ("Select **all that
# apply**:", "Select **one or more**:"), "options"/"answers" after the count ("Select **two**
# options:"), the colon inside the bold ("Select **two:**") and the whole label in bold
# ("**Select two:**"); any single bold word other than "one" is a multi-answer count.
# Case-sensitive, a whole word, and ending in a colon, so SQL ("SELECT allocation_id") and
# prose ("select all rows") are not labels; they are looked for only outside code (see
# _without_code).
_SELECT_COUNT = r"(?:one or more|two or more|all that apply|all|two|three|four|five|six|[2-9])"
_SELECT_TAIL = r"(?: (?:options?|answers?|that apply))?"
SELECT_ONE = re.compile(r"(?:\*\*)?\bSelect (?:\*\*)?one(?:\*\*)?(?: (?:option|answer))?"
                        r"(?:\*\*)?:(?:\*\*)?")
SELECT_MULTI = re.compile(
    r"(?:\*\*)?\bSelect (?:(?:\*\*)?" + _SELECT_COUNT + r"(?:\*\*)?" + _SELECT_TAIL
    + r"(?:\*\*)?:(?:\*\*)?"
    r"|\*\*(?!one\*\*)[A-Za-z]+\*\*" + _SELECT_TAIL + r":|\*\*(?!one:)[A-Za-z]+:\*\*)")
SELECT_FORMS = ('"Select one:" (one answer); "Select **two**:", "Select **two** options:", '
                '"Select two:", "**Select two:**", "Select **all** that apply:", "Select '
                '**all that apply**:", "Select one or more:" or "Select **one or more**:" '
                '(several answers)')
FENCE = "```"
_FENCED_BLOCK = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)",
                           re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"(`+)[^\n]*?\1")
# Header lines. The template's forms are shown in errors; accepted: the colon inside or
# outside the bold (or no bold), indentation, a list marker, "MCQ" for "Multiple Choice",
# "(12 MCQ)" for "(12 questions)", and an all-written "**Format: 100% Written (6 questions)**".
TOTAL_LINE = "**Total Questions: N**"
FORMAT_LINE = "**Format: X% Multiple Choice (M questions), Y% Written (W questions)**"
TEMPLATE_TOTAL = "**Total Questions: [TOTAL]**"
TEMPLATE_FORMAT = ("**Format: [X]% Multiple Choice ([MCQ_COUNT] questions), [Y]% Written "
                   "([WRITTEN_COUNT] questions)**")
_LINE_START = r"^[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?(?:\*\*)?"
_LABEL_END = r"[ \t]*(?:\*\*[ \t]*:|:(?:[ \t]*\*\*)?)[ \t]*"
_TOTAL = re.compile(_LINE_START + r"Total Questions" + _LABEL_END + r"(\d+)",
                    re.MULTILINE | re.IGNORECASE)
_FORMAT = re.compile(_LINE_START + r"Format" + _LABEL_END + r"([^\n]*)",
                     re.MULTILINE | re.IGNORECASE)
_MCQ_PART = re.compile(r"(?:Multiple[ -]Choice|MCQs?)\s*\(\s*(\d+)\s*"
                       r"(?:questions?|MCQs?|multiple[ -]choice)?\s*\)", re.IGNORECASE)
_MCQ_WORD = re.compile(r"Multiple[ -]Choice|MCQ", re.IGNORECASE)
_WRITTEN = re.compile(r"\bWritten\b", re.IGNORECASE)
# Maths: \( \[ $$, or $...$ by pandoc's rule (opening $ followed by a non-space, closing $
# preceded by a non-space and not followed by a digit), so "$5 and $10" is not maths.
_MATHS = re.compile(r"\\\(|\\\[|\$\$|(?<![\\$])\$(?=\S)[^$\n]*?(?<=[^\s\\])\$(?!\d)")

MIN_MULTI_PCT = 40
MIN_NOTATION_PCT = 20


class ExamNotFound(Exception):
    """The exam's questions file does not exist."""


class ExamDirError(Exception):
    """The exam folder cannot safely be written to (message is for the user)."""


@dataclass
class ExamCheck:
    n: int
    errors: list[str]
    warnings: list[str]
    questions: int          # "**Question k**" headings found
    single: int             # "Select one:" labels
    multi: int              # multi-answer Select labels
    code_blocks: int        # ``` pairs
    expected_total: int | None = None   # N from the header, None if not found
    expected_mcq: int | None = None     # M from the header, None if not found
    maths: int = 0          # questions with maths and no code block
    uncheckable: bool = False   # a file could not be read: the exam is neither valid nor invalid

    @property
    def ok(self) -> bool:
        return not self.errors


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def _questions_file(exam_dir: Path, n: int) -> Path:
    path = Path(exam_dir) / f"exam{n}.md"
    if not path.is_file():
        raise ExamNotFound(f"exam{n}.md not found in {exam_dir}")
    return path


def find_exams(exam_dir: Path) -> list[int]:
    """The numbers N of the `exam<N>.md` files in `exam_dir`, in order."""
    exam_dir = Path(exam_dir)
    if not exam_dir.is_dir():
        return []
    found = set()
    for path in exam_dir.iterdir():
        m = _EXAM_FILE.fullmatch(path.name)
        if m and path.is_file():
            found.add(int(m.group(1)))
    return sorted(found)


def _question_texts(content: str) -> list[str]:
    """The text of each question, from its heading to the next one (or the end)."""
    starts = [m.start() for m in QUESTION.finditer(content)]
    return [content[a:b] for a, b in zip(starts, starts[1:] + [len(content)])]


def _without_code(content: str) -> str:
    """`content` with fenced code blocks and inline code spans removed."""
    return _INLINE_CODE.sub("", _FENCED_BLOCK.sub("", content))


@dataclass
class _Question:
    number: int
    stem: str                       # fenced code masked out, whitespace collapsed
    options: list[tuple[str, str]]  # (letter, text with its continuation lines)
    labelled: bool                  # has a Select label
    unparsed: list[str]             # lines that may be options but are not in an option form


def _mask_code(content: str) -> str:
    return _INLINE_CODE.sub(_CODE_MARK, _FENCED_BLOCK.sub(_CODE_MARK, content))


def _plain(text: str) -> str:
    """Text with bold/italic markers removed and whitespace collapsed."""
    return " ".join(text.replace("**", "").replace("__", "").split())


_CODE_OPEN, _CODE_CLOSE = "\x02", "\x03"


def _flat(text: str, keep_code: bool = False) -> str:
    """Text as compared with banned phrases: emphasis markers (`*`, `_` next to a non-word
    character) and backticks removed, whitespace collapsed, case folded. With `keep_code`,
    each inline code span is kept marked (between \\x02 and \\x03) instead, so that a
    backticked `a` is not taken for option a."""
    if keep_code:
        text = _INLINE_CODE.sub(lambda m: _CODE_OPEN + m.group(0).strip("`") + _CODE_CLOSE,
                                text)
    return " ".join(_EMPHASIS.sub("", text.replace("`", "")).split()).casefold()


def _uncode(text: str) -> str:
    return text.replace(_CODE_OPEN, "").replace(_CODE_CLOSE, "")


_RULE_LINES = ("---", "***", "___")


def _split_questions(content: str) -> list[_Question]:
    """Each question's stem, options and unparsed option-like lines (fenced code masked out).

    The stem is the text after the heading up to the first Select label, option line, `---`
    rule or (once the stem has begun) Markdown heading; headings before the stem (`####
    Scenario`) are skipped, and so is the rest of a Markdown question heading's line. A
    non-blank line straight after an option line continues it."""
    masked = _FENCED_BLOCK.sub(_CODE_MARK, content)
    heads = list(QUESTION.finditer(masked))
    questions = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(masked)
        lines = masked[head.end():end].split("\n")
        if head.group(2) is not None:   # "### Question 3 — Networking": the rest is a title
            lines[0] = ""
        stem: list[str] = []
        options: list[list[str]] = []
        unparsed: list[str] = []
        labelled, state, current = False, "stem", None
        for line in lines:
            text = line.strip()
            option = _OPTION_LINE.match(line)
            if option:
                letter = next(g for g in option.groups()[:3] if g)
                options.append([letter.lower(), option.group(4)])
                current, state = options[-1], "options"
                continue
            if SELECT_ONE.search(line) or SELECT_MULTI.search(line):
                labelled = True
                rest = SELECT_MULTI.sub("", SELECT_ONE.sub("", line)).strip()
                if state == "stem" and rest:
                    stem.append(rest)
                current, state = None, "options"
                continue
            if not text:
                current = None
                if state == "stem":
                    stem.append("")
                continue
            if text in _RULE_LINES or text.startswith("#"):
                current = None
                if text.startswith("#") and state == "stem" and not "".join(stem).strip():
                    continue            # a heading before the stem
                state = "end"
                continue
            if state == "stem":
                stem.append(line)
                if _OPTION_LIKE.match(line):
                    unparsed.append(text)
            elif state == "options" and current is not None:
                current[1] += " " + text
            elif state == "options" or _OPTION_LIKE.match(line):
                unparsed.append(text)
        questions.append(_Question(int(head.group(1) or head.group(2)), _plain("\n".join(stem)),
                                   [(lt, t) for lt, t in options], labelled, unparsed))
    return questions


def _option_key(text: str) -> str:
    return _flat(text).strip(" .()[]")


def _all_none(text: str) -> bool:
    return bool(_ALL_NONE.search(text) or _ALL_NONE_NOUN.search(text)
                or _BOTH_ABOVE.search(text))


def _banned(option: str, options: list[tuple[str, str]], letter: str) -> bool:
    text = _flat(option)
    if _all_none(text):
        return True
    core = _BOTH_SUFFIX.sub("", text.rstrip(" .!;"))
    if _ALL_NONE_THESE.fullmatch(core):
        return True
    # "Both X and Y": X and Y must be other options of this question, by letter (not in
    # backticks: "Both `a` and `b`" names variables) or by their text.
    coded = _BOTH_SUFFIX.sub("", _flat(option, keep_code=True).rstrip(" .!;"))
    both = _BOTH_OPTION.fullmatch(coded)
    if not both:
        return False
    others = {lt for lt, _ in options if lt != letter}
    texts = {_option_key(t) for lt, t in options if lt != letter}
    parts = [re.sub(r"^(?:options?|answers?|choices?)\s+", "", p.strip(" .()[]"))
             .strip(" .()[]") for p in both.groups()]
    return all((len(p) == 1 and p in others) or _uncode(p).strip(" .()[]") in texts
               for p in parts)


def _banned_line(line: str, letters: set[str] = frozenset()) -> bool:
    """A banned phrase in a line that could not be parsed as an option. "Both a and c"
    counts only with letters of the question's parsed options, if it has any."""
    if _all_none(_flat(line)):
        return True
    for m in _BOTH_LETTERS.finditer(_flat(line, keep_code=True)):
        if not letters or {m.group(1), m.group(2)} <= letters:
            return True
    return False


def _generic_topic(topic: str | None) -> bool:
    """A lazy stem's topic ("about X"): short, with no data, code or clause in it, not
    material shared by several questions ("the diagram above") and no constraint ("on
    graphs with negative edges")."""
    if topic is None:
        return True
    return (len(topic.split()) <= LAZY_TOPIC_WORDS and not _TOPIC_CONCRETE.search(topic)
            and not _TOPIC_CLAUSE.search(topic) and not _TOPIC_SHARED.search(topic)
            and not _TOPIC_CONSTRAINT.search(topic))


def _lazy(stem: str) -> bool:
    if not stem or _CODE_MARK in stem or "`" in stem or len(stem) > LAZY_STEM_MAX:
        return False
    m = _LAZY_WHOLE_STEM.fullmatch(_EMPHASIS.sub("", stem).strip())
    return bool(m) and all(_generic_topic(m.group(g)) for g in ("t1", "t2", "t3"))


def _plural_s(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _shown_line(text: str, limit: int = 60) -> str:
    text = " ".join(text.replace("\x00", "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _stem_and_option_errors(content: str) -> list[str]:
    """Lazy stems (rules 5, 6) and banned options (rule 3); see the module docstring."""
    questions = _split_questions(content)
    errors = []
    if not questions:  # cannot tell stems from options: the old whole-file patterns
        lazy = LAZY_STEM.findall(content)
        if lazy:
            errors.append(f"{len(lazy)} lazy stems found ('Which of the following is true...'; "
                          "no question headings, so the whole file was searched)")
        banned = BANNED_OPTION.findall(content)
        if banned:
            errors.append(f"{len(banned)} banned 'All/None of the above' options found (no "
                          "question headings, so the whole file was searched)")
        return errors

    lazy = [q.number for q in questions if _lazy(q.stem)]
    if lazy:
        errors.append(f"{_plural_s(len(lazy), 'lazy stem')} (rules 5 and 6: the whole stem is "
                      "only a 'Which of the following is true?'-type question about a general "
                      "topic; fix it by putting a concrete scenario, code or data before the "
                      "question, or by asking about a specific constrained case, e.g. 'Which "
                      "of the following is true of a binary search tree built by inserting 4, "
                      "2, 6 in that order?'): " + ", ".join(f"Question {n}" for n in lazy))
    banned = []
    for q in questions:
        banned += [f"Question {q.number} option {lt}" for lt, text in q.options
                   if _banned(text, q.options, lt)]
        banned += [f"Question {q.number} line \"{_shown_line(line)}\" (options not in the "
                   "'a. ...' form)" for line in q.unparsed
                   if _banned_line(line, {lt for lt, _ in q.options})]
    if banned:
        errors.append(f"{_plural_s(len(banned), 'banned option')} (rule 3: never 'All of the "
                      "above', 'None of the above' or 'Both X and Y'; replace each with a "
                      "distractor that targets a specific misconception): " + ", ".join(banned))
    return errors


def _mcq_count(rest: str) -> int | None:
    """M from the text after "Format:": "60% Multiple Choice (12 questions), ..." → 12;
    an all-written "100% Written (6 questions)" → 0; None if it cannot be told."""
    m = _MCQ_PART.search(rest)
    if m:
        return int(m.group(1))
    if not _MCQ_WORD.search(rest) and _WRITTEN.search(rest):
        return 0
    return None


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%"


def validate(exam_dir: Path, n: int) -> ExamCheck:
    """Check `exam<n>.md` (the questions file). Raises ExamNotFound if it does not exist."""
    content = _read(_questions_file(exam_dir, n))
    errors: list[str] = []
    warnings: list[str] = []

    m = _TOTAL.search(content)
    total = int(m.group(1)) if m else None
    if total is None:
        errors.append(f"header line {TOTAL_LINE} not found; write it as in exam_template.md: "
                      f"{TEMPLATE_TOTAL}")
    m = _FORMAT.search(content)
    mcq = _mcq_count(m.group(1)) if m else None
    if mcq is None:
        what = "has no readable multiple-choice count" if m else "not found"
        errors.append(f"header line {FORMAT_LINE} {what}; write it as in exam_template.md: "
                      f"{TEMPLATE_FORMAT}")
    if total is not None and mcq is not None and mcq > total:
        errors.append(f"header says {mcq} multiple-choice questions but {total} questions in total")

    questions = list(QUESTION.finditer(content))
    if total is not None and len(questions) < total:
        errors.append(f"Only {len(questions)} questions found (header says {total})")

    errors += _stem_and_option_errors(content)
    placeholders = placeholder_error(f"exam{n}.md", content)
    if placeholders:
        errors.append(placeholders)

    labels = _without_code(content)
    single = len(SELECT_ONE.findall(labels))
    multi = len(SELECT_MULTI.findall(labels))
    if mcq:  # checked against the multiple-choice questions only, and only if there are any
        if single + multi < mcq:
            questions_ = _split_questions(content)
            missing = [q.number for q in questions_ if q.options and not q.labelled] or \
                [q.number for q in questions_[:mcq] if not q.labelled]
            which = (f"; no Select label on question{'s' if len(missing) != 1 else ''} "
                     f"{_numbers_text(missing)}" if missing else "")
            errors.append(f"Only {single + multi} Select labels for {mcq} multiple-choice "
                          f"questions{which}. Label each multiple-choice question, on its own "
                          f"line before the options, in one of these forms: {SELECT_FORMS}")
        if multi * 100 < MIN_MULTI_PCT * mcq:
            errors.append(f"Multi-answer questions: {multi}/{mcq} = {_pct(multi, mcq)} of the "
                          f"multiple-choice questions (need >={MIN_MULTI_PCT}%)")

    code_blocks = content.count(FENCE) // 2
    maths = sum(1 for text in _question_texts(content)
                if FENCE not in text and _MATHS.search(text))
    notation = code_blocks + maths
    base = total if total is not None else len(questions)
    if base and notation * 100 < MIN_NOTATION_PCT * base:
        warnings.append(f"Code/notation questions: {notation}/{base} = {_pct(notation, base)} "
                        f"({MIN_NOTATION_PCT}% wanted where the subject allows; counted: code "
                        "blocks, and questions with \\( \\[ or $ maths)")

    return ExamCheck(n=n, errors=errors, warnings=warnings, questions=len(questions),
                     single=single, multi=multi, code_blocks=code_blocks,
                     expected_total=total, expected_mcq=mcq, maths=maths)


def report(check: ExamCheck) -> str:
    """The old script's per-exam output, with errors and warnings told apart."""
    name = f"exam{check.n}.md"
    e, w = len(check.errors), len(check.warnings)
    if e:
        head = f"{name} — {e} error(s)" + (f", {w} warning(s)" if w else "") + ":"
    elif w:
        head = f"{name} — passed with {w} warning(s):"
    else:
        head = f"{name} — All checks passed"
    lines = [head]
    lines += [f"  - error: {text}" for text in check.errors]
    lines += [f"  - warning: {text}" for text in check.warnings]
    header = ("header: " + (f"{check.expected_total} questions" if check.expected_total is not None
                            else "no total")
              + ", " + (f"{check.expected_mcq} multiple choice" if check.expected_mcq is not None
                        else "no format line"))
    lines.append(f"  Questions: {check.questions}, Single: {check.single}, Multi: {check.multi}, "
                 f"Code blocks: {check.code_blocks}, Maths questions: {check.maths} ({header})")
    return "\n".join(lines)


def check_plain_folder(exam_dir: Path) -> None:
    """Refuse to write into an exam folder that is a symlink (it could lead outside the
    module folder) or not a folder; raises ExamDirError."""
    exam_dir = Path(exam_dir)
    if exam_dir.is_symlink() or (exam_dir.exists() and not exam_dir.is_dir()):
        raise ExamDirError(f"{exam_dir} is not a plain folder inside the module folder; "
                           "move it out of the way and try again")


def merge(exam_dir: Path, n: int) -> Path:
    """Write `exam<n>_full.md`: the questions, a `---` rule, then the answers (questions
    only if `exam<n>_answers.md` does not exist).

    An existing `exam<n>_full.md` is overwritten (through a temp file and a rename): it is
    this tool's own output, regenerated from the two parts, which are never changed. Raises
    ExamDirError if `exam_dir` is a symlink, and ExamNotFound if `exam<n>.md` does not
    exist; in both cases nothing is written."""
    exam_dir = Path(exam_dir)
    check_plain_folder(exam_dir)
    content = _read(_questions_file(exam_dir, n))
    answers = exam_dir / f"exam{n}_answers.md"
    if answers.is_file():
        content += "\n\n---\n\n" + _read(answers)

    out = exam_dir / f"exam{n}_full.md"
    tmp = exam_dir / f".exam{n}_full.md.tmp"
    try:
        tmp.unlink()  # left by an interrupted run; it is ours
    except FileNotFoundError:
        pass
    with open(tmp, "x", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(content)
    os.replace(tmp, out)
    return out


def stats(exam_dir: Path) -> str:
    """The old script's table: size, ~tokens (chars / 4), questions, answers file."""
    exam_dir = Path(exam_dir)
    exams = find_exams(exam_dir)
    if not exams:
        return "No exam files found."

    lines = [f"Found {len(exams)} exam(s): {exams}", "",
             f"{'Exam':<8} {'Chars':>8} {'~Tokens':>8} {'Questions':>10} {'Has Answers':>12}",
             "-" * 52]
    total_chars = 0
    for n in exams:
        content = _read(exam_dir / f"exam{n}.md")
        chars = len(content)
        total_chars += chars
        q_count = sum(1 for _ in QUESTION.finditer(content))
        if (exam_dir / f"exam{n}_answers.md").exists():
            has_answers = "YES"
        else:
            has_answers = "INLINE" if "# ANSWERS" in content else "NO"
        lines.append(f"exam{n:<4} {chars:>8,} {chars // 4:>8,} {q_count:>10} {has_answers:>12}")
    lines.append("-" * 52)
    lines.append(f"{'Total':<8} {total_chars:>8,} {total_chars // 4:>8,}")
    return "\n".join(lines)


# --- sessions: what `notes exam MODULE` and its Stop hook check -------------------------
#
# Which exams belong to a session is decided by a snapshot of the exam numbers taken when
# it starts (`examsession`), never by modification times: exam<N>.md is the session's if
# no exam<N>.md existed when the session started.

_PART = re.compile(r"exam([1-9]\d*)(_answers|_full)?\.md")
_EXAM_PARTS = ("exam{n}.md", "exam{n}_answers.md", "exam{n}_full.md")
PART_KINDS = ("exam", "answers", "full")


def exam_numbers(exam_dir: Path) -> dict[str, set[int]]:
    """The numbers N of every `exam<N>.md`, `exam<N>_answers.md` and `exam<N>_full.md` in
    `exam_dir`, by kind ("exam", "answers", "full"), whatever each entry is (file, folder,
    link). Empty sets if `exam_dir` is not a folder."""
    found: dict[str, set[int]] = {kind: set() for kind in PART_KINDS}
    exam_dir = Path(exam_dir)
    if not exam_dir.is_dir():
        return found
    for path in exam_dir.iterdir():
        m = _PART.fullmatch(path.name)
        if m:
            kind = {None: "exam", "_answers": "answers", "_full": "full"}[m.group(2)]
            found[kind].add(int(m.group(1)))
    return found


# A file that looks like an exam but is not named as one, so nothing checks it: "Exam3.md",
# "exam03.md", "exam 3.md", "exam3-answers.md", "practice_exam_3.md" in `Practice Exams/`,
# and any of these or a proper exam<N>.md name in a subfolder.
_EXAM_LIKE = re.compile(r"(?:practice[ _-]?)?exam[ _-]?0*\d+"
                        r"(?:[ _-]?(?:answers?|answer[ _-]?key|key|solutions?|full|questions?))?"
                        r"\.md", re.IGNORECASE)
STRAY_SCAN_LIMIT = 20000  # entries looked at, at most (a huge folder is not walked forever)


def stray_exams(exam_dir: Path) -> list[str]:
    """Exam-like files in `exam_dir` (and its subfolders, links not followed) that are not
    exactly `exam<N>.md`, `exam<N>_answers.md` or `exam<N>_full.md` in `exam_dir` itself,
    as paths relative to `exam_dir` (POSIX form), sorted. Hidden names are ignored."""
    exam_dir = Path(exam_dir)
    if exam_dir.is_symlink() or not exam_dir.is_dir():
        return []
    found, seen = [], 0
    for dirpath, dirnames, filenames in os.walk(exam_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        top = Path(dirpath) == exam_dir
        for name in filenames:
            seen += 1
            if name.startswith(".") or not _EXAM_LIKE.fullmatch(name):
                continue
            if top and _PART.fullmatch(name):
                continue
            found.append((Path(dirpath) / name).relative_to(exam_dir).as_posix())
        seen += len(dirnames)
        if seen > STRAY_SCAN_LIMIT:
            break
    return sorted(found)


def _not_a_file(path: Path) -> str | None:
    """Why `path` cannot be read as an exam file, or None if it is a regular file (links
    followed)."""
    if not os.path.lexists(path):
        return "does not exist"
    if path.is_dir():
        return "is a folder, not a file"
    if not path.is_file():
        return ("is a link to nothing" if path.is_symlink() and not path.exists()
                else "is not a regular file")
    return None


# An answer entry in the answers file: a line starting with "Q3", "Question 3" or "Answer
# 3" (the template's "**Q3:** b", also "Q3: b", "Q3. b", "**Q 3**:", "## Q3", "### Question
# 3", "**Question 3**", "**Q13: Short Answer**", "- **Q3:** b"), the number followed by the
# end of the bold, a colon, a full stop, a dash, "(" or the end of the line. A bare
# numbered-list line ("3. b") is not an entry: the prompt never writes one, and a list in
# an explanation would otherwise count.
_ANSWER_ENTRY = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?(?:#{1,6}[ \t]+)?(?:\*\*|__)?[ \t]*"
    r"(?:Q|Question|Answer)[ \t]*\[?(\d+)\]?"
    r"(?:\*\*|__|(?=[ \t]*(?:[:.)(—–-]|$)))",
    re.MULTILINE | re.IGNORECASE)
MAX_ANSWER_KEY_QUESTIONS = 500  # a huge "Total Questions" must not exhaust memory
# A placeholder left in an answer: TODO/TBD/FIXME (upper case) as a whole answer or
# explanation, in brackets ("[TODO]", "[TBD: …]"), or followed by a colon at the start of a
# line or of an entry ("TODO: explain", "**Q4:** b — FIXME: check"). The word in prose
# ("The TODO comment is self-admitted technical debt") is not a placeholder.
_PLACEHOLDER_WORD = r"(?:TODO|TBD|FIXME)"
_PLACEHOLDER_BRACKET = re.compile(r"\[[ \t]*" + _PLACEHOLDER_WORD + r"\b[^\]\n]*\]")
# What may come before an answer on its line: a list marker, a heading, and an entry label
# ("**Q4:**", "Q4.", "**Model Answer:**", "- a:", "Rubric:"), then the answer letters and a
# dash ("b —", "b, c, e:").
_ENTRY_LEAD = re.compile(
    r"^[ \t]*(?:[-*+>][ \t]+)?(?:#{1,6}[ \t]+)?"
    r"(?:(?:\*\*|__)?[ \t]*(?:(?:Q|Question|Answer)[ \t]*\d+|Model Answer|Answer|Explanation|"
    r"Rubric|Solution|[a-fA-F])(?:[ \t]*\([^)\n]*\))?[ \t]*(?:\*\*|__)?[ \t]*[:.)](?:[ \t]*(?:\*\*|__))?"
    r"[ \t]*)?"
    r"(?:(?:\*\*|__)?[a-fA-F](?:[ \t]*,[ \t]*[a-fA-F])*(?:\*\*|__)?[ \t]*[—–:-][ \t]*)?",
    re.IGNORECASE)
# (A reference after it is still nothing: "**Q4:** TODO (Lecture 3)".)
_PLACEHOLDER_ALONE = re.compile(r"[\s*_`.(\[]*" + _PLACEHOLDER_WORD + r"[\s*_`.!)\]]*"
                                r"(?:\([^()\n]*\)[\s.]*)?")
_PLACEHOLDER_START = re.compile(r"[*_]*" + _PLACEHOLDER_WORD + r"[*_]*[ \t]*:")
# The Answer Key Distribution Audit section: a heading containing the name, as in the
# template ("## Answer Key Distribution Audit"), or the name inside a bold line.
_AUDIT = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+[^\n]*?answer key distribution audit"
                    r"|(\*\*|__)[^\n*_]*?answer key distribution audit[^\n*_]*?\1)",
                    re.MULTILINE | re.IGNORECASE)
AUDIT_HEADING = "## Answer Key Distribution Audit"


def _numbers_text(numbers: list[int], limit: int = 12) -> str:
    shown = [str(n) for n in numbers[:limit]]
    more = f" and {len(numbers) - limit} more" if len(numbers) > limit else ""
    return ", ".join(shown) + more


def _blank_code(text: str) -> str:
    """`text` with fenced code blocks and inline code spans replaced by spaces (newlines
    kept), so that offsets and line numbers stay those of `text`."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))   # noqa: E731
    return _INLINE_CODE.sub(blank, _FENCED_BLOCK.sub(blank, text))


_MATHS_SPAN = re.compile(r"\$\$.*?\$\$|\\\(.*?\\\)|\\\[.*?\\\]"
                         r"|(?<![\\$])\$(?=\S)[^$\n]*?(?<=[^\s\\])\$(?!\d)", re.DOTALL)


def _blank_maths(text: str) -> str:
    """`text` with maths spans ($…$ by pandoc's rule as in _MATHS, $$…$$, \\(…\\), \\[…\\])
    replaced by spaces (newlines kept)."""
    return _MATHS_SPAN.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _lines_text(lines: list[int], limit: int = 8) -> str:
    lines = sorted(set(lines))
    word = "line" if len(lines) == 1 else "lines"
    return f"{word} {_numbers_text(lines, limit)}"


def todo_lines(answers: str) -> list[int]:
    """Line numbers of the TODO/TBD/FIXME placeholders in an answers file (see
    _PLACEHOLDER_BRACKET); code is ignored, and so is a table cell that is only the word
    when its row has other non-empty cells."""
    found = []
    for number, line in enumerate(_blank_code(answers).split("\n"), 1):
        if _PLACEHOLDER_BRACKET.search(line):
            found.append(number)
            continue
        cells = line.strip().strip("|").split("|") if line.lstrip().startswith("|") else [line]
        row = len([cell for cell in cells if cell.strip()]) > 1
        for cell in cells:
            if row and _PLACEHOLDER_ALONE.fullmatch(cell):
                continue                # "| TODO | marks unfinished work |": a table's content
            lead = _ENTRY_LEAD.match(cell)
            rest = cell[lead.end():] if lead else cell
            if _PLACEHOLDER_ALONE.fullmatch(rest) or _PLACEHOLDER_START.match(rest):
                found.append(number)
                break
    return found


_BRACKET_TOKEN = re.compile(r"\[([^\[\]\n]+)\]")
# exam_template.md's short placeholders ("[X]", "[N]", "[n]", "[%]", ...) also occur in real
# content (`A[X]`, "[N]" in a reference), so they count only where the template puts them.
# "Q[n]" and "**[X]**" only in the template's forms: "Q[n]([letters])" (the audit line),
# "Q[N]:"/"Q[N+1]:" (answer entries; "(" or ":" right after the bracket) and "| **[X]** |"
# (a rubric's total), and maths spans
# are blanked before this is searched, so "$y[n] = x[n] + Q[n]$" and a bold matrix name
# "**[X]**" in prose are not placeholders.
_SHORT_PLACEHOLDERS = re.compile(
    r"\[(?:X|Y|%)\][ \t]*%|\[X\][ \t]+marks\b|\|[ \t]*(?:\*\*)?\[X\](?:\*\*)?[ \t]*\|"
    r"|\bmax \[X\]|\bQ\[(?:n|N(?:\+\d+)?)\](?=[(:])|\bQuestion \[N(?:\+\d+)?\]|\b[Ee]xam ?\[N\]"
    r"|\[(?:R|A|An|S)\][ \t]+(?:recall|application|analysis|synthesis)\b")
_FALLBACK_PLACEHOLDERS = (
    "[MODULE NAME]", "[TOTAL]", "[MCQ_COUNT]", "[WRITTEN_COUNT]", "[MINUTES]", "[Option]",
    "[count]", "[letters]", "[Pass/Fail]", "[Criterion]", "[threshold]", "[cases]",
    "[one sentence]", "[Complete answer.]", "[Full working solution]",
    "[1-2 sentence explanation. Reference: Lecture X, slide Y.]", "[misconception name]",
    "[one sentence max]", "[one sentence with lecture reference]",
    "[1-2 sentence explanation of why b is correct, with lecture reference]")


@lru_cache(maxsize=1)
def template_placeholders() -> tuple[frozenset[str], frozenset[str]]:
    """The long bracket placeholders of exam_template.md and of the prompt's answer
    format ("Rules for Answer Explanations"), as (words, phrases): single-word ones
    ("[Option]", "[count]") and the others ("[Pass/Fail]", "[one sentence]", "[code — 5-15
    lines]"). Short ones ("[X]", "[N+1]") are _SHORT_PLACEHOLDERS. If the packaged files
    cannot be read, a built-in copy of the list is used."""
    try:
        prompts = resources.files("notes_cli") / "prompts"
        text = (prompts / "exam_template.md").read_text("utf-8")
        prompt = (prompts / "exam.md").read_text("utf-8")
        start = prompt.index("### Rules for Answer Explanations")
        text += prompt[start:prompt.index("### Pre-Output Checklist", start)]
        tokens = {f"[{m.group(1)}]" for m in _BRACKET_TOKEN.finditer(text)}
    except (OSError, ValueError, ModuleNotFoundError):
        tokens = set(_FALLBACK_PLACEHOLDERS)
    long = {t for t in tokens if len(t) > 5 or re.search(r"[^A-Za-z0-9+%\[\]]", t)}
    words = frozenset(t for t in long if re.fullmatch(r"\[[A-Za-z_]+\]", t))
    return words, frozenset(long - words)


def placeholder_lines(text: str) -> list[tuple[str, int]]:
    """Template placeholders left in `text`, as (placeholder, line number), in order.
    A placeholder counts only on its own, not as an index (`arr[count]`) or a link text
    (`[Option](…)`); single-word ones and the short ones only outside code."""
    words, phrases = template_placeholders()
    found = []
    outside = _blank_code(text)
    for source, tokens in ((text, phrases), (outside, words)):
        for m in _BRACKET_TOKEN.finditer(source):
            token = m.group(0)
            before = source[m.start() - 1] if m.start() else ""
            after = source[m.end():m.end() + 1]
            if token in tokens and not (before.isalnum() or before in "_]\\") and after != "(":
                found.append((m.start(), token))
    found += [(m.start(), m.group(0))
              for m in _SHORT_PLACEHOLDERS.finditer(_blank_maths(outside))]
    return [(token, _line_of(text, at)) for at, token in sorted(found)]


def placeholder_error(name: str, text: str) -> str | None:
    found = placeholder_lines(text)
    if not found:
        return None
    shown = []
    for token, line in found:
        item = f"{token} (line {line})"
        if item not in shown:
            shown.append(item)
    more = f" and {len(shown) - 6} more" if len(shown) > 6 else ""
    return (f"{name} still has template placeholders: {', '.join(shown[:6])}{more}; replace "
            "each with real content")


def answer_key_errors(questions: str, answers: str, n: int) -> list[str]:
    """What makes `answers` (exam<n>_answers.md) not a finished answer key for `questions`
    (exam<n>.md): no entry for some question number, a TODO/TBD/FIXME placeholder or a
    template placeholder left in it, or no Answer Key Distribution Audit section
    (exam_template.md; the prompt makes it mandatory). Code blocks are ignored, except
    that a sentence-like template placeholder inside one still counts."""
    name = f"exam{n}_answers.md"
    errors = []
    plain = _FENCED_BLOCK.sub(_CODE_MARK, answers)
    wanted = sorted({q.number for q in _split_questions(questions)})
    if not wanted:
        m = _TOTAL.search(questions)
        total = min(int(m.group(1)), MAX_ANSWER_KEY_QUESTIONS) if m else 0
        wanted = list(range(1, total + 1))
    have = {int(a) for a in _ANSWER_ENTRY.findall(plain)}
    missing = [k for k in wanted if k not in have]
    if missing:
        errors.append(f"{name} has no answer for question{'s' if len(missing) > 1 else ''} "
                      f"{_numbers_text(missing)}: give every question its own entry, as in "
                      "exam_template.md (**Q1:** b — …)")
    todos = todo_lines(answers)
    if todos:
        errors.append(f"{name} still has a TODO, TBD or FIXME placeholder on "
                      f"{_lines_text(todos)}: finish those answers")
    placeholders = placeholder_error(name, answers)
    if placeholders:
        errors.append(placeholders)
    if not _AUDIT.search(plain):
        errors.append(f"{name} has no Answer Key Distribution Audit section: end it with the "
                      f"'{AUDIT_HEADING}' section from exam_template.md")
    return errors


def _no_check(n: int, error: str) -> ExamCheck:
    return ExamCheck(n=n, errors=[error], warnings=[], questions=0, single=0, multi=0,
                     code_blocks=0)


def finished_check(exam_dir: Path, n: int) -> ExamCheck:
    """`validate`, plus: an exam is not finished without a complete answer key
    (`answer_key_errors`). A questions or answers file that is missing, not a file or
    unreadable is an error here, not an exception."""
    exam_dir = Path(exam_dir)
    questions_path = exam_dir / f"exam{n}.md"
    answers = exam_dir / f"exam{n}_answers.md"
    problem = _not_a_file(questions_path)
    if problem == "does not exist":
        only = f" (only exam{n}_answers.md does)" if os.path.lexists(answers) else ""
        return _no_check(n, f"exam{n}.md does not exist{only}")
    if problem:
        return _no_check(n, f"exam{n}.md {problem}")
    try:
        check = validate(exam_dir, n)
        questions = _read(questions_path)
    except OSError as e:
        unread = _no_check(n, f"exam{n}.md cannot be read: {e.strerror or e}")
        unread.uncheckable = True
        return unread
    problem = _not_a_file(answers)
    if problem == "does not exist":
        check.errors.append(f"exam{n}_answers.md is missing: an exam is not finished without "
                            "its answer key")
        return check
    if problem:
        check.errors.append(f"exam{n}_answers.md {problem}: an exam is not finished without "
                            "its answer key")
        return check
    try:
        text = _read(answers)
    except OSError as e:
        check.errors.append(f"exam{n}_answers.md cannot be read: {e.strerror or e}")
        check.uncheckable = True
        return check
    if not text.strip():
        check.errors.append(f"exam{n}_answers.md is empty: an exam is not finished without its "
                            "answer key")
        return check
    check.errors += answer_key_errors(questions, text, n)
    return check


def error_report(check: ExamCheck) -> str:
    """The exam's name, how many errors, and each error (no warnings, no counts)."""
    lines = [f"exam{check.n}.md — {len(check.errors)} error(s):"]
    lines += [f"  - {text}" for text in check.errors]
    return "\n".join(lines)


def exam_files(exam_dir: Path, n: int) -> list[Path]:
    """The files of exam `n` that exist (links included, not followed)."""
    paths = [Path(exam_dir) / part.format(n=n) for part in _EXAM_PARTS]
    return [p for p in paths if os.path.lexists(p)]


class FolderChanged(Exception):
    """The exam folder is not (or no longer) the folder it should be (message says why)."""


def open_folder(exam_dir: Path, identity: tuple[int, int] | None = None) -> int:
    """A file descriptor for `exam_dir` (the caller closes it), opened without following a
    symlink in its last component. With `identity` (st_dev, st_ino), the folder opened must
    be that one. Raises FolderChanged if it is a symlink, not a folder, cannot be opened or
    is another folder."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(exam_dir, flags)
    except OSError as e:
        if os.path.islink(exam_dir):
            raise FolderChanged(f"{Path(exam_dir).name} is a symlink") from e
        raise FolderChanged(f"{Path(exam_dir).name} cannot be opened as a folder "
                            f"({e.strerror or e})") from e
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or (identity is not None
                                        and (st.st_dev, st.st_ino) != tuple(identity)):
        os.close(fd)
        raise FolderChanged(f"{Path(exam_dir).name} is not the folder the session started in "
                            "(it was moved or replaced)")
    return fd


def file_print(st: os.stat_result) -> list[int]:
    """What identifies a file's current version: [st_dev, st_ino, st_size, st_mtime_ns]."""
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]


def delete_exam(exam_dir: Path, n: int, keep: tuple[str, ...] = (), *,
                folder: tuple[int, int] | None = None,
                protected: frozenset[tuple[int, int]] = frozenset(),
                recorded: dict[str, list[int]] | None = None) -> tuple[list[Path], str | None]:
    """Delete `exam<n>.md`, `exam<n>_answers.md` and `exam<n>_full.md` (whichever exist)
    from `exam_dir`, and nothing else; a kind named in `keep` ("answers", "full") is left
    alone. Returns the deleted paths and, if something could not be deleted, why (then
    nothing more is deleted). The questions file goes first, so a failure never leaves an
    exam without its answers.

    The names come only from exact matches in the folder's listing (on a case-insensitive
    file system `exam3.md` would otherwise also name `Exam3.md`), and every unlink is
    made relative to one descriptor of the folder, which must be `folder` (st_dev, st_ino)
    when that is given, so a folder swapped for another or for a symlink cannot redirect it.

    Refuses, deleting nothing, if `exam_dir` is a symlink, not a folder or not `folder`, or
    if any file to delete is a symlink, not a regular file, a file whose (st_dev, st_ino)
    is in `protected` (it existed before the session, under some name), or, when
    `recorded` is given, not exactly the file recorded there (`file_print`)."""
    exam_dir = Path(exam_dir)
    try:
        fd = open_folder(exam_dir, folder)
    except FolderChanged as e:
        return [], str(e)
    try:
        listing = set(os.listdir(fd))
        names = [part.format(n=n) for kind, part in zip(PART_KINDS, _EXAM_PARTS)
                 if kind not in keep]
        names = [name for name in names if name in listing]
        for name in names:
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError as e:
                return [], f"{name} cannot be looked at ({e.strerror or e})"
            if stat.S_ISLNK(st.st_mode):
                return [], f"{name} is a symlink"
            if not stat.S_ISREG(st.st_mode):
                return [], f"{name} is not a regular file"
            if (st.st_dev, st.st_ino) in protected:
                return [], (f"{name} is a file that was already in the module folder or "
                            f"{EXAM_DIR}/ when the session started (moved, renamed or linked "
                            "to this name), so it is not this session's")
            if recorded is not None and recorded.get(name) != file_print(st):
                return [], (f"{name} is not the file notes recorded for this session "
                            "(written or changed after notes last recorded the session's "
                            "files)")
        deleted = []
        for name in names:
            try:
                os.unlink(name, dir_fd=fd)
            except OSError as e:
                return deleted, f"cannot delete {exam_dir / name}: {e.strerror or e}"
            deleted.append(exam_dir / name)
        return deleted, None
    finally:
        os.close(fd)
