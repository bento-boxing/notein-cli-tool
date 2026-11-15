# Anki flashcards for {module}

This file is your brief for this session. Read all of it before you start, then
follow it. The user, a computer-science student, is here with you and can answer
questions; everything you need is below, so after telling the user your plan in a
few lines, start working unless something is genuinely unclear.

## What this is for

The user is revising for the university module **{module}**. Your working
directory is that module's folder. It holds the lecturer's slides
(`Lecture-Slides/`) and the user's own handwritten notes from lectures and
tutorials ({note_folders}). The handwritten notes were taken in a tablet app and
converted to PDF: some are pages of handwriting, some are the lecturer's slides
with handwriting on top.

Your job is to turn this material into Anki flashcards, one file per teaching
week, that cover everything examinable, import into Anki in one step, and can be
updated in later sessions without creating duplicate cards or losing the user's
review history.

**Scope of this run:** {week_scope}

The numbered sections below are in the order you need them. Some rules look
fussy; most exist because Anki's importer would otherwise silently mangle, drop
or duplicate cards, and the reason is given where it helps.

## 1. Sources

Use all of these sources, and only these. Paths are relative to the working
directory.

**The week labels below are guesses taken from file and folder names**, not
facts. Teaching weeks and the user's own numbering can be off by one or two
(reading weeks, renumbered slide decks, notes filed under the week they were
written up), so the slides and the notes labelled with the same week do not
always cover the same lectures. Section 2 says how this is checked.

**Lecture slides**: typed PDFs; read them as text.

{slides}

**Handwritten notes**: read them as page images (section 8).

{notes}

**Sources with no week number in their name or folder.** The tool could not tell
which week these belong to; section 2 says how they are sorted out. Content that
belongs to no week (welcome, module admin, assessment logistics) is not
flashcard material.

{unassigned}
{nearby}
How to read each kind:

- **Slides** have a text layer. `pdftotext -layout '<file>' -` is the quickest
  way to read them. Where the meaning is in a diagram, table or figure, or a
  formula comes out garbled, look at that page as an image instead (the Read
  tool with `pages`).
- **Handwritten notes** have no text layer for the handwriting: it is ink, not
  text, so `pdftotext` returns nothing for it (or only the printed slide text
  underneath). They must be read by looking at each page as an image; see
  section 8.
- In shell commands, put every file name in single quotes, as above, and write a
  single quote inside a name as `'\''`. Never use double quotes or leave names
  unquoted: names contain spaces and may contain `$`, backticks or quotes.
- The slides are the authority on what the module covers and on the exact
  wording of definitions. The handwritten notes add what the lecturer said,
  worked examples, tutorial and lab exercises with their solutions, and what the
  user thought worth writing down: treat these as strong signals of what
  matters. If notes and slides disagree on a fact, follow the slides and mention
  the disagreement in your final report.

## 2. Work one week at a time

The work is done by subagents (the Agent tool), with **one subagent per week**
writing that week's cards. Reading PDFs, especially as page images, uses a lot of
context; keeping it in subagents leaves this main session small enough to
coordinate every week, check and report. Do not read the source PDFs in the main
session yourself.

Each subagent reads `{prompt_file}` but follows only the sections for its role.
Tell it its role and these sections in its brief, because it does not see this
conversation:

| Role | Follows | Skips | Writes |
|---|---|---|---|
| main session (you) | everything; 2, 5 and 12 are yours alone | — | nothing in the module folder |
| triage subagent | "How to read each kind" in 1; 8; 11 | 2–7, 9, 10, 12 | only its page images |
| week subagent (writes, and later fixes, one week's cards) | "How to read each kind" in 1; 3; 4; 6; 7; 8; 9; 10; 11 | 2, 5, 12 | that week's files in `{out_dir}/` |
| reviewer subagent | "How to read each kind" in 1; 4; 6; 7 (to judge, not to apply); 8; 11 | 2, 3, 5, 9, 10, 12 | only its page images |

Steps:

1. **Triage first, before any cards are written.** Start a triage subagent (if
   there are more than about 15 files, split them over several, numbered 1, 2,
   …). Give it every file listed in section 1, including the unassigned ones and
   any neighbouring-week files. It looks at only the first page or two of each
   file and reports, per file, one line: the main topic, and the week it seems to
   belong to, or "no week: admin". Then compare, week by week, the topics of the
   slides with those of the handwritten notes labelled with that week, and decide
   where each unassigned file goes.
2. **If the labels do not line up, ask; do not guess.** When a week's slides and
   notes cover different topics, a file looks as if it belongs to another week,
   or an unassigned file is not clearly one week's, show the user what triage
   found (file names and topics, side by side) and then
   ask which sources belong together. Use the user's answer as the source list
   for each week. If everything lines up, say so in one line and carry on.
3. **One week subagent per week.** Its brief: the module ({module}), the week
   number, the role "week subagent" with its sections from the table above, and
   the exact list of source files for that week as settled in steps 1 and 2. Ask
   it to reply with a short summary only: cards added and kept, topics covered,
   material left out as non-examinable, anything it was unsure of, and any
   source that turned out to be about another week's topic (it makes no cards
   from such a source and leaves the decision to you).
4. When a week subagent has finished, run the review in section 5 for that week.

You may run up to three week subagents at once; each writes only its own week's
files and page images, so they do not get in each other's way.

## 3. Topic checklist first, then cards

Before writing any card, write a topic checklist for the week from every one of
its sources: `{out_dir}/{module} Week NN topics.md` (NN is the two-digit week).
One line per examinable item (each definition, theorem or lemma, algorithm and
its running time, proof idea, protocol, formula, distinction, type of worked
example, common pitfall) with the source file and page(s) it comes from. Go
through every page of every source; do not skim. Then write the cards against
the checklist, ticking each item (`- [x]`) once it has at least one card.

This makes coverage checkable, gives the reviewer something concrete to compare
against, and shows the user at a glance what the week contains. If the topics
file already exists from an earlier session, update it instead of starting over.

## 4. What goes on cards

- **Leave out content marked non-examinable** by the lecturer, on the slides or
  in the user's notes ("non-examinable", "not examinable", "not in the exam",
  "for interest only", "beyond the scope of this module" and the like). If you
  are not sure whether something is examinable, make the card, add the tag
  `check-examinable`, and mention it in your summary.
- **Include every point from "Extra Notes" and "Review" sections** (and similar
  recap or summary slides): each point gets at least one card, even if it looks
  minor. Lecturers use these sections to signal what they expect students to
  know.
- Leave out admin: deadlines, rooms, office hours, coursework logistics.
- Writing good cards:
  - One fact, idea or step per card. Split lists into several cards rather than
    asking for "the five properties of X" at once.
  - The front is a question with one clear answer that still makes sense months
    later, shuffled among other modules: name the topic ("In TCP, ..."), never
    "this algorithm" or "the above".
  - The back is short: the answer first, then at most a line or two of
    explanation or a small example.
  - Ask "why" and "when", not only "what": for algorithms and proofs, cards for
    the idea, the key steps, the running time and why, and the conditions and
    edge cases; for tutorial exercises, a card per technique, on a small
    concrete instance.
  - Use the lecturer's notation and terminology.
  - Do not add material that is not in the sources, beyond what an answer needs
    to be correct and self-contained.

## 5. Review by a fresh subagent

When a week's cards are written, start a **fresh subagent** (not the one that
wrote them, so it judges the cards without the writer's assumptions) in the
reviewer role (section 2). Give it the module, the week, the week's sources, the
card file and the topics file, and tell it to read every source page itself and
to report, without changing any file:

- **missing topics**: things in the sources with no card, or covered only in
  part;
- **wrong cards**: incorrect, ambiguous, or answering a different question than
  they ask;
- **unnecessary cards**: duplicates, trivia, admin, non-examinable content,
  things not in the sources.

Then have the gaps filled and the wrong cards corrected by a week subagent for
that week (the one that wrote the cards if you can continue it, otherwise a new
one with the same brief plus the reviewer's findings).

**Do not delete cards** the reviewer calls unnecessary, or for any other reason.
Collect them as **proposed removals** (file, id, front, reason) for the user to
decide on. Taking a line out of the file would not remove the card from Anki
anyway; it would only make the file and the user's collection disagree.

## 6. Output format

Each week's cards go into one file, `{out_dir}/{module} Week NN.txt`, which
starts with the six header lines of section 9 (they tell Anki the separator, the
note type, the deck, and that column 1 is the card's GUID and column 4 its tags).
After the header comes one card per line, with no blank line before the first:

- The file is UTF-8 text without a byte-order mark.
- Each card has exactly four fields separated by single tab characters:
  `id<TAB>front<TAB>back<TAB>tags`.
  - **id**: the card's stable identifier (section 7). Anki stores it as the
    note's GUID, so when the file is imported again a row whose id matches an
    existing note updates that note instead of adding a duplicate, and the
    user's review history is kept.
  - **front** and **back**: HTML.
  - **tags**: space-separated, no spaces inside a tag: `{module_id}`, `weekNN`,
    and one or two topic tags in lower case with hyphens, e.g.
    `{module_id} week03 master-theorem`; plus `check-examinable` where section 4
    says so.
- Fields are HTML, and a tab or a line break ends a field or a row, so:
  - never put a tab, a real line break or any other control character inside a
    field; write `<br>` for a line break;
  - write `<` as `&lt;`, `>` as `&gt;` and `&` as `&amp;` wherever they are text
    rather than HTML tags, including inside maths and code
    (`\(a &lt; b\)`, `List&lt;String&gt;`, `a &amp;&amp; b`);
  - write HTML tags in lower case; `<B>` or `<P>` in upper case is read as text
    and must be `&lt;B&gt;`;
  - never start a field with `"`: Anki would take it as the start of a quoted
    field and swallow the tabs and lines after it. Write `&quot;` instead;
  - keep formatting simple: `<b>`, `<i>`, `<u>`, `<sub>`, `<sup>`, `<code>`,
    `<br>`, `<ul><li>…</li></ul>`, `<ol>`, `<table>`. Code over several lines:
    `<code>` with `<br>` between lines and `&nbsp;` for indentation.
- Maths: inline `\( … \)`, display `\[ … \]`; Anki renders both with MathJax.
  Never `$…$` or `$$…$$`, which Anki shows as plain dollar signs. A literal
  dollar sign is `&#36;`. A LaTeX row break `\\` inside maths is fine. Text
  inside `<code>` or `<pre>` is not checked for maths, so code may contain `$`
  and brackets freely; avoid `\(` and `\[` there all the same.
- Every card has a non-empty id, front and back. No two cards have the same
  front.

## 7. Card identity: merge, never regenerate

The id is what makes re-importing safe, and it only works if a card keeps its
id for ever and no id is ever given to a second card.

- **Id format:** `{module_id}-WNN-NNNN`: the module, the two-digit week of the
  file the card was first written in, and a four-digit serial number within that
  week, e.g. `{module_id}-W03-0001`, `{module_id}-W03-0002`. Module and week make
  ids unique across the user's whole Anki collection; the serial does not depend
  on the card's wording, so rewording a card never changes its id.
- **The last issued id is recorded** in `{out_dir}/{module} Week NN last-id.txt`:
  one line, the highest id ever issued for that week (e.g. `{module_id}-W03-0042`).
  New ids continue from the larger of that record and the highest serial in the
  card file, and after adding cards you write the new highest id into the record.
  Why: the user may delete rows from the card file while the cards live on in
  Anki; if a new card reused such an id, importing it would overwrite an
  unrelated card and its review history. The record never goes down. If it does
  not exist yet, create it.
- **If the week's file already exists, read it first** and merge into it:
  - a card that covers the same fact as an existing card
    **keeps its existing id**, even if you reword, correct or improve it: edit
    that row in place;
  - only genuinely new cards get new ids, as above; never fill gaps in the
    numbering;
  - keep the existing rows in their order and add new rows at the end;
  - never delete an existing card: list it as a proposed removal (section 5);
  - keep ids that do not follow the format above exactly as they are.
- **Never regenerate a file from scratch over an existing one**, and never write
  a week's file without first reading what is already there.
- **Back up once per session, before the first change.** Before you first change
  a week file that already existed when this session started, run:

  ```
  {backup_cmd}
  ```

  (with NN filled in). It copies only if this session's backup does not exist
  yet, so the backup keeps the file as it was at the start of this session
  however many times the file changes later, and running the command again is
  harmless. Never edit or delete backups. The check in section 10 compares the
  file with its newest backup and reports any card that has gone missing.
- Never reuse an id for a different fact and never give two cards the same id,
  not even in different week files. If an existing card clearly belongs to
  another week, leave it where it is and mention it in your report.

## 8. Reading handwritten notes as page images

Everything in {note_folders} is handwriting converted to PDF.
It is not searchable: you read it by looking at the pages as page images.

- Open the PDF with the Read tool a few pages at a time (`pages: "1-5"`, then
  `"6-10"`, and so on) and look at every page. Do not skip pages: the last pages
  of a lecture often hold its summary. (Triage looks at the first page or two
  only.)
- Go page by page and note what each page says before moving on. Transcribe
  formulas carefully: subscripts, primes, inequality directions, quantifiers.
- Some notes are the lecturer's slides with the user's handwriting on top: read
  both the printed text and the handwriting. Underlines, circles, arrows, stars
  and margin notes show what the user or the lecturer emphasised.
- If a word or symbol is illegible, do not build a card on a guess: resolve it
  from the slides, or leave it out and mention it in your summary.
- If the Read tool cannot show you a page, render it to PNG and read that, in a
  folder of your own so that subagents working at the same time do not touch
  each other's images: `{out_dir}/.pages/WNN/` for the week subagent of week NN,
  `{out_dir}/.pages/WNN-review/` for its reviewer, `{out_dir}/.pages/triage-K/`
  for triage subagent K. For example:
  `pdftoppm -r 110 -png -f P -l P '<file>' '{out_dir}/.pages/W03/page'`.
  When you have finished, delete your own folder and nothing else.

## 9. File name and header

One file per week: `{out_dir}/{module} Week NN.txt`, where NN is the week with
two digits (`{module} Week 03.txt`, not `{module} Week 3.txt`). It begins with
exactly these six lines, in this order, with NN replaced by the same week:

```
#separator:tab
#html:true
#notetype:Basic
#deck:CS Degree::{module}::Week NN
#guid column:1
#tags column:4
```

No other line in the file may start with `#`.

## 10. Check every file

After writing or changing a week's file, run (with NN filled in):

```
{check_cmd}
```

It prints one problem per line, each saying where it is and how to fix it, and
exits with status 1 if there are any. Fix every problem it reports, then run it
again, until it reports none. No fix ever requires deleting a card: if a card
looks impossible to fix, keep it, make it pass (for example by rewording), and
mention it in your summary. A week is not finished until its file passes. The
check covers the format, the backup and the id record only; it cannot tell
whether a card is right.

## 11. Stay inside this folder

- Stay inside the working directory (this module's folder). Read only the
  sources listed in section 1 and the files in `{out_dir}/`. Do not look at
  other modules' folders or anywhere else on the computer, and do not search the
  web: the cards come from these sources.
- Write only inside `{out_dir}/`, and only what your role writes (section 2): the
  week card files, their topics and last-id files, this session's backups, and
  your own temporary page images.
- Never change, move, rename or delete anything in `Lecture-Slides/`,
  {note_folders} or `Practice Exams/`, never change or delete a backup, and do not
  edit this file.

## 12. Final report

When every week in scope is done and its file passes the check, finish with a
report to the user:

1. For each week: the file, the number of cards added and kept (and how many of
   the kept ones you reworded), and the topics covered.
2. Proposed removals: file, id, front and reason. Nothing has been deleted; the
   user decides.
3. Anything uncertain: illegible handwriting, disagreements between notes and
   slides, week labels that did not match the content and what was decided,
   `check-examinable` cards, and which week each unassigned source went to.
4. How to import: in Anki, File → Import → select the file (one file per week).
   The header sets the separator, note type, deck and columns, so the import
   screen should already show note type Basic and deck
   `CS Degree::{module}::Week NN`; if it asks what to do with existing notes,
   choose Update. Importing an updated file again later updates the existing
   cards in place, matched by id, instead of duplicating them.
