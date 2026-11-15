import os

import pytest

from notes_cli.weeks import Sources, gather, week_of


@pytest.mark.parametrize(
    "name, expected",
    [
        # The plan's examples (all must hold).
        ("DBA LGT W3", 3),
        ("FM2 W1 LGT", 1),
        ("SWE S1W2 DevOps", 2),
        ("4ABC1DSA_Lecture_Notes_Week_5_Insertion_Sort", 5),
        ("DSA1_notes_week10", 10),
        ("5abc2fm2_lgt4", 4),
        ("fm1_week4", 4),
        ("Week 03", 3),
        ("PRG T2W7", 7),
        ("DSA Lab 2", None),
        ("4ABC1DSA_SGT_3", None),
        ("SGT1-software-processes", None),
        ("Ex2_data", None),
        ("week2_cooklevin.pdf", 2),
        # Real names not covered by the plan's examples.
        ("Week 1 Slides-20261114", 1),          # the date is not a week
        ("Week 01", 1),
        ("Module Overview", None),
        ("1_1_Overview.pdf", None),             # IAS slide: section numbers, not weeks
        ("07_1_Overview Markup Languages.pdf", None),
        ("week2-sat.pdf", 2),
        ("SGT Week 11", 11),
        ("FM1 Week 3 SGT Exercises", 3),
        ("fm1sgt_week11", 11),
        ("IST SGT W1 Tasks", 1),
        ("ELC SGT 1", None),                    # SGT session numbers are not weeks
        ("sgt_01_elc", None),
        ("Lab_2", None),
        ("lab_1", None),
        ("4ABC1DSA_SGT_1", None),
        ("5abc2fm2", None),                     # module codes are not weeks
        ("FM2 W6", 6),
        ("SWE S1W1", 1),
        ("PAR W6 LGT", 6),
        ("Ex2_data.pdf", None),
        # Defensive cases.
        ("", None),
        ("LGT 2026", None),
        ("Week 123", None),
        ("W3 recap of W2", None),               # two different weeks: ambiguous
        ("Week 3 W3", 3),                        # the same week twice is fine
        ("wk 5", 5),
        ("midweek3", None),
        ("SW2", None),
        ("HW3 solutions", None),
        ("IAS W3C standards", None),
        ("IAS W7 W3C standards", 7),
        ("SWE S2W1", 1),                         # the semester is ignored
    ],
)
def test_week_of(name, expected):
    assert week_of(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "W1-2", "Week3-4", "W10-11", "W3&4", "week 3 & 4", "Revision W1-5", "week1_2",
        "week1-2_intro", "week1_2.pdf", "W1-W2", "Week 3 - Week 4",
        "W1–2", "Week3–4", "week 3 – 4",          # en dash
        "W1+2", "week 3 + 4",
        "W1 and 2", "week 3 and 4", "Week 3 and Week 4",
        "W1 to 2", "week 3 to 4", "weeks 3 to 5 lgt",
        "lgt4-5", "5abc2fm2_lgt4&5",
    ],
)
def test_week_ranges_are_unassigned(name):
    assert week_of(name) is None


@pytest.mark.parametrize(
    "name, expected",
    [
        ("W3 slides 1-20", 3),
        ("week 3 - 2026-10-05", 3),
        ("Week 1 Slides-20261114", 1),
        ("week7_2opt", 7),
        ("week7_2opt.pdf", 7),
        ("week7_proving_2opt.pdf", 7),
        ("4ABC1DSA_Lecture_Notes_Week_5_Insertion_Sort", 5),
        ("week2_cooklevin.pdf", 2),
        ("week2-sat.pdf", 2),
        ("W3 topics", 3),                        # "to" only as a whole word
        ("W3 andrew", 3),
        ("SWE S1W2 DevOps", 2),
        ("PRG T2W7", 7),
    ],
)
def test_not_a_range(name, expected):
    assert week_of(name) == expected


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n")
    return path


def rel(paths, root):
    return [p.relative_to(root).as_posix() for p in paths]


@pytest.fixture
def fm2(cfg):
    """A FM2 module folder with slides, notes and things that must never be sources."""
    root = cfg.module_dir("FM2")
    for name in [
        "Lecture-Slides/Week 1 Slides-20261114/week1_turing.pdf",
        "Lecture-Slides/Week 1 Slides-20261114/week1_anbn.pdf",
        "Lecture-Slides/Week 2 Slides-20261114/week2-sat.pdf",
        "Lecture-Slides/Week 10 Slides/week10_flow.pdf",
        "Lecture-Slides/Module Overview/0_1_Welcome.pdf",
        "Lecture Notes/FM2 W1.pdf",
        "Lecture Notes/FM2 W2.pdf",
        "Lecture Notes/FM2 W10.pdf",
        "LGT/FM2 W1 LGT.pdf",
        "LGT/5abc2fm2_lgt4.pdf",
        "SGT/FM2 W2 SGT.pdf",
        "SGT/Lab_2.pdf",
        # never sources:
        "Flashcards/old.pdf",
        "Practice Exams/exam1.pdf",
        "loose.pdf",
    ]:
        touch(root / name)
    (root / "Lecture Notes" / "FM2 W1.txt").write_text("not a pdf")
    touch(root / "Lecture Notes" / ".hidden W1.pdf")
    touch(root / "Lecture-Slides" / ".cache" / "week1_x.pdf")
    (root / "Flashcards" / "FM2 Week 01.txt").write_text("#separator:tab\n")
    return root


def test_gather_all(cfg, fm2):
    sources = gather(cfg, "FM2")

    assert isinstance(sources, Sources)
    assert rel(sources.slides, fm2) == [
        "Lecture-Slides/Module Overview/0_1_Welcome.pdf",
        "Lecture-Slides/Week 1 Slides-20261114/week1_anbn.pdf",
        "Lecture-Slides/Week 1 Slides-20261114/week1_turing.pdf",
        "Lecture-Slides/Week 2 Slides-20261114/week2-sat.pdf",
        "Lecture-Slides/Week 10 Slides/week10_flow.pdf",
    ]
    assert rel(sources.notes, fm2) == [
        "Lecture Notes/FM2 W1.pdf",
        "Lecture Notes/FM2 W2.pdf",
        "Lecture Notes/FM2 W10.pdf",
        "LGT/5abc2fm2_lgt4.pdf",
        "LGT/FM2 W1 LGT.pdf",
        "SGT/FM2 W2 SGT.pdf",
        "SGT/Lab_2.pdf",
    ]
    assert sources.unassigned == []
    # Paths are inside the module folder as the user sees it.
    assert all(p.is_relative_to(fm2) for p in sources.slides + sources.notes)


def test_gather_week_filters_and_unassigned(cfg, fm2):
    sources = gather(cfg, "fm2", week=1)

    assert rel(sources.slides, fm2) == [
        "Lecture-Slides/Week 1 Slides-20261114/week1_anbn.pdf",
        "Lecture-Slides/Week 1 Slides-20261114/week1_turing.pdf",
    ]
    assert rel(sources.notes, fm2) == ["Lecture Notes/FM2 W1.pdf", "LGT/FM2 W1 LGT.pdf"]
    assert rel(sources.unassigned, fm2) == [
        "Lecture-Slides/Module Overview/0_1_Welcome.pdf",
        "SGT/Lab_2.pdf",
    ]


def test_gather_week_with_no_matching_files(cfg, fm2):
    sources = gather(cfg, "FM2", week=7)

    assert sources.slides == [] and sources.notes == []
    assert rel(sources.unassigned, fm2) == [
        "Lecture-Slides/Module Overview/0_1_Welcome.pdf",
        "SGT/Lab_2.pdf",
    ]


def test_slide_folder_week_beats_filename(cfg):
    root = cfg.module_dir("FM2")
    touch(root / "Lecture-Slides" / "Week 5 Slides-20261114" / "week7_2opt.pdf")
    touch(root / "Lecture-Slides" / "Week 5 Slides-20261114" / "extra" / "week7_more.pdf")
    touch(root / "Lecture-Slides" / "loose" / "week7_alone.pdf")

    assert rel(gather(cfg, "FM2", week=5).slides, root) == [
        "Lecture-Slides/Week 5 Slides-20261114/extra/week7_more.pdf",
        "Lecture-Slides/Week 5 Slides-20261114/week7_2opt.pdf",
    ]
    assert rel(gather(cfg, "FM2", week=7).slides, root) == ["Lecture-Slides/loose/week7_alone.pdf"]


def test_note_filename_week_beats_folder(cfg):
    root = cfg.module_dir("DBA")
    touch(root / "LGT" / "Week 2" / "DBA LGT W3.pdf")
    touch(root / "LGT" / "Week 2" / "untitled.pdf")

    assert rel(gather(cfg, "DBA", week=3).notes, root) == ["LGT/Week 2/DBA LGT W3.pdf"]
    assert rel(gather(cfg, "DBA", week=2).notes, root) == ["LGT/Week 2/untitled.pdf"]


def test_gather_uses_configured_subfolders(cfg):
    root = cfg.module_dir("DBA")
    touch(root / "Lecture Notes" / "DBA W1.pdf")
    touch(root / "Other" / "DBA W1 other.pdf")

    assert rel(gather(cfg, "DBA").notes, root) == ["Lecture Notes/DBA W1.pdf"]


def test_gather_pdf_suffix_case_insensitive(cfg):
    root = cfg.module_dir("DBA")
    touch(root / "SGT" / "DBA SGT W4.PDF")

    assert rel(gather(cfg, "DBA").notes, root) == ["SGT/DBA SGT W4.PDF"]


def test_gather_ignores_symlinks_out_of_module(cfg, tmp_path):
    root = cfg.module_dir("DBA")
    outside = tmp_path / "outside"
    touch(outside / "DBA W9.pdf")
    touch(outside / "slides" / "week9.pdf")
    touch(root / "Lecture Notes" / "DBA W1.pdf")
    touch(root / "Flashcards" / "secret W1.pdf")
    os.symlink(outside / "DBA W9.pdf", root / "Lecture Notes" / "DBA W9.pdf")
    os.symlink(outside / "slides", root / "Lecture-Slides")
    os.symlink(outside, root / "LGT")
    (root / "SGT").mkdir()
    os.symlink(root / "Flashcards" / "secret W1.pdf", root / "SGT" / "link W1.pdf")
    os.symlink(root / "Lecture Notes", root / "SGT" / "loop")

    sources = gather(cfg, "DBA")

    assert sources.slides == []
    assert rel(sources.notes, root) == ["Lecture Notes/DBA W1.pdf"]


def test_gather_includes_symlink_within_source_folders(cfg):
    root = cfg.module_dir("DBA")
    touch(root / "Lecture Notes" / "DBA W1.pdf")
    (root / "SGT").mkdir()
    os.symlink(root / "Lecture Notes" / "DBA W1.pdf", root / "SGT" / "DBA SGT W1.pdf")

    assert rel(gather(cfg, "DBA").notes, root) == ["Lecture Notes/DBA W1.pdf", "SGT/DBA SGT W1.pdf"]


def test_gather_missing_module_folder(cfg):
    assert gather(cfg, "PAR") == Sources(slides=[], notes=[], unassigned=[])


def test_gather_is_deterministic(cfg, fm2):
    assert gather(cfg, "FM2") == gather(cfg, "FM2")
    assert gather(cfg, "FM2", 2) == gather(cfg, "FM2", 2)
