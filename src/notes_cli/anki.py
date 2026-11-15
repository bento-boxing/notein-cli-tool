"""Anki tab-separated card files: the header, and a checker for files Claude writes.

A card file is `Flashcards/<MODULE> Week NN.txt`: UTF-8, the six header lines from
`header()`, then one card per line, `id<TAB>front<TAB>back<TAB>tags`. The header makes
Anki read column 1 as the note's GUID (re-importing updates a card instead of adding a
duplicate) and column 4 as space-separated tags; `#html:true` makes the fields HTML.

Two companion files keep card ids safe across sessions (the prompt explains them):

- `Flashcards/<MODULE> Week NN last-id.txt`: one line, the highest id ever issued for
  that week (`<MODULE>-WNN-NNNN`). New ids continue from it, so an id is never issued
  twice even after the user removes rows from the card file.
- `Flashcards/backups/<MODULE> Week NN <YYYYMMDD-HHMMSS>.txt`: a copy of the card file
  made once per session, before its first change. The stamp is the session's.

`check_file` reports everything that would make the import fail or go wrong silently,
or would lose a card, one problem per string. Each names its line (or the file, or the
card) and says how to fix it without deleting a card. It checks:

- the encoding: UTF-8, without a byte-order mark;
- the six header lines: missing, duplicated, out of order, unexpected extra `#` lines,
  a malformed deck line, a deck that disagrees with the file name `<MODULE> Week NN.txt`
  (or a file name that does not follow that pattern), and blank lines between the header
  and the first card;
- each row: exactly 4 tab-separated fields; non-empty id, front and back; ids unique in
  the file and across the module's other week files in the same folder, and free of
  whitespace; no two cards with the same front; no row starting with `#`; no literal line
  break, carriage return or other control character inside a field (line breaks must be
  `<br>`); no field starting with `"` (Anki would read it as a quoted field);
- in front and back (not tags): no `$…$` maths (must be `\\( \\)` / `\\[ \\]`); balanced
  `\\( \\)` and `\\[ \\]` (a LaTeX `\\\\` row break is not a delimiter; text inside
  `<code>`/`<pre>` is not checked for maths); no `<` that is not a lower-case HTML tag and
  no bare `&` (fields are HTML: they must be `&lt;` and `&amp;`);
- that there is at least one card;
- against the newest backup: every id in it is still in the file;
- against the last-id record: it exists when the file has ids in the module's scheme,
  and no id in the file is above it.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

DECK_ROOT = "CS Degree"
HEADER_LINES = (
    "#separator:tab",
    "#html:true",
    "#notetype:Basic",
    None,  # the deck line, which depends on module and week
    "#guid column:1",
    "#tags column:4",
)
DECK_TEMPLATE = "#deck:CS Degree::<MODULE>::Week NN"
FILE_NAME_RE = re.compile(r"^(?P<module>.+) Week (?P<week>\d\d)\.txt$")
_DECK_RE = re.compile(r"^#deck:CS Degree::(?P<module>[^:]+(?::[^:]+)*)::Week (?P<week>\d\d)$")
FIELDS = ("id", "front", "back", "tags")
BACKUP_DIR = "backups"
STAMP_RE = r"\d{8}-\d{6}"

# Tags a card may use. Anything else after "<" is reported, because with `#html:true` a
# literal "<" (as in "a < b", "List<String>" or "<B>") hides text from the card. Only
# lower-case names count: the prompt asks for lower-case tags, so "<B>" is taken as text.
_HTML_TAGS = {
    "a", "b", "big", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt", "em",
    "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li",
    "mark", "ol", "p", "pre", "s", "samp", "small", "span", "strike", "strong", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul", "var",
}
_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)(?:\s[^<>]*)?/?>")
_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")
_DOLLAR_MATHS_RE = re.compile(r"(?<!\\)\$\$?[^$]+?(?<!\\)\$")
_CODE_RE = re.compile(r"<(code|pre)(?:\s[^<>]*)?>.*?</\1>", re.DOTALL)


def module_id(module: str) -> str:
    """The module as it appears in card ids and tags (no whitespace)."""
    return re.sub(r"\s+", "_", module)


def deck_line(module: str, week: int) -> str:
    if not 0 <= week <= 99:
        raise ValueError(f"week must be between 0 and 99, not {week}")
    return f"#deck:{DECK_ROOT}::{module}::Week {week:02d}"


def header(module: str, week: int) -> str:
    """The six header lines of a card file, newline-terminated."""
    lines = [deck_line(module, week) if line is None else line for line in HEADER_LINES]
    return "\n".join(lines) + "\n"


def last_id_name(module: str, week: str) -> str:
    """File name of the last-issued-id record for a week (`week` as two digits)."""
    return f"{module} Week {week} last-id.txt"


def backup_name(module: str, week: str, stamp: str) -> str:
    """File name (inside `backups/`) of a session backup of a week's card file."""
    return f"{module} Week {week} {stamp}.txt"


def count_cards(path: Path) -> int:
    """The number of card rows (non-empty lines not starting with `#`) in a card file."""
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    return sum(1 for line in text.split("\n") if line.strip() and not line.startswith("#"))


def _show(text: str, limit: int = 60) -> str:
    """A short, single-line rendering of `text` for a problem message."""
    shown = "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)
    return shown if len(shown) <= limit else shown[: limit - 1] + "…"


def _ids_in(path: Path) -> dict[str, int]:
    """id → line number for the rows of a card file (best effort; {} if unreadable)."""
    ids: dict[str, int] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ids
    for number, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if line.strip() and not line.startswith("#"):
            ids.setdefault(line.split("\t", 1)[0].strip(), number)
    return ids


def check_file(path: Path) -> list[str]:
    """Problems that would stop `path` importing cleanly into Anki or lose a card;
    [] means fine."""
    path = Path(path)
    raw = path.read_bytes()
    problems: list[str] = []

    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("file starts with a UTF-8 byte-order mark; save it without one "
                        "(Anki may not recognise the first header line)")
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        line = raw[: e.start].count(b"\n") + 1
        return [f"file is not valid UTF-8 (first bad byte on line {line}); save it as UTF-8"]

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the newline that ends the last line
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]  # CRLF is fine

    n_header = 0
    while n_header < len(lines) and lines[n_header].startswith("#"):
        n_header += 1

    module, week = _check_header(path, lines[:n_header], problems)
    i = n_header
    while i < len(lines) and lines[i].strip() == "" and any(l.strip() for l in lines[i:]):
        problems.append(f"line {i + 1}: blank line between the header and the first card; "
                        "remove it")
        i += 1
    ids = _check_rows(path, lines, n_header, module, problems)
    if module and week:
        present = {line.split("\t", 1)[0].strip() for line in lines[n_header:]
                   if line.strip() and not line.startswith("#")}
        _check_against_backup(path, module, week, present, problems)
        _check_last_id(path, module, week, ids, problems)
    return problems


def _check_header(path: Path, header_lines: list[str],
                  problems: list[str]) -> tuple[str | None, str | None]:
    """Check the leading `#` lines; returns the module and week the file is for, if known."""
    named = FILE_NAME_RE.match(path.name)
    deck_index = HEADER_LINES.index(None)
    order = ", ".join(line or DECK_TEMPLATE for line in HEADER_LINES)

    def kind(line: str) -> int | None:
        """Index of the expected header line this is, or None."""
        if line.startswith("#deck:"):
            return deck_index
        return HEADER_LINES.index(line) if line in HEADER_LINES else None

    seen: dict[int, int] = {}  # expected index -> line number of first occurrence
    present: list[tuple[int, int]] = []  # (line number, expected index)
    for number, line in enumerate(header_lines, 1):
        k = kind(line)
        if k is None:
            problems.append(f"line {number}: unexpected header line '{_show(line)}'; remove it "
                            "(the header is exactly six lines: " + order + ")")
        elif k in seen:
            problems.append(f"line {number}: duplicate header line '{_show(line)}' "
                            f"(already on line {seen[k]}); remove this one")
        else:
            seen[k] = number
            present.append((number, k))

    expected_order = sorted(k for _, k in present)
    for (number, k), wanted in zip(present, expected_order):
        if k != wanted:
            problems.append(
                f"line {number}: header line '{_show(header_lines[number - 1])}' is out of "
                f"order; put the header lines in this order: {order}")

    module = named["module"] if named else None
    week = named["week"] if named else None
    for k, line in enumerate(HEADER_LINES):
        if k in seen:
            continue
        if line is None:
            line = (f"#deck:{DECK_ROOT}::{module}::Week {week}" if named else DECK_TEMPLATE)
        problems.append(f"missing header line '{line}'; add it at the top, in this order: "
                        f"{order}")

    if deck_index in seen:
        number = seen[deck_index]
        deck = _DECK_RE.match(header_lines[number - 1])
        if not deck:
            wanted = (f"#deck:{DECK_ROOT}::{named['module']}::Week {named['week']}" if named
                      else DECK_TEMPLATE)
            problems.append(f"line {number}: the deck line must be '{DECK_TEMPLATE}' with a "
                            f"two-digit week; found '{_show(header_lines[number - 1])}'. "
                            f"Write '{wanted}'")
        elif named and (deck["module"], deck["week"]) != (named["module"], named["week"]):
            problems.append(f"line {number}: the deck is {deck['module']} Week {deck['week']} "
                            f"but the file is named '{path.name}'; make the deck line "
                            f"'#deck:{DECK_ROOT}::{named['module']}::Week {named['week']}' "
                            "if the file name is right")
        elif not named:
            module, week = deck["module"], deck["week"]
    if not named:
        problems.append(f"file name '{_show(path.name)}' should be '<MODULE> Week NN.txt'"
                        + (f"; rename it to '{module} Week {week}.txt'" if module else ""))
    return module, week


def _check_rows(path: Path, lines: list[str], start: int, module: str | None,
                problems: list[str]) -> dict[str, int]:
    """Check the card rows; returns id → line number of the well-formed cards."""
    ids: dict[str, int] = {}
    fronts: dict[str, int] = {}
    cards = 0
    i = start
    while i < len(lines):
        first = i + 1  # 1-based line number
        line = lines[i]
        i += 1
        if line.strip() == "":
            continue
        if line.startswith("#"):
            problems.append(f"line {first}: starts with '#', so Anki skips it as a header or "
                            "comment; if it is a stray line, remove it; if it is a card, its "
                            "id must not start with '#' (Anki never imported it, so give it a "
                            "new id as the id rules say)")
            continue
        where = f"line {first}"
        if line.count("\t") < 3:
            # Perhaps a field broken over several lines by a literal line break.
            joined, j = line, i
            while joined.count("\t") < 3 and j < len(lines) and lines[j].count("\t") < 3 \
                    and lines[j].strip() != "" and not lines[j].startswith("#"):
                joined += "\n" + lines[j]
                j += 1
            if j > i and joined.count("\t") == 3:
                problems.append(f"lines {first}-{j}: a field contains a literal line break; "
                                "join the lines and write <br> where the break was")
                line, i, where = joined.replace("\n", "<br>"), j, f"lines {first}-{j}"
        fields = line.split("\t")
        if len(fields) != 4:
            problems.append(f"{where}: a card needs exactly 4 tab-separated fields "
                            f"(id, front, back, tags); found {len(fields)}. Use one tab "
                            "between fields and none inside them (an empty tags field still "
                            "needs the tab before it)")
            continue
        cards += 1
        _check_card(where, fields, problems)

        card_id, front = fields[0].strip(), fields[1].strip()
        if card_id:
            if card_id in ids:
                problems.append(f"{where}: duplicate id '{_show(card_id)}' (first used on line "
                                f"{ids[card_id]}); keep it on the older card and give this one "
                                "a new id")
            else:
                ids[card_id] = first
        if front:
            key = re.sub(r"\s+", " ", front).casefold()
            if key in fronts:
                problems.append(f"{where}: same front as line {fronts[key]}; reword one of the "
                                "two fronts so the questions differ (if they are really the "
                                "same card, keep both, reword the newer one and list it as a "
                                "proposed removal)")
            else:
                fronts[key] = first

    if cards == 0:
        problems.append("file has no cards; add the week's cards below the header")
    if module:
        _check_ids_across_weeks(path, module, ids, problems)
    return ids


def _check_card(where: str, fields: list[str], problems: list[str]) -> None:
    card_id = fields[0]
    if not card_id.strip():
        problems.append(f"{where}: empty id; give the card a new id as the id rules say")
    for name, value in zip(FIELDS[1:3], fields[1:3]):
        if not value.strip():
            problems.append(f"{where}: empty {name}; write the card's {name}")
    if card_id.strip() and re.search(r"\s", card_id.strip()):
        problems.append(f"{where}: id '{_show(card_id)}' contains whitespace; replace each "
                        "space with '-'")
    elif card_id != card_id.strip():
        problems.append(f"{where}: id '{_show(card_id)}' has leading or trailing spaces; "
                        "remove them")

    for name, value in zip(FIELDS, fields):
        if "\r" in value:
            problems.append(f"{where}: {name} contains a carriage return; remove it, or write "
                            "<br> if it was meant as a line break")
        controls = sorted({ch for ch in value if ch != "\r"
                           and unicodedata.category(ch) == "Cc"})
        if controls:
            codes = ", ".join(f"U+{ord(ch):04X}" for ch in controls)
            problems.append(f"{where}: {name} contains the control character(s) {codes}; "
                            "remove them")
        if value.startswith('"'):
            problems.append(f"{where}: {name} starts with a double quote, which Anki reads as "
                            "the start of a quoted field; write &quot; instead")
        if name in ("id", "tags"):
            continue
        maths_text = _CODE_RE.sub("", value)  # MathJax and $ are not checked inside code
        if _DOLLAR_MATHS_RE.search(maths_text):
            problems.append(f"{where}: {name} uses $…$ maths; write \\( \\) for inline maths "
                            "and \\[ \\] for display maths (and &#36; for a dollar sign)")
        for opener, closer in (("\\(", "\\)"), ("\\[", "\\]")):
            if not _balanced(maths_text, opener, closer):
                problems.append(f"{where}: {name} has unbalanced {opener} {closer} maths "
                                f"delimiters; close each {opener} with {closer}, and write a "
                                "plain bracket without the backslash where no maths is meant")
        without_tags = _TAG_RE.sub(lambda m: "" if m[1] in _HTML_TAGS else m[0], value)
        if "<" in without_tags:
            snippet = without_tags[without_tags.index("<"):][:20]
            problems.append(f"{where}: {name} contains a '<' that is not a lower-case HTML tag "
                            f"('{_show(snippet)}'); write &lt; (and &gt; for '>')")
        if "&" in _ENTITY_RE.sub("", value):
            problems.append(f"{where}: {name} contains a bare '&'; write &amp;")


def _balanced(value: str, opener: str, closer: str) -> bool:
    """True if `opener`/`closer` alternate properly (no nesting, every one closed).
    A `\\\\` (LaTeX row break) is skipped first, so `\\\\[4pt]` and `\\\\(b)` are not
    delimiters."""
    depth = 0
    for token in re.findall(r"\\\\|\\[()\[\]]", value):
        if token == opener:
            if depth:
                return False
            depth = 1
        elif token == closer:
            if not depth:
                return False
            depth = 0
    return depth == 0


def _check_ids_across_weeks(path: Path, module: str, ids: dict[str, int],
                            problems: list[str]) -> None:
    """Report ids also used by the module's other week files next to this one: importing
    one file would then overwrite a card from another week."""
    try:
        siblings = sorted(path.parent.iterdir())
    except OSError:
        return
    for other in siblings:
        named = FILE_NAME_RE.match(other.name)
        if (not named or named["module"] != module or other.name == path.name
                or not other.is_file()):
            continue
        for card_id, number in _ids_in(other).items():
            if card_id in ids:
                problems.append(f"line {ids[card_id]}: id '{_show(card_id)}' is also used in "
                                f"'{other.name}' (line {number}); ids must be unique across "
                                "the module. The card in the file whose week matches the id's "
                                "WNN keeps it; give the other card a new id")


def newest_backup(path: Path, module: str, week: str) -> Path | None:
    """The newest session backup of the card file at `path`, if any."""
    folder = path.parent / BACKUP_DIR
    pattern = re.compile(rf"^{re.escape(module)} Week {week} ({STAMP_RE})\.txt$")
    try:
        found = [(m[1], p) for p in folder.iterdir() if (m := pattern.match(p.name))]
    except OSError:
        return None
    return max(found)[1] if found else None


def _check_against_backup(path: Path, module: str, week: str, present: set[str],
                          problems: list[str]) -> None:
    backup = newest_backup(path, module, week)
    if backup is None:
        return
    shown = f"{BACKUP_DIR}/{backup.name}"
    for card_id, number in _ids_in(backup).items():
        if card_id and card_id not in present:
            problems.append(f"card {_show(card_id)} was in the previous version ('{shown}', "
                            f"line {number}) and is missing: cards are never deleted. Copy its "
                            "row back from that file and list it as a proposed removal instead")


def _check_last_id(path: Path, module: str, week: str, ids: dict[str, int],
                   problems: list[str]) -> None:
    scheme = re.compile(rf"^{re.escape(module_id(module))}-W{week}-(\d{{4}})$")
    serials = [(int(m[1]), card_id, number) for card_id, number in ids.items()
               if (m := scheme.match(card_id))]
    record = path.parent / last_id_name(module, week)
    example = f"{module_id(module)}-W{week}-NNNN"
    if not serials:
        return
    highest, highest_id, line = max(serials)
    try:
        recorded = record.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        problems.append(f"file '{record.name}' is missing; create it next to the card file, "
                        f"containing {highest_id}, the highest id issued so far for this week")
        return
    except (OSError, UnicodeDecodeError) as e:
        problems.append(f"file '{record.name}' cannot be read ({e}); rewrite it as one line, "
                        f"the highest id issued for this week (e.g. {highest_id})")
        return
    m = scheme.match(recorded)
    if not m:
        problems.append(f"file '{record.name}' must contain one id of the form {example}; "
                        f"found '{_show(recorded)}'. Write the highest id ever issued for this "
                        f"week there (at least {highest_id})")
    elif int(m[1]) < highest:
        problems.append(f"line {line}: id {highest_id} is higher than the last issued id "
                        f"recorded in '{record.name}' ({recorded}); write {highest_id} into "
                        "that file")
