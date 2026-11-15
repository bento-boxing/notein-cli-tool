# notes

`notes` is a command-line tool for revising from handwritten Notein notes. It copies
the Notein backup from Google Drive, picks out the notes taken in lectures and tutorials
(LGT and SGT), and turns each one into a PDF in a folder per module. It can then start a
Claude Code session in a module's folder that turns the slides and notes into Anki
flashcards or practice exams. It never writes to Google Drive and never changes the
backup or your slides. The only files it replaces are ones it made itself: its PDFs, the
two brief files it writes for Claude, and merged exams. It deletes files in one case
only: at the end of a `notes exam MODULE` session it deletes exams written in that
session that are still not valid, with their answers and merged files (see
[Checks and deletion](#checks-and-deletion)). It never deletes anything else.

The design is in `docs/superpowers/specs/2026-11-14-notes-cli-design.md`; this file
describes what the code actually does.

## Contents

- [The folders](#the-folders)
- [The usual routine](#the-usual-routine)
- [Setting up](#setting-up)
- [Commands](#commands)
- [How `notes` finds the data folder](#how-notes-finds-the-data-folder)
- [Adding a module](#adding-a-module)
- [Flashcards in Anki](#flashcards-in-anki)
- [Practice exams](#practice-exams)
- [Known limits](#known-limits)
- [For working on the code](#for-working-on-the-code)

## The folders

Everything lives in `/workspaces/NoteinNotes` (in the dev container):

```
NoteinNotes/
├── notes-cli/                  this tool's code (a git repository)
└── data/                       your data ("the data folder")
    ├── notes.toml              settings: modules, folder names, rclone remote
    ├── .notes-manifest.json    the tool's record of which PDF it wrote for which note
    ├── NoteInDataSync/         the Notein backup, copied from Google Drive; only read
    ├── Notein/                 an older copy of part of the backup; ignored
    └── CS Degree/
        └── Year N/
            └── MODULE/             e.g. Year 2/FM2
                ├── Lecture Notes/      PDFs of lecture notes      (written by notes convert)
                ├── LGT/                PDFs of LGT notes          (written by notes convert)
                ├── SGT/                PDFs of SGT and lab notes  (written by notes convert)
                ├── Lecture-Slides/     the lecturer's slides; yours, never touched
                ├── Flashcards/         written by the flashcards Claude session
                └── Practice Exams/     written by the exam Claude session
```

`Lecture-Slides/` exists only where you made it (at present FM2 and IAS). You put the
slide PDFs there yourself, in any subfolders you like (FM2 uses `Week 1 Slides-…/`, IAS
`Week 01/`). The tool reads them for flashcards and exams and never changes them.

## The usual routine

```
notes sync                  # 1. copy the latest backup from Google Drive
notes organise              # 2. check where each note will go (optional)
notes convert               # 3. make PDFs of new and changed notes
notes flashcards FM2        # 4. make or update flashcards for a module
notes exam IAS              #    or practice exams
```

`notes all` does steps 1 to 3 in one go. Every step is safe to repeat: `convert` only
works on notes that are new or changed since the last run.

## Setting up

**Inside the dev container there is nothing to install.** The tool is already installed
in the Python environment at `/home/vscode/venv` in "editable" mode, which means changes
to the code take effect immediately, and `/home/vscode/venv/bin` is on the PATH, so
`notes` works in any terminal. (`uv` is not installed in the container.)

**On another computer** (not tried; only the container set-up has been checked): with
Python 3.12 or later, run either

```
uv tool install --editable /path/to/notes-cli
pip install -e /path/to/notes-cli          # inside a virtual environment
```

(A virtual environment is a private Python install for one project: `python3 -m venv ~/notes-venv`
creates one, and its programs, including `notes`, are then in `~/notes-venv/bin`.) Make sure
the data folder is found (see [below](#how-notes-finds-the-data-folder)). `rclone`, `claude`
and `pdftotext`/`pdftoppm` have to be installed there too.

**Other programs it uses**, none of which it installs or sets up for you:

| Program | Needed for | In the container |
|---|---|---|
| `rclone` | `notes sync`, `notes all` | installed |
| `claude` (Claude Code) | `notes flashcards MODULE`, `notes exam MODULE` | installed |
| `pdftotext`, `pdftoppm` | used by Claude in those sessions to read PDFs | installed |
| `pandoc` | turning a merged exam into PDF | **not installed** |
| Anki | studying the flashcards | not installed (it runs on your own computer) |

**One-time step: the rclone remote.** `notes sync` copies from the rclone "remote" (a
saved connection to a cloud drive) named in `data/notes.toml`:

```
remote = "my-drive:NoteInDataSync"
```

That is the folder `NoteInDataSync` on the remote `my-drive:`, which is your Google
Drive with read-only access. The remote has to exist before `notes sync` can work. If it
does not (for example on a new computer), create it once with `rclone config`: choose
"New remote", name it `my-drive`, type Google Drive, and the read-only scope. The
tool never runs `rclone config` itself. These steps are the usual rclone procedure and
were not re-run while building this tool. Two things to expect: signing in to Google needs
a web browser, so inside the container rclone will ask you to finish the sign-in on a
computer that has one (it prints the instructions); and if `notes sync` later says the
sign-in has expired, `rclone config reconnect my-drive:` renews it.

**Claude Code** must be logged in (run `claude` once by itself and follow the prompts).

## Commands

Every command can be given the data folder first: `notes --data /some/folder convert`
(the `--data` goes *before* the command name). Usually you do not need it.

What the exit code means (useful in scripts; otherwise ignore it):

| Code | Meaning |
|---|---|
| 0 | done, nothing failed |
| 1 | something failed: a note could not be converted, a program is missing, no sources, an exam has errors; for `notes exam MODULE`: an exam of the session was not valid (and was deleted, or could not be), or something could not be checked |
| other | `notes sync` and `notes all` pass on rclone's own exit code when the copy fails |
| 2 | the command was typed wrongly, or `notes.toml` is missing or invalid |
| 130 | you pressed Ctrl-C (`notes: interrupted; everything finished so far is kept`) |

### `notes sync`

Runs `rclone copy my-drive:NoteInDataSync data/NoteInDataSync --progress`, so you
see rclone's own progress output. `rclone copy` only adds and updates files: it never
deletes anything in `data/NoteInDataSync` and never writes to Google Drive. Needs
network access and the rclone remote.

| Message | What to do |
|---|---|
| `notes: rclone is not installed or not on PATH` | install rclone |
| `notes: rclone exited with code N` | read rclone's error above it; usually there is no network, or the remote is missing or its Google login has expired (fix that with `rclone config`) |

### `notes organise`

Reads the backup, works out the Notein folder of every note, and prints where each note
goes or why it is skipped. It creates the folders `Lecture Notes`, `LGT` and `SGT` for
every module in `notes.toml` (even when they stay empty). It writes no PDFs. Use it to
check what `convert` will do, or to find out why a note has no PDF.

Example (made-up notes, shortened):

```
$ notes organise
Quick Start Guide -- skipped: outside degree tree (in (top level))
...
CA1 Week 2 Exercises -- skipped: not in a lecture/LGT/SGT folder (in Uni CS/Year 1/Semester 1/CA1)
...
DBA W1 Summary -- skipped: module not configured (in Uni CS/Year 1/Semester 2/Revision/DBA/DBA Lecture)
...
FM2 W6 -> CS Degree/Year 2/FM2/Lecture Notes/FM2 W6.pdf
FM2 W6 LGT -> CS Degree/Year 2/FM2/LGT/FM2 W6 LGT.pdf
...

120 notes: 80 mapped, 40 skipped; 0 bad entries
  skipped, module not configured: 22
  skipped, not in a lecture/LGT/SGT folder: 15
  skipped, outside degree tree: 3
```

Which notes are converted is decided only by the Notein folder a note is in, not by its
title. A note is converted when its folder is

```
Uni CS / Year N / [Semester M /] MODULE / SUBFOLDER
```

where `MODULE` is listed in `notes.toml` with year N (upper/lower case does not matter),
the `Semester M` level is optional (SWE has none), and `SUBFOLDER`, after removing the
module name from its start, is one of these (again ignoring case):

| Notein subfolder (examples) | PDF goes to |
|---|---|
| `Lecture`, `Lecture Notes`, `Slides` (`DBA Lecture Notes`, `FM1 Slides`) | `Lecture Notes/` |
| `LGT`, `LGT Notes` (`DBA LGT Notes`, `FM2 LGT`) | `LGT/` |
| `SGT`, `SGT Notes` (labs filed there included) | `SGT/` |

Everything else is skipped, with one of these reasons:

| Reason | Meaning |
|---|---|
| `module not configured` | the folder under `Year N` is not a module in `notes.toml` (or is listed with another year), for example an extra `Revision` folder between the year and the module |
| `not in a lecture/LGT/SGT folder` | the note sits directly in the module folder, in another subfolder (`Question Sheets`, `Textbook Notes`, `CW Notes`…), or one level deeper. A module whose notes all sit directly in its folder gets no PDFs |
| `outside degree tree` | not under `Uni CS/Year N` (the top folder must be named exactly `Uni CS`, capitals included) |
| `in trash` | in the Notein bin |

To have a skipped note converted, move it into a lecture, LGT or SGT folder in Notein
(then sync and convert), or [add the subfolder name](#adding-a-module) to `notes.toml`.

**PDF names** are the note's Notein title, with characters that are not allowed in file
names (`/ \ : * ? " < > |`) replaced by `-` and leading dots removed. If two notes in the
same folder would get the same name (ignoring case), both get part of their Notein id
added, e.g. `FM2 W1 (3f2a9c1d).pdf`.

**"bad entry" lines** are backup files the tool could not use; the run carries on:

| Message | What to do |
|---|---|
| `bad entry: NAME -- not a readable zip (corrupt or incomplete download?)` | run `notes sync` again |
| `bad entry: OLD_NAME -- older copy of note ID (newer copy: NEW_NAME)` | nothing: you renamed the note in Notein, the backup keeps the file under its old name too (sync never deletes), and the tool uses the newer one |
| `bad entry: OLD_NAME -- older copy of folder ID (newer copy: NEW_NAME)` | nothing: the same, for a folder you renamed in Notein |

One message stops the command instead (nothing is listed and no folders are made):
`notes: backup folder … not found; run 'notes sync' first`. Run `notes sync`.

### `notes convert [MODULE] [--force]`

Makes a PDF for every converted note (see the rule above) that is new or has changed in
Notein since the last run, or whose PDF is missing. `notes convert FM2` does only FM2.
Unchanged notes are counted, not redone. A full conversion of about 200 notes takes roughly
100 to 160 seconds; a run with nothing changed takes about 2 seconds.

What it writes: the PDFs, the folders they go in, and `data/.notes-manifest.json` (its
record of note to PDF). Nothing else, and it never deletes a file.

Each PDF has one page per Notein page, the same size, with the ink, straight lines,
pictures and text boxes. Notes written on top of an imported PDF (slides, tutorial
sheets) show the original pages, still with selectable text and working links, with the
ink on top. See [Known limits](#known-limits) for what is not drawn.

Example (from a test folder):

```
$ notes convert
[1/2] FM2 W2 LGT Simplex -> CS Degree/Year 2/FM2/LGT/FM2 W2 LGT Simplex.pdf
[2/2] FM2 W3 LGT -- failed: CS Degree/Year 2/FM2/LGT/FM2 W3 LGT.pdf exists and was not made by notes; not overwritten. Move or rename it to convert this note

1 converted, 3 unchanged, 4 skipped, 1 failed; 0 warnings, 1 orphan, 1 bad entry
  skipped, in trash: 1
  skipped, module not configured: 1
  skipped, not in a lecture/LGT/SGT folder: 1
  skipped, outside degree tree: 1
  ('notes organise' lists every skipped note)
failed: FM2 W3 LGT -- CS Degree/Year 2/FM2/LGT/FM2 W3 LGT.pdf exists and was not made by notes; not overwritten. Move or rename it to convert this note
orphan: CS Degree/Year 2/FM2/LGT/FM2 W2 LGT.pdf -- no note is converted to this file any more; left in place
bad entry: FM2 W2 LGT_n003aaaaaaaa -- older copy of note n003aaaaaaaa (newer copy: FM2 W2 LGT Simplex_n003aaaaaaaa)
```

**Which files it will overwrite.** Every PDF the tool makes carries a hidden stamp
(in the PDF's document properties: creator `notes-cli`, subject `Notein note <id>`). It
only replaces PDFs that it made and put there itself: normally the same note's earlier
PDF, and, after you rename notes, the out-of-date PDF of another note whose new PDF is
already safely in place. Anything else at a note's place is left alone and the note is
reported as failed until you move the file. So:

- **Do not annotate a converted PDF where it is.** The next time that note changes in
  Notein (or you use `--force`) the tool replaces it and your annotations are gone.
  Copy it somewhere else first (outside `Lecture Notes/`, `LGT/`, `SGT/`).
- Do not put your own files in `Lecture Notes/`, `LGT/` or `SGT/` under a note's name.

**Messages and what to do:**

| Message | What to do |
|---|---|
| `failed: … exists and was not made by notes; not overwritten` | a file of yours (or a damaged file) is where this note's PDF goes. Move or rename it, then run `notes convert` again |
| `failed: … holds the PDF of another note (…) that is no longer converted` / `… is a copy of the PDF of another note …` | same: move or rename that file |
| `failed: … still holds the PDF of another note … Convert that note first` | run `notes convert` again without a module name |
| `failed: TITLE -- SomeError: …` | the note itself could not be read or drawn. The other notes are still done; the old PDF, if any, is kept. Try `notes sync` (the download may be incomplete); if it persists it is a bug |
| `orphan: PATH -- no note is converted to this file any more; left in place` | a note was renamed, moved to another folder or put in the Notein bin, and this is its old PDF. The new one (if any) has been written. Delete the old file yourself when you no longer want it; until then it is reported on every run |
| `warning: TITLE -- …` (often `page N: …`) | the PDF was made but something was left out or changed (for example an unreadable picture, or an imported PDF missing from the backup); the message says what |
| `warning: .notes-manifest.json … kept as .notes-manifest.json.corrupt-…` | the record was damaged and has been put aside; every note is converted again this run. Nothing to do |

`--force` re-makes every PDF in scope even if unchanged (use it after the drawing code
has been improved). It does not override the overwriting rule above.

### `notes all`

`notes sync`, then `notes organise`, then `notes convert`, stopping at the first that
fails (for example, no network means nothing is converted). It takes no module name and
no `--force`. The output is that of the three commands one after the other.

### `notes flashcards MODULE [--week N]`

Prepares a brief for Claude and starts Claude Code in the module's folder, where Claude
writes the flashcards. **The tool itself writes no cards.** It writes one file,
`MODULE/Flashcards/FLASHCARDS_PROMPT.md` (the brief, listing the module's slides and
converted notes), then starts an ordinary interactive `claude` session in the same terminal, told
to read that file. You can talk to Claude as usual, answer its questions and steer it.
When you quit Claude, you are back at your shell.

```
$ notes flashcards FM2 --week 5
wrote CS Degree/Year 2/FM2/Flashcards/FLASHCARDS_PROMPT.md
(Claude Code starts here)
```

Without `--week`, Claude works through every week; with `--week 5`, only week 5 (see
[Week numbers](#week-numbers) for how weeks are guessed). In short, the brief tells
Claude to: first check that the slides and notes labelled with each week are about the
same topics and ask you when they are not; write a topic checklist per week; write the
cards with one helper agent per week; have a fresh agent review them and fill the gaps;
never delete a card but list proposed removals for you; run `notes flashcards check` on
every file; and finish with a report. It is also told to read only the module's own
files, to write only in `Flashcards/`, and not to search the web.

What appears in `Flashcards/`:

| File | What it is |
|---|---|
| `FM2 Week 05.txt` | the cards for week 5, ready to import into Anki |
| `FM2 Week 05 topics.md` | the topic checklist the cards were written against |
| `FM2 Week 05 last-id.txt` | the highest card id used so far for that week (so an id is never reused) |
| `backups/FM2 Week 05 20261115-143000.txt` | a copy of the card file taken at the start of each session that changed it |
| `FLASHCARDS_PROMPT.md` | the brief; rewritten every time you run the command |

Running it again for a week that already has cards makes Claude update that file rather
than start over: existing cards keep their ids (so Anki updates them instead of making
duplicates) and new cards are added at the end.

At the end Claude lists **proposed removals**: cards it thinks are unnecessary. It never
removes them itself. For each one, either ignore the suggestion or suspend the card in
Anki (see [Flashcards in Anki](#flashcards-in-anki)).

While it works, Claude may keep temporary page pictures in a hidden folder
`Flashcards/.pages/` (and `Practice Exams/.pages/` for exams). It deletes them when it
finishes; if a session is interrupted and the folder is left behind, you can delete it.

| Message | What to do |
|---|---|
| `notes: no converted notes or slides for DBA; run 'notes convert DBA' first` | run `notes convert` (or add slides) |
| `notes: no notes or slides for FM2 week 7 (…); sources exist for weeks 1, 2, 3` | pick a week from the list, or leave out `--week` |
| `notes: claude is not installed or not on PATH` | Claude Code is missing on this computer (in the container it is installed); the brief has been written anyway |
| `notes: unknown module 'XYZ'; valid modules: PRG, DBA, …` | check the spelling (case does not matter) |

### `notes flashcards check FILE`

Checks one card file for everything that would make the Anki import fail or quietly go
wrong, prints one problem per line with how to fix it, and exits 1 if there are any. It
reads only that file and the other files in the same `Flashcards` folder (the `last-id`
file, `backups/`, and the other weeks' card files, to catch an id used in two weeks),
and needs no data folder.
Claude runs it on every file it writes; run it yourself after editing a card file by hand.

```
# typed in /workspaces/NoteinNotes; the quotes are needed because of the spaces
$ notes flashcards check "data/CS Degree/Year 2/FM2/Flashcards/FM2 Week 01.txt"
data/CS Degree/Year 2/FM2/Flashcards/FM2 Week 01.txt: no problems found (2 cards)

# ("…" below stands for the same folder path as above)
$ notes flashcards check "…/FM2 Week 02.txt"
line 7: front uses $…$ maths; write \( \) for inline maths and \[ \] for display maths (and &#36; for a dollar sign)
line 7: front contains a '<' that is not a lower-case HTML tag ('< b?'); write &lt; (and &gt; for '>')
line 8: empty back; write the card's back
line 8: duplicate id 'FM2-W02-0001' (first used on line 7); keep it on the older card and give this one a new id
file 'FM2 Week 02 last-id.txt' is missing; create it next to the card file, containing FM2-W02-0001, the highest id issued so far for this week
5 problems in …/FM2 Week 02.txt
```

It also reports a card that was in the newest backup but is missing from the file
(cards should not be deleted from the file; see [Anki](#flashcards-in-anki)). If you add
a card by hand, give it the next id and write that id into the `last-id` file.

### `notes exam MODULE [--mcq PCT] [--questions N]`

This one command is the whole exam workflow. It writes
`MODULE/Practice Exams/EXAM_PROMPT.md` (the brief, based on your old exam-generator
prompt) and `MODULE/Practice Exams/exam_template.md` (only if there is none, so an edited
template is kept), then starts an interactive `claude` session in the module folder.
When you quit Claude, `notes` checks the new exams and deletes the ones that are not
valid (see [Checks and deletion](#checks-and-deletion)).

```
$ notes exam IAS --mcq 60 --questions 20
wrote CS Degree/Year 2/IAS/Practice Exams/EXAM_PROMPT.md
(Claude Code starts here; when you quit it:)

exam1.md — All checks passed
…
notes: 10 exams written in this session, all valid.
```

`--mcq 60` means 60% multiple choice; `--questions 20` means 20 questions per exam. Claude
asks you anything not given (format split, questions per exam, whether there is a
textbook or a mock exam). See [Practice exams](#practice-exams) for the rest.

Exit code: 0 when every new exam is valid; 1 when a new exam was not valid (it was
deleted, or could not be), or something could not be checked, or another session is
running; Claude's own exit code when the session wrote no exam. The 1 can also come from
the check of an earlier session that was stopped (see
[Checks and deletion](#checks-and-deletion)), which runs first.

### `notes exam validate MODULE [N|all]`, `notes exam merge MODULE [N|all]`, `notes exam stats MODULE`

Tools for the exams in `MODULE/Practice Exams/`. Claude runs them during the session; you
can run them yourself. Without a number they work on all exams (`all` is the same).

- `validate` checks each `examN.md` and exits 1 if any has errors.
- `merge` joins `examN.md` and `examN_answers.md` into `examN_full.md` (it writes only
  that file).
- `stats` lists the exams with their size and whether they have answers. It takes no
  exam number.

```
$ notes exam validate FM2
exam1.md — All checks passed
  Questions: 5, Single: 3, Multi: 2, Code blocks: 0, Maths questions: 5 (header: 5 questions, 5 multiple choice)

exam2.md — 1 error(s):
  - error: Multi-answer questions: 1/5 = 20% of the multiple-choice questions (need >=40%)
  Questions: 5, Single: 4, Multi: 1, Code blocks: 0, Maths questions: 5 (header: 5 questions, 5 multiple choice)

2 exams checked: 1 passed, 1 with errors

$ notes exam merge FM2 1
  exam1_full.md created (747 chars)
pandoc is not installed (not on PATH), so the merged exams cannot be exported to PDF here; they are ready for it once pandoc is installed
```

What `validate` checks:

- The header lines `**Total Questions: N**` and
  `**Format: X% Multiple Choice (M questions), …**` are there (the expected numbers are
  read from them), and there are at least N questions.
- A "Select …:" label on each of the M multiple-choice questions, and at least 40% of
  them multi-answer.
- No lazy stems. A stem is lazy when the whole question is just "Which of the following
  is true?" or a close variant ("Which of the following statements about paging are
  correct?", "Select the true statement about mutexes."), with at most a short, general
  topic. It is fine as soon as the stem gives a concrete case: "Which of the following is
  true of a binary search tree built by inserting 4, 2, 6 in that order?" passes.
  A question that points to shared material ("about the diagram above") or narrows the
  topic ("on graphs with negative edges") also passes, and so does a question with a
  scenario, code or a table before the "Which…" line.
- No banned options: "All of the above", "None of the above" (also in bold, italics or
  backticks), "All of the options", "None of these", "Both of the above", and "Both a and
  c" (also "Both b and d are correct", "Both a and c only"). Options in other forms (`e)`,
  `(e)`, `**e.**`, `e -`) are checked too. Real options that only start the same way pass:
  "All the statements are executed in one transaction", "None of the options are
  inherited by the child", "Both `a` and `b` are incremented" (`a` and `b` in backticks
  are code, not option letters). Text in a question that only mentions "none of the
  above" is not an error.
- No text left over from the template, such as `[Option]` or `[TOTAL]`. Ordinary
  brackets (`arr[i]`, `[0, 1]`, links) are fine.
- Fewer than 20% of questions with code or maths is only a warning.

`validate` only reads. During and after a `notes exam MODULE` session, an exam must also
have a finished answer key in `examN_answers.md`:

- an entry for every question number, in the form `**Q3:** b` (also `Q3:`, `Q3.`,
  `**Question 3**` or a heading `### Question 3`); a plain numbered line such as `3. b`
  does not count;
- no placeholder: no `[TODO]`, no answer or explanation that is only `TODO`, `TBD` or
  `FIXME`, and no line or entry that starts with `TODO:`, `TBD:` or `FIXME:`. These words
  inside a normal sentence ("The TODO comment is technical debt") are fine. The error
  gives the line numbers;
- no text left over from the template, such as `[count]` or `[Pass/Fail]`;
- the "Answer Key Distribution Audit" section the template ends with, under a heading
  (or a bold line) with that name.

Exam files must be named `exam1.md`, `exam2.md` and so on (`exam01.md` is not picked up).
Other messages you may see:

- `notes: no exams found in … (looked for exam<N>.md files)`: there is nothing to check
  or merge yet.
- `examN_answers.md not found, using questions only`: `merge` made `examN_full.md` from
  the questions alone.
- `kept your edited Practice Exams/exam_template.md …`: `notes exam` found that you had
  changed the template in that folder and left your version in place. Delete it to get
  the packaged one back next time.

Every command also has its own help: `notes COMMAND --help`, for example
`notes convert --help`.

## How `notes` finds the data folder

It looks, in this order, for:

1. the folder given with `--data` (`notes --data /workspaces/NoteinNotes/data convert`);
2. the folder in the environment variable `NOTES_DATA`;
3. a `notes.toml` in the current folder or any folder above it, or in a `data/`
   folder inside the current folder or inside any folder above it.

Because of rule 3, any terminal opened anywhere inside `/workspaces/NoteinNotes` finds
`/workspaces/NoteinNotes/data` without further ado. From somewhere else you get
`notes: no notes.toml found: pass --data, set $NOTES_DATA, or run from inside the data folder`.

## Adding a module

Edit `data/notes.toml`. It looks like this (comments added here, module list shortened):

```toml
remote = "my-drive:NoteInDataSync"   # where `notes sync` copies from
tree_root = "Uni CS"                     # the top Notein folder of the degree

[modules]          # module name = year
PRG = 1
DBA = 1
...
FM2 = 2
PAR = 2

[subfolders]       # Notein subfolder, without the module name (capitals do not matter) = PDF folder
"lecture" = "Lecture Notes"
"lecture notes" = "Lecture Notes"
"slides" = "Lecture Notes"
"lgt" = "LGT"
"lgt notes" = "LGT"
"sgt" = "SGT"
"sgt notes" = "SGT"
```

**A new module**, say `AI` in year 3: add `AI = 3` under `[modules]`. In Notein, its notes
must be in folders like `Uni CS/Year 3/Semester 1/AI/AI Lecture Notes` (or
`Uni CS/Year 3/AI/AI LGT`): the year folder named `Year 3`, an optional `Semester N`
folder, a folder named exactly like the module (case does not matter), and inside it a
subfolder whose name, without the leading `AI`, is a key of `[subfolders]`. Then run
`notes sync`, `notes organise` (which also creates `CS Degree/Year 3/AI/…`) and
`notes convert`. For slides, create `CS Degree/Year 3/AI/Lecture-Slides/` yourself and put
the PDFs in it.

**A new subfolder name**: if you call a Notein folder `AI Lectures`, add
`"lectures" = "Lecture Notes"` under `[subfolders]`. The key is the subfolder name without
the module name, in lower case. The value can also be a new PDF folder name, e.g.
`"labs" = "Labs"`; `organise` then creates a `Labs/` folder in every module and the
flashcards and exam sessions read it too.

**Choosing the Claude model**: by default `notes flashcards` and `notes exam` start
Claude Code with whatever model it would use if you typed `claude` yourself. To fix the
model, add a line at the very top of `notes.toml`, above `[modules]`:

```toml
model = "sonnet"
```

The value is passed to Claude Code as `claude --model …`, so it can be an alias such as
`"sonnet"`, `"opus"` or `"haiku"`, or a full model name. Remove the line to go back to
Claude Code's default. `notes` does not check that the model exists; if the name is wrong,
Claude Code reports it when the session starts.

What `notes.toml` refuses (the message names the problem, and every command stops with
exit code 2 until it is fixed):

- a module name that is not a plain folder name (contains `/` or `\`, is `.` or `..`, or
  starts or ends with a space): `module name 'AI/ML' must be a plain folder name`;
- a year that is not a whole number: `year for module 'AI' must be an integer` (write
  `AI = 3`, not `AI = "3"`);
- a PDF folder that is not a plain folder name, or is `Lecture-Slides`, `Flashcards` or
  `Practice Exams`: `subfolder destination 'Flashcards' for 'lgt' is reserved; …`;
- a missing `remote`, `tree_root`, `[modules]` or `[subfolders]`, or a `remote` that is
  empty or starts with `-`;
- a `model` that is empty, starts with `-`, or contains spaces or other unusual characters.

Also avoid naming a module `check`, `validate`, `merge` or `stats`: those words are read
as actions by `notes flashcards` and `notes exam`.

If you rename a PDF folder value or move a module to another year, the PDFs already made
stay where they are and are reported as orphans; delete them when you are happy with the
new ones.

## Flashcards in Anki

Anki is not installed in the container and these steps have **not been tried with a real
Anki**; they are the expected procedure. It needs Anki 2.1.55 or later (the version that
reads the header lines used below).

1. Find the week file, e.g. `data/CS Degree/Year 2/FM2/Flashcards/FM2 Week 05.txt`, on the
   computer where Anki runs (copy it there if needed; only that `.txt` file is used).
2. In Anki: **File → Import**, and choose the file.
3. The first lines of the file set everything up, so the import screen should already
   show: separator Tab, HTML allowed, note type **Basic**, deck
   **`CS Degree::FM2::Week 05`** (Anki creates the nested decks `CS Degree` → `FM2` →
   `Week 05`), column 1 as the card's id (GUID) and column 4 as tags. Check them and press
   Import.
4. Before pressing Import, check that the import screen's setting for notes that already
   exist is **Update** (expected; not tried).

Importing a newer version of the same file later should update the existing cards
(matched by their id in column 1) instead of adding duplicates, so your review history
should be kept. This only works if cards keep their ids, which is why Claude never
renumbers or deletes cards and why you should not either: removing a line from the file
does not remove the card from Anki. To retire a card, **suspend** it in Anki. If you
delete it in Anki while its line is still in the file, the next import adds it back as
a new card.

Maths is written as `\( … \)` and `\[ … \]`, which Anki shows with MathJax.

## Practice exams

`notes exam MODULE` starts a Claude session with your old exam-generator instructions,
changed as follows: the lecture slides are the main source and the handwritten notes add
extra material (a module without slides uses the notes as the main source); everything
is written to `MODULE/Practice Exams/`; and the work is done in one session by two helper
agents, exams 1 to 5 and then 6 to 10, with `concept_matrix.md` passed between them.
Claude first asks the setup questions you did not answer with `--mcq` and `--questions`.
Optional folders `TextBook/` and `MockExamReference/` inside the module folder are used if
present. If there is no textbook, the instructions (unchanged from your original prompt)
let the second batch look on the web for practice questions.

What ends up in `Practice Exams/`: `exam1.md` … `exam10.md` (questions),
`exam1_answers.md` … (answers with explanations), `concept_matrix.md`,
`exam_template.md`, `EXAM_PROMPT.md`, and `examN_full.md` for exams you merge. While a
session runs there is also `.notes-exam-session.json` (see below). If you run
`notes exam MODULE` again, the brief tells Claude about the exams already there: it must
ask you whether to replace them or keep them and number the new ones after them (11 to
20, and so on), and whether to extend or replace `concept_matrix.md`, before it writes
anything.

### Checks and deletion

`notes` checks the exams Claude writes, during the session and when it ends, and deletes
the new ones that are still not valid. Some files can still be left over; `notes` always
says so and exits with 1. They are listed under "What can be left over" below.

- **Which exams are new.** When the session starts, `notes` writes down which exam
  numbers already exist, and which files are already in `Practice Exams/`. An exam is
  "new" (belongs to this session) only if its number was not on that list. File dates are
  not used.
- **During the session.** Each time Claude is about to finish a reply, `notes` checks
  the new exams (a Claude Code "Stop hook"). If one is not valid, or has no complete
  answer key, Claude is shown the errors and cannot finish until it has fixed them. A
  file with a wrong exam-like name (`Exam3.md`, `exam03.md`, or an exam in a subfolder)
  also stops it once, because such a file would not be checked. Claude is told to rename
  it if it wrote it, and to leave it alone and tell you if it did not. The second time,
  that file alone no longer stops Claude (it may be yours); it is reported at the end.
- **When the session ends.** `notes` checks the new exams again. A new exam that is
  still not valid, or has no complete answer key, is deleted together with its
  `examN_answers.md` and `examN_full.md`. It prints what it deleted and why. Run
  `notes exam MODULE` again to have them written again.
- **Earlier exams are never deleted.** Exams that existed before the session are only
  reported: if one is not valid, or was changed during the session, `notes` says so and
  leaves it alone. This includes an exam that Claude rewrites under an existing number
  because you asked it to replace earlier exams: it does not hold Claude and is not
  deleted, but `notes` reports it if it is not valid (and exits with 1). If an earlier
  `examN_answers.md` or `examN_full.md` was already there under a number that the session
  then used for a new exam, that earlier file is kept too.
- **Nothing else is deleted.** Files with wrong exam-like names are reported, not
  deleted. `notes exam validate` and the check during the session never delete anything.
- **`concept_matrix.md` is not touched by `notes`.** After exams were deleted it may
  still mention them. The next `notes exam MODULE` session tells Claude to remove those
  mentions once you have answered its setup questions, before it writes new exams.

**What can be left over.** These are reported, never deleted, and `notes` exits with 1
(except for the first one, see there):

- an exam written under a number that already existed (replacing an earlier exam): exit
  code 1 only if it is now not valid; if it is still valid, the exit code is 0;
- a file with a wrong exam-like name (`Exam3.md`, `exam03.md`, one in a subfolder);
- an earlier file that was renamed or linked to a new exam number (`notes` recognises the
  file itself, not just its name). This includes a file that was elsewhere in the module
  folder when the session started and was moved into `Practice Exams/`;
- an exam whose files could not be read (so it could not be checked) or could not be
  deleted;
- after a stopped session (see below): an exam that the stopped session had not yet
  recorded, or that changed after it was recorded.

**The session file.** While a session runs, `Practice Exams/.notes-exam-session.json`
holds what `notes` needs to finish the job: the list of files that existed at the start,
which `notes` and which Claude are running, which folder and module it belongs to, and
the files this session has written. `notes` updates it every few seconds while Claude
runs, and once more just before the final check. It is removed after the final check. It
is not an exam, and the exam tools ignore it.

- If `notes` itself was stopped before the final check (killed, the computer crashed, or
  the dev container was stopped), the file stays. The next `notes exam MODULE` notices
  this and runs the missed check first. It deletes only files that the stopped session
  recorded as its own and that have not changed since. Anything else, such as an exam
  written in its last few seconds or one you wrote by hand afterwards, is only reported.
  Then it starts the new session.
- If you start `notes exam MODULE` while another session for the same module is still
  running, it refuses with one line and changes nothing. This also happens when that
  session's `notes` was stopped but its Claude is still running: quit that Claude first.
  If you are sure nothing is running, delete the session file and try again.
- If the session file is a link, damaged, or was made for another module or folder (for
  example copied from somewhere else), `notes` refuses to start and deletes nothing.
  Check the exams with `notes exam validate MODULE`, delete the file by hand, and start
  again.
- While a stopped session is being checked, its file is renamed to
  `.notes-exam-session.json.claimed-<number>`, so that two runs cannot check it at once.
  If you ever find such a file with no `notes exam` running, check the exams with
  `notes exam validate MODULE` and delete it by hand.

**Limits.**

- Esc does not quit Claude; it only interrupts its current reply. If you quit Claude
  yourself (Ctrl-C twice, Ctrl-D or `/exit`) while an exam is unfinished, the check during
  the session cannot hold Claude, but the check at the end still runs and deletes that
  exam. Closing the terminal, or stopping `notes` with `kill`, still lets the end check
  run. Only `kill -9`, a crash or stopping the dev container skips it; then the next
  `notes exam MODULE` does it, as described above.
- The check during the session only works once you have accepted Claude Code's "trust
  this folder" prompt for the module folder. The end check does not depend on it.
- The tests never start Claude, so the check during a real Claude session has not been
  tried here; only the command it runs has been tested (with a stand-in for Claude).

**PDF export needs pandoc, which is not installed.** `notes exam merge` still makes the
`examN_full.md` files; on a computer with pandoc (and the LaTeX it uses for PDF output)
`merge` prints the command to convert them, of the form
`pandoc 'exam1_full.md' -o 'exam1_full.pdf'` run inside `Practice Exams/`. The Markdown
files read fine in VS Code's Markdown preview.

## Known limits

**Which notes.** Only notes in a Notein folder
`Uni CS/Year N/[Semester M/]MODULE/<Lecture…|LGT…|SGT…>` are converted. Everything else
(revision, question sheets, coursework, textbook notes, notes directly in a module
folder) is skipped on purpose and listed with its reason by `notes organise`.

**Never overwrites what it did not make, and deletes only invalid new exams.** A PDF
left behind by a renamed or moved note, or one put in the Notein bin, is reported as an
orphan on every run until you delete it. The only files `notes` ever deletes are exams
written in a `notes exam MODULE` session that are not valid when it ends (see
[Checks and deletion](#checks-and-deletion)).

**Deleting a note in Notein does not remove it here.** `notes sync` only ever adds and
updates files in `data/NoteInDataSync` (that is what makes it safe), so a note you delete
in Notein stays in the local backup and keeps its PDF. To get rid of it, delete its file
from `data/NoteInDataSync` and its PDF yourself. A file
of yours where a note's PDF should go makes that note fail until you move the file. A
converted PDF that you annotate in place is replaced the next time that note is
converted; copy it elsewhere first.

**What the PDFs do not show.**

- Pen pressure: every stroke is drawn at one constant width.
- The ruled lines of lined Notein paper (only the paper colour is drawn).
- Ink beyond the right or left edge of a page is cut off at the edge.
- Ink written across the boundary between two pages is drawn on both pages, so the
  writing lines up when the pages are viewed one under the other.
- A rotated picture is drawn unrotated, with a warning (no picture in the current backup
  is rotated).
- Bookmarks (the outline/table of contents) and page labels of an imported PDF are not
  carried over. Its links are.
- If a note refers to an imported PDF that is not in the backup, its pages are drawn as
  ink on blank paper, with a warning. This does not happen in the current backup.

### Week numbers

`--week` and the week labels Claude sees are **guesses from file and folder names**: `W3`,
`Week 3`, `Week_03`, `week10`, `wk 5`, `lgt4`, `S1W2`, `T2W7` and the like.

- For slides, the folder name wins over the file name: FM2's
  `Week 5 Slides-20261114/week7_2opt.pdf` counts as week 5.
- For notes, the file name wins over the folder name.
- Numbered tutorials and labs (`SGT 3`, `4ABC1DSA_SGT_3`, `Lab 2`) have no week, nor do
  names with a range (`W1-2`, `Revision W1-5`) or two different weeks. With `--week` these
  are given to Claude as "unassigned" to sort out.
- In `S1W2` and `T2W7` the semester or term number is ignored: `S2W1` is week 1, the same
  as `S1W1`.

Slides and handwritten notes on the same topic are sometimes numbered differently: a
tutorial sheet named `…_lgt7` (week 7) whose slides are in `Week 6 Slides/`, or slides in
`Week 07/` with notes called `W6`. So `--week` can put together material that does not belong together, or split material
that does. The flashcard brief
makes Claude compare the topics first and ask you which files belong together before
writing any card; check its suggestion.

## For working on the code

| Where | What |
|---|---|
| `docs/superpowers/specs/2026-11-14-notes-cli-design.md` | the design: intent, goals, what it must never do |
| `docs/superpowers/plans/2026-11-14-notes-cli.md` | the build plan, task by task |
| `docs/inkblob-format.md` | how Notein's binary pen strokes are stored, and the evidence |
| `reference/exam-generator/` | your original exam prompt, template and `merge_exam.py` |
| `src/notes_cli/` | the code; `cli.py` is the entry point, and each module's opening comment explains its rules |
| `src/notes_cli/prompts/` | the briefs given to Claude (`flashcards.md`, `exam.md`) and the exam template |
| `tests/` | the tests; `factory.py` builds fake Notein notes for them |

Run the tests in the container with:

```
TMPDIR=/workspaces/NoteinNotes/notes-cli/.tmp /home/vscode/venv/bin/python -m pytest -q /workspaces/NoteinNotes/notes-cli --rootdir /workspaces/NoteinNotes/notes-cli
```

It takes about three minutes, because some tests read the real backup in
`data/NoteInDataSync` (they only read it, and are skipped if it is absent). Plain
`python` on the PATH has no pytest, hence the full path. `TMPDIR` keeps temporary files in
`notes-cli/.tmp/`, which git ignores. The tests never use the network and never start
real `rclone` or `claude`.
