# exam[N].md — Questions File (what students see)

# [MODULE NAME] Practice Exam [N]

**Total Questions: [TOTAL]**
**Format: [X]% Multiple Choice ([MCQ_COUNT] questions), [Y]% Written ([WRITTEN_COUNT] questions)**
**Suggested Time: [MINUTES] minutes**

**Scoring:**
- MCQ (single-answer): 1 mark each. No negative marking.
- MCQ (multi-answer): +1 per correct selection, -0.5 per incorrect selection, min 0 per question.
- Written: Marks as indicated per question.

---

## Part A: Multiple Choice

---

**Question 1**

A process running on a single-core system calls fork() and the child immediately calls exec(). What happens to the child's address space after exec() completes?

Select one:

a. [Option]
b. [Option]
c. [Option]
d. [Option]
e. [Option]

---

**Question 2**

Consider the following [code/system/configuration]. Which statements correctly describe its behaviour?

Select **three**:

a. [Option]
b. [Option]
c. [Option]
d. [Option]
e. [Option]

---

**Question 3**

What does the following function return when called with input `n = 5`?

```
[code — 5-15 lines]
```

Select one:

a. [Option]
b. [Option]
c. [Option]
d. [Option]
e. [Option]

---

[... Questions 4-N follow same patterns, easier to harder ...]

---

## Part B: Written Questions

**(Only if user specified a written component. Otherwise omit.)**

---

**Question [N+1]: Short Answer** ([X] marks)

[Question requiring 2-4 sentence constructed response]

---

**Question [N+2]: Code Writing** ([X] marks)

[Specification for a function/method/query to implement]

---

**Question [N+3]: Analysis / Design** ([X] marks)

[Scenario requiring trade-off analysis or system design]

---

**Question [N+4]: Proof / Derivation** ([X] marks)

[Property to prove or result to derive]

---

---

# exam[N]_answers.md — Answers File (separate from questions)

# [MODULE NAME] Practice Exam [N] — Answer Key

## Part A: Multiple Choice

**Q1:** b — [1-2 sentence explanation. Reference: Lecture X, slide Y.]
  - a: Confuses fork() memory with thread shared memory. fork() copies the address space.
  - c: Assumes exec() returns to the caller. exec() replaces the entire process image.
  - d: Confuses exec() with wait(). wait() blocks the parent, not the child.
  - e: Off-by-one in understanding when the address space is replaced.

---

**Q2:** b, c, e
  - a: Confuses X with Y. [one sentence]. (Ref: Lecture X)
  - b: CORRECT. [one sentence]. (Ref: Lecture X, slide Y)
  - c: CORRECT. [one sentence]. (Ref: Lecture X)
  - d: Forgets edge case Z. [one sentence].
  - e: CORRECT. [one sentence]. (Ref: Lecture X)

---

**Q3:** b — Step-by-step trace:
  - Iteration 1: i=1, acc=1
  - Iteration 2: i=2, acc=3
  - ... returns 15.
  - a: Result of off-by-one (loop uses < instead of <=).
  - c: Result of confusing 0-indexed with 1-indexed.
  - d: Result of reading * as + in line 4.
  - e: Result if base case were 0 instead of 1.

---

[... compact explanations for all remaining MCQ questions ...]

---

## Part B: Written Questions — Model Answers

**Q[N+1]: Short Answer** ([X] marks)

**Model Answer:** [Complete answer.]

**Rubric:**

| Criteria | Marks | Description |
|----------|-------|-------------|
| [Criterion] | [X] | Full: [threshold]. Partial: [threshold]. |
| **Total** | **[X]** | |

---

**Q[N+2]: Code Writing** ([X] marks)

**Model Answer:**
```
[Full working solution]
```

**Rubric:**

| Criteria | Marks | Description |
|----------|-------|-------------|
| Core logic correct | [X] | Full: works for standard inputs. Partial: minor bugs. |
| Edge cases | [X] | Full: handles [cases]. Partial: handles some. |
| Clarity | [X] | Full: readable. Partial: works but messy. |
| **Total** | **[X]** | |

---

[... model answers for remaining written questions ...]

---

## Answer Key Distribution Audit

**Single-answer questions ([count]):**
- a: [count], b: [count], c: [count], d: [count], e: [count]

**Multi-answer questions ([count]):** Q[n]([letters]), Q[n]([letters]), ...

**Multi-answer letter frequency:** a=[count], b=[count], c=[count], d=[count], e=[count]

**Checks:**
- Consecutive: max [X] same letter. [Pass/Fail].
- Multi-answer combos all distinct. [Pass/Fail].
- Multi-answer: [count]/[MCQ_COUNT] = [%]% (>=40%). [Pass/Fail].
- Code/notation: [count]/[TOTAL] = [%]% (>=20%). [Pass/Fail].
- Max 3 correct per multi-answer. [Pass/Fail].
- Difficulty: [R] recall, [A] application, [An] analysis, [S] synthesis. [Pass/Fail].
- MCQ/written split: [X]%/[Y]%. [Pass/Fail].
- Progressive difficulty: easier→harder. [Pass/Fail].
- All distractors have named misconceptions. [Pass/Fail].
- Lecture references present. [Pass/Fail].
