#!/usr/bin/env python3
"""
Merge exam questions and answers into a single file, or split them apart.
Also validates exam structure and reports statistics.

Usage:
    python3 merge_exam.py merge 1        # Merge exam1.md + exam1_answers.md -> exam1_full.md
    python3 merge_exam.py merge all      # Merge all exams
    python3 merge_exam.py stats          # Show stats across all exams
    python3 merge_exam.py validate 1     # Validate exam 1 structure
"""

import sys
import os
import re
from pathlib import Path

WORKSPACE = Path("/workspace")


def find_exams():
    """Find all exam question files."""
    exams = []
    for f in sorted(WORKSPACE.glob("exam*.md")):
        name = f.stem
        # Match exam1, exam2, etc. but not exam1_answers or exam_template
        if re.match(r"^exam\d+$", name):
            exams.append(int(name.replace("exam", "")))
    return sorted(exams)


def merge_exam(n):
    """Merge questions and answers into one file."""
    q_file = WORKSPACE / f"exam{n}.md"
    a_file = WORKSPACE / f"exam{n}_answers.md"
    out_file = WORKSPACE / f"exam{n}_full.md"

    if not q_file.exists():
        print(f"  exam{n}.md not found, skipping")
        return

    content = q_file.read_text()
    if a_file.exists():
        content += "\n\n---\n\n" + a_file.read_text()
    else:
        print(f"  exam{n}_answers.md not found, using questions only")

    out_file.write_text(content)
    print(f"  exam{n}_full.md created ({len(content):,} chars)")


def exam_stats():
    """Print statistics across all exams."""
    exams = find_exams()
    if not exams:
        print("No exam files found.")
        return

    print(f"Found {len(exams)} exam(s): {exams}\n")
    print(f"{'Exam':<8} {'Chars':>8} {'~Tokens':>8} {'Questions':>10} {'Has Answers':>12}")
    print("-" * 52)

    total_chars = 0
    for n in exams:
        q_file = WORKSPACE / f"exam{n}.md"
        a_file = WORKSPACE / f"exam{n}_answers.md"
        content = q_file.read_text()
        chars = len(content)
        total_chars += chars

        # Count questions
        q_count = len(re.findall(r"\*\*Question \d+\*\*", content))
        has_answers = "YES" if a_file.exists() else ("INLINE" if "# ANSWERS" in content else "NO")

        print(f"exam{n:<4} {chars:>8,} {chars//4:>8,} {q_count:>10} {has_answers:>12}")

    print("-" * 52)
    print(f"{'Total':<8} {total_chars:>8,} {total_chars//4:>8,}")


def validate_exam(n):
    """Validate exam structure."""
    q_file = WORKSPACE / f"exam{n}.md"
    if not q_file.exists():
        print(f"exam{n}.md not found")
        return

    content = q_file.read_text()
    issues = []

    # Check question count
    questions = re.findall(r"\*\*Question (\d+)\*\*", content)
    if len(questions) < 20:
        issues.append(f"Only {len(questions)} questions found (expected 20)")

    # Check for lazy stems
    lazy = re.findall(r"Which of the following (?:is|are) (?:true|correct|false)", content, re.IGNORECASE)
    if lazy:
        issues.append(f"{len(lazy)} lazy stems found ('Which of the following is true...')")

    # Check for banned options
    banned = re.findall(r"(?:All|None) of the above", content, re.IGNORECASE)
    if banned:
        issues.append(f"{len(banned)} banned 'All/None of the above' options found")

    # Check for Select labels
    select_one = len(re.findall(r"Select one:", content))
    select_multi = len(re.findall(r"Select (?:one or more|\*\*\w+\*\*|all)", content, re.IGNORECASE))
    total_select = select_one + select_multi
    if total_select < len(questions):
        issues.append(f"Only {total_select} Select labels for {len(questions)} questions")

    multi_pct = (select_multi / max(total_select, 1)) * 100
    if multi_pct < 40:
        issues.append(f"Multi-answer questions: {multi_pct:.0f}% (need >=40%)")

    # Check for code blocks
    code_blocks = len(re.findall(r"```", content)) // 2
    code_pct = (code_blocks / max(len(questions), 1)) * 100
    if code_pct < 20:
        issues.append(f"Code questions: {code_pct:.0f}% (need >=20%)")

    if issues:
        print(f"exam{n}.md — {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print(f"exam{n}.md — All checks passed")

    print(f"  Questions: {len(questions)}, Single: {select_one}, Multi: {select_multi}, Code blocks: {code_blocks}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "merge":
        target = sys.argv[2] if len(sys.argv) > 2 else "all"
        if target == "all":
            for n in find_exams():
                merge_exam(n)
        else:
            merge_exam(int(target))

    elif cmd == "stats":
        exam_stats()

    elif cmd == "validate":
        if len(sys.argv) > 2 and sys.argv[2] == "all":
            for n in find_exams():
                validate_exam(n)
                print()
        elif len(sys.argv) > 2:
            validate_exam(int(sys.argv[2]))
        else:
            print("Usage: validate <N> or validate all")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
