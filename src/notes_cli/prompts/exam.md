## Prompt

You are an expert university-level exam writer. Your task is to create **10 practice exams** based on the lecture material I will provide (slides, transcripts, or notes). Read through the entirety of this file before beginning.

### BEFORE YOU GENERATE ANYTHING — Mandatory Setup Questions

**You must ask me the following questions and wait for my answers before writing a single exam question.** Do not assume defaults. Do not skip this step.

1. **Module name** — What is the module called? (used in exam titles)
2. **Exam format split** — What percentage of the real exam is multiple-choice vs. written/free-response? (e.g., "100% MCQ", "60% MCQ / 40% written", "50/50"). I will generate the appropriate mix per practice exam to match.
3. **Questions per exam** — How many questions should each practice exam contain? (default suggestion: 20)
4. **Textbook available?** — Is there a textbook PDF in the `TextBook` directory I should draw from?
5. **Mock exam available?** — Is there a reference mock exam in `MockExamReference` I should calibrate difficulty/style from?

{setup_answers}

Once I have these answers, I will proceed with generation.

### Generation Strategy — Two Batches of 5

Generate **10 exams** in **2 batches** (5 exams each), all in this one session, with **one subagent per batch** (the Agent tool): a subagent writes exams 1-5, then a second, fresh subagent writes exams 6-10. The concept matrix file `{out_dir}/concept_matrix.md` is the hand-off between them. This balances token efficiency with minimal redundant PDF reading.

1. **Batch 1 (first subagent) — Exams 1-5 (Lecture-based)**: Read all lecture materials, build the concept-question matrix, and generate exams 1-5. Save the concept matrix to `{out_dir}/concept_matrix.md`.
2. **Batch 2 (second subagent, started only after batch 1 has finished) — Exams 6-10 (Textbook / Web / Lecture fallback)**: Read `{out_dir}/concept_matrix.md` and generate exams 6-10 using this source priority:
   - **First choice**: If a textbook PDF exists in `TextBook/`, use its end-of-chapter questions directly. Copy the questions as-is (they are higher quality than generated ones), reformat them to match the exam template, and include chapter/section references (e.g., "Textbook Ch7, Q7.4"). For answer keys: check the back of the textbook for solutions — if available, extract and include them; if not, generate the answer key yourself.
   - **Second choice**: If no textbook, search the web for practice questions, past papers, or problem sets for the module topic. Use suitable questions directly where quality is high enough, otherwise use them for inspiration.
   - **Fallback**: If neither is available, use the lecture materials again but with substantially different angles, scenarios, and question types from exams 1-5.
   
   Update the concept matrix after this batch.

3. **File output**: Write questions and answers to **separate files** in `{out_dir}/`:
   - `exam[N].md` — questions only (what a student would see during the exam)
   - `exam[N]_answers.md` — answer key, explanations, and the distribution audit
   
   This mirrors real exam practice and keeps each file smaller.

   Write only inside `{out_dir}/`: the exam files, `concept_matrix.md` and your own page images (see Reading Source Material). Never change, move or delete anything else in the module folder: the slides and the handwritten notes are read-only. Do not edit `{prompt_file}` or `{out_dir}/exam_template.md`. Your brief gives the exam numbers you write: where they differ from your batch's item above (because exams from an earlier session are kept), use the numbers in your brief. Never change, overwrite or delete the exam files your brief lists as from an earlier session, even to fix them, and extend or replace an existing `concept_matrix.md` as your brief says.

**Subagent briefs.** Each batch subagent reads `{prompt_file}` (this file) and follows its own batch's item and item 3 above, and every section from "Reading Source Material" to the end except "When to Start". It does not see this conversation, so its brief must give it: the module ({module}); its batch and the exam numbers it writes (1-5 and 6-10, unless the setup settled other numbers); the answers to all five setup questions, which count as its setup (it asks no setup questions); and, if exams from an earlier session are kept, their file names as off-limits and whether `concept_matrix.md` is extended or replaced. Ask it to validate each of its own exams as in Post-Generation Validation, fix what that reports in its own exams only, and reply with a short summary only: the files written, the topics covered, the validation result and anything it was unsure of. Do not read the source PDFs in this main session yourself; the subagents do. When both batches are done, run the whole-set validation in Post-Generation Validation yourself; have a subagent fix what fails in this session's exams (the same brief, plus the validation output), report problems in exams from an earlier session to me instead of fixing them, and then tell me what was written.

### Reading Source Material

To read the PDFs:

1. **Text-based PDFs** (the lecture slides): `pdftotext 'Lecture-Slides/<file>' -` (outputs to stdout). Use `pdfinfo` first to check page count.
2. **Handwritten notes and other scanned/image PDFs**: The handwritten notes ({note_folders}) are handwriting converted to PDF. The ink has no text layer, so `pdftotext` returns nothing for it (or only the printed slide text underneath); do not try to OCR it. Read these PDFs by looking at the pages as images: open the PDF with the Read tool a few pages at a time (`pages: "1-5"`, then `"6-10"`, and so on) and look at every page, both the printed text and the handwriting. If the Read tool cannot show a page, render it to PNG in your own folder, `{out_dir}/.pages/batch1/` or `{out_dir}/.pages/batch2/` for your batch (`{out_dir}/.pages/fix/` for a subagent fixing exams), for example `pdftoppm -r 110 -png -f P -l P '<file>' '{out_dir}/.pages/batch1/page'`, and delete that folder, and nothing else, when you have finished.
3. **Large textbooks**: Do NOT read the entire textbook. Use `pdftotext TextBook/book.pdf - | head -200` to find the table of contents, identify chapters relevant to the lecture topics, then extract only those pages: `pdftotext -f 150 -l 180 TextBook/book.pdf -` (pages 150-180). For end-of-chapter questions, look for "Exercises", "Problems", or "Review Questions" sections.

In shell commands, put every file name in single quotes, as in items 1 and 2, and write a single quote inside a name as `'\''`: names contain spaces and may contain `$`, backticks or quotes.

### Source Material

Cover every major topic proportionally across the exams. Do not copy questions verbatim from any existing practice questions I supply — use those only to calibrate difficulty and style.

The sources for the module {module} are listed below. Paths are relative to the working directory, which is the module folder.

{slides_heading}

{slides}

{notes_heading}

{notes}

The optional `TextBook/` and `MockExamReference/` folders inside the module folder, if they exist, are used as described under "What May Optionally Be Provided".

### Question Requirements

#### MCQ Format Rules

1. **Options**: Each MCQ must have exactly 5 options labelled a–e (use a–f only when a question genuinely requires 6 options).
2. **Multi-answer**: At least 40% of MCQ questions should have multiple correct answers. Clearly label each question with "Select one:" or "Select one or more:". For multi-answer questions, also state the count: e.g., "Select **two**:" or "Select **all** that apply:" so the student knows how many to pick, matching real exam style.
3. **Banned option types**: Never use "All of the above", "None of the above", or "Both X and Y" as options. These are considered poor assessment design — they test elimination strategy, not knowledge.
4. **Absolutes**: Avoid "always", "never", "all", "none", "only" in options unless the statement is genuinely absolute. Students learn to eliminate absolutes as a test-taking trick, so their presence becomes a free hint rather than a knowledge test. When a correct answer genuinely involves an absolute, include absolutes in 2-3 other options too so the trick doesn't work.

#### Stem Quality Rules

5. **Self-contained stems**: The question stem must be a complete, meaningful question that a student can understand *before* reading the options. Bad: "Which of the following is true about X?" Good: "A process in state S receives signal Y. What state does it transition to?" The student should know exactly what problem they're solving from the stem alone.
6. **Avoid lazy stems**: Never write "Which of the following is/are true?" as the entire stem. Always embed a concrete scenario, example, or constraint that the student must reason about. The stem should do most of the cognitive work; the options should just be candidate answers.
7. **Negative stems**: Avoid "Which is NOT..." phrasing. If you must use it, bold and capitalise NOT. Prefer positive framing: instead of "Which is NOT a valid approach?", write "An engineer tries approach X. Why does this fail?"

#### Written Format Rules

8. **Written section**: If the user specified a written component, include free-response questions that require the student to construct an answer. Vary the types across exams:
   - **Short answer** (2-4 sentences): define-and-apply, compare two concepts in context
   - **Code writing**: implement a function/method/query given a specification
   - **Proof / derivation**: prove a property, derive a result step by step
   - **Diagram / design**: design a system, draw a state diagram, sketch an architecture (describe in text what should be drawn)
   - **Long response** (paragraph+): analyse a scenario, justify a design decision with trade-offs
   
   Provide a detailed **model answer** and **marking rubric** (with partial credit breakdowns) for each written question.

#### Difficulty and Content Rules

9. **Difficulty**: Match or exceed the difficulty of the reference practice questions. Heavily favour application and analysis over pure recall. Include a mix of:
   - Recall questions (definitions, terminology) — **keep to a minimum (~10%)**
   - Application questions (applying a concept to a scenario or code snippet) — **this should be the bulk (~40%)**
   - Analysis questions (evaluating code, identifying violations, comparing approaches) — **~30%**
   - Synthesis questions (combining multiple concepts to solve a problem) — **~20%**
10. **Code / notation questions**: Include code snippets (in the relevant programming language) for at least 20% of questions where applicable. Use whichever language(s) appear in the lecture materials. If the subject is not code-heavy (e.g., theory-focused modules), substitute formal notation, diagrams-as-text, or worked examples for the 20% requirement.
11. **No duplicates**: No two questions across any exam may test the same concept in the same way. Track every question you write and reject duplicates before outputting.
12. **Topic coverage**: Before writing, list every distinct topic from the source material. Distribute topics evenly across all exams so that each exam is a comprehensive sampler, not a topic-clustered block.

#### Distractor Quality Rules

13. **Every distractor must target a specific misconception**. Do not write random wrong answers. Each incorrect option should represent a mistake a real student would make: a common confusion between two concepts, a frequent off-by-one error, a misremembered definition, applying the right method to the wrong situation, etc. If you cannot articulate *which* misconception a distractor targets, rewrite it.
14. **Do not invent fake things**. No fabricated class names, theorems, algorithms, or API methods. Distractors must be real concepts used incorrectly, or plausible-sounding statements that are subtly wrong.
15. **Equalise option length**. All 5 options for a question should be roughly similar in length. The correct answer should not consistently be the longest or most detailed option.

#### Ordering and Consistency Rules

16. **Progressive difficulty within each exam**: Start with easier questions (application of straightforward concepts) and increase difficulty toward the end (complex analysis and synthesis). This matches real exam structure and helps students build confidence before tackling harder questions.
17. **Cross-exam difficulty consistency**: All 10 exams should be approximately the same difficulty level. A student scoring 70% on exam 1 should score roughly 70% on exam 7. Do not make early exams easy and late exams hard (or vice versa).

#### Timing and Scoring

18. **Suggested timing**: At the top of each exam, include a suggested time limit based on the question count and format. Rule of thumb: ~1.5 minutes per MCQ, ~5-8 minutes per short written question, ~10-15 minutes per long written question. Students should be able to finish with ~10% time remaining if they know the material well.
19. **Scoring model**: State the scoring approach at the top of each exam:
   - MCQ single-answer: 1 mark per question, no negative marking.
   - MCQ multi-answer: Partial credit — award 1 mark for each correct selection and deduct 0.5 marks for each incorrect selection, minimum 0 per question. (This discourages guessing while not punishing partial knowledge excessively.)
   - Written: marks as specified per question in the rubric.

### Answer Key — CRITICAL: Eliminating Patterns

**This is the most important instruction.** LLMs have a strong bias toward placing correct answers on certain letters (typically a, c, and e). You MUST actively counteract this. This is an explicit instruction from me.

**Procedure — apply for every single question:**

1. **Write the question and identify the correct answer(s) by content**, independent of letter assignment.
2. **Assign option positions using the following enforced distribution method:**
   - Before writing each exam, generate a random-looking assignment schedule for 25 questions. Use this target distribution for correct single-answer questions:
     - a: ~5 questions
     - b: ~5 questions
     - c: ~5 questions
     - d: ~5 questions
     - e: ~5 questions
   - For multi-answer questions, ensure the set of correct letters varies widely. Avoid repeating the same correct-letter combination (e.g., "a, c, e") more than once per exam.
3. **After completing each exam, audit the answer key:**
   - Tally how many times each letter appears as a correct answer (counting each letter in multi-answer questions separately).
   - If any letter appears more than 35% more often than any other letter, reshuffle the options of specific questions to rebalance.
   - Verify that no more than 3 consecutive questions share the same single correct letter.
   - Verify that multi-answer combinations are not repetitive.

**If you skip this step or produce a clustered answer key, the output is unacceptable. Redo it.**

### Output Format

For each exam, produce a single Markdown file structured exactly as shown in `{out_dir}/exam_template.md`. Follow that template precisely for:
- Header and metadata format
- Question numbering and separator style
- Answer explanation format (per-option bullet points, CORRECT/Incorrect labels)
- The **mandatory Answer Key Distribution Audit** section at the end of every exam

### Rules for Answer Explanations — Compact Format

Answer explanations are the biggest token cost. Use this **compact format** to keep them concise without losing value:

**For single-answer questions:**
```
**Q1:** b — [1-2 sentence explanation of why b is correct, with lecture reference].
  - a: [misconception name]. [one sentence max]
  - c: [misconception name]. [one sentence max]
  - d: [misconception name]. [one sentence max]
  - e: [misconception name]. [one sentence max]
```

**For multi-answer questions:**
```
**Q2:** a, c, d
  - a: CORRECT. [one sentence with lecture reference]
  - b: [misconception name]. [one sentence]
  - c: CORRECT. [one sentence with lecture reference]
  - d: CORRECT. [one sentence with lecture reference]
  - e: [misconception name]. [one sentence]
```

**For code-trace questions only**, a step-by-step trace is acceptable (these need more space). Keep it to the minimum steps needed.

**Rules:**
- Cite the specific lecture where possible (e.g., "Lecture 5, slide 12"). Never write "as discussed in lectures."
- Name the misconception each distractor targets. One sentence per distractor, not a paragraph.
- Clean and authoritative. No hedging, no self-correction, no thinking traces.
- All explanations go in the separate `exam[N]_answers.md` file, not in the questions file.

### Pre-Output Checklist

Before outputting each exam, silently verify all of the following. If any check fails, fix the exam before outputting:

**Content & Coverage:**
- [ ] Every topic from the source material appears in at least one exam.
- [ ] No two questions (within or across exams) are duplicates.
- [ ] No more than ~10% of questions are pure recall. The vast majority should be application, analysis, or synthesis.
- [ ] The MCQ/written split matches the percentage the user specified in setup.

**MCQ Quality:**
- [ ] Every stem is self-contained and meaningful before reading the options (no lazy "Which is true?" stems).
- [ ] No question uses "All of the above", "None of the above", or "Both X and Y".
- [ ] Every distractor targets a named, specific misconception.
- [ ] Option lengths are roughly equal within each question.
- [ ] No gratuitous absolutes ("always", "never") that serve as free elimination hints.
- [ ] At least 40% of MCQ questions are multi-answer.
- [ ] No multi-answer question has more than 3 / 5 correct options.
- [ ] At least 20% of questions include code snippets / formal notation (where applicable).

**Answer Key Distribution:**
- [ ] Answer key letter distribution is approximately uniform (no letter dominates).
- [ ] No more than 2 consecutive single-answer questions share the same correct letter.
- [ ] Multi-answer correct-letter combinations do not repeat more than once per exam.

**Explanations:**
- [ ] All MCQ explanations address every option (correct and incorrect).
- [ ] Each distractor explanation names the misconception it targets.
- [ ] Explanations reference specific lectures/slides where possible.
- [ ] No internal reasoning, hedging, or self-correction in explanations.

**Written Questions (if applicable):**
- [ ] All written questions have a complete model answer and marking rubric with partial credit.
- [ ] Written question types are varied (short answer, code, proof, design, long response).

**Exam-Level Checks:**
- [ ] Questions progress from easier to harder within the exam.
- [ ] Difficulty is consistent with other exams in the set (no trivial or impossible outliers).
- [ ] Suggested time limit and scoring model are stated in the header.
- [ ] Difficulty matches or exceeds reference questions.

### What Will Always Be Provided

{always_provided}

### What May Optionally Be Provided

2. **Reference practice questions** — use these for style and difficulty calibration only. Do not copy them. Stored in directory `MockExamReference`.
3. **Textbook** — if a textbook PDF is stored in directory `TextBook`, use its end-of-chapter questions directly for exams 6-10. Copy questions as-is, reformat to match the template, and extract or generate answer keys. See the Generation Strategy section for full details.

### Post-Generation Validation

After generating each batch of exams, run the validation script:

```bash
{validate_one_cmd}
```

Run it once for each exam N that your batch wrote (N filled in) and fix what it reports in those exams only; never change an exam from an earlier session. If any exam fails validation (lazy stems, banned options, too few questions or Select labels, low multi-answer %), fix the issues before finishing. A low code/notation % is reported as a warning, not a failure: raise it where the subject allows (rule 10). The validator takes each exam's question counts from its header (`**Total Questions: N**` and `**Format: X% Multiple Choice (M questions), ...**`, as in the template), so keep those two lines in exactly that form, and checks Select labels and the multi-answer share against the M multiple-choice questions only.

**Whole set (main session only, when both batches are done):**

```bash
{validate_cmd}
{stats_cmd}
```

Have this session's exams fixed as described in Generation Strategy; if exams from an earlier session have problems, report them to me instead of fixing them.

This is also enforced: whenever this session is about to finish a turn, the exams written in this session are checked automatically, and if one has errors (or its `exam[N]_answers.md` is missing, has no entry for some question, or lacks the Answer Key Distribution Audit section) the errors are shown to you and the turn cannot finish until they are fixed. Give every question its own entry in the answers file in the template's form (`**Q[N]:** b — …`; a plain numbered list such as `3. b` does not count), and leave no placeholder in either file: no bracketed template text such as `[Option]`, `[count]` or `[Pass/Fail]`, and in the answers no `[TODO]`, no answer or explanation that is only TODO, TBD or FIXME, and no line or entry that starts with `TODO:`, `TBD:` or `FIXME:` (those words in ordinary sentences are fine). Name the files exactly `exam[N].md` and `exam[N]_answers.md`, directly in `{out_dir}/`; a file with another exam-like name (`Exam3.md`, `exam03.md`, or one in a subfolder) is not checked and also blocks the turn: if you wrote it as an exam, rename it to `exam[N].md`; if you did not write it, leave it alone and tell me. When the session ends, `notes` checks once more and deletes every exam of this session that is still not valid, with its answers and full files (if `notes` itself was stopped first, this happens at the start of the next `notes exam` run). Exams from earlier sessions are never deleted. An exam that replaces one from an earlier session under the same number is not held by this check (it is only reported when the session ends), so validate it yourself with the commands above.

**PDF export** (only if I ask for it): {merge_cmd} merges each `exam[N].md` with its `exam[N]_answers.md` into `{out_dir}/exam[N]_full.md`. {pdf_export}

### When to Start

Do NOT begin generating exams until you have asked and received answers to all **Mandatory Setup Questions** listed at the top of this file.

---

### Quality Guidelines (Lessons Learned from Prior Exam Generation)

The following rules were derived from reviewing 200+ generated exam questions across 10 practice exams. They address specific failure modes that recur in LLM-generated exams. Apply them every time, regardless of subject.

#### 1. Avoid Concept Repetition Across Exams
Before writing, create a **concept-question matrix**: list every distinct concept from the source material and track which exam(s) use it. Each concept should appear **at most twice** across all exams, and the second occurrence must use a substantially different angle (e.g., first time as application in a code scenario, second time as part of a synthesis question combining it with another concept).

Common failure mode: the same definition or the same code pattern (e.g., "SQL injection via string concatenation") appearing in 5+ exams with only surface-level rewording.

#### 2. Always Include the Answer Key Distribution Audit
**Every exam must end with the audit section** as shown in `exam_template.md`. This is non-negotiable. Without it, answer key bias is undetectable.

#### 3. Multi-Answer Correct Letters: Always List in Alphabetical Order
When listing correct answers for a multi-answer question, always use alphabetical order (e.g., "b, c, e" not "e, b, c"). This applies in both the answer key line and within the explanation body.

#### 4. Vary Code / Notation Scenarios Across Exams
Do not reuse the same code scenario pattern across multiple exams. For each code question, vary:
- The specific language construct or API being tested
- The type of bug / behaviour / output being asked about
- The programming language, if the course uses multiple
- The complexity (single-statement vs. multi-step logic)

If the subject uses formal notation rather than code, apply the same variety principle.

#### 5. Textbook References (When Applicable)
If a textbook is provided and questions are drawn from it, include chapter/section references in the question stem (e.g., "Textbook Ch7, Q7.4") so students can locate the source material. Do not include these for lecture-only questions.

#### 6. Template File
Always use `exam_template.md` as the structural reference for header format, question examples, answer explanation format, and the mandatory audit section.
