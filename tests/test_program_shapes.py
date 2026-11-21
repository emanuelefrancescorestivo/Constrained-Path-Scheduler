"""
A first year's program read whole (DECISIONS.md D180): the shapes the owner's Year 1
syllabus showed that the reader missed, each written here for another course, as the
reader must read any program of that shape (AUDIT 122). The owner's file is not in the
repository.

What must hold: a word a PDF split at its hyphen is one word again; "Continuation of last
course" is no topic; a chapter with a paragraph under it is one topic, and with items under
it their label; a total of hours with its split in brackets is the total; a weight said in
brackets ("20% of the final grade") or after a colon ("Presentation: 20%.") is read, and a
table's wrapped "Exam" cell does not take the assessment's place; a session table's
objective column is not its topic; a "Mid-term Exam" and a final exam named in place of a
row's number are assessments, the latter on its date; a January written with the old
year in a schedule that runs forward is the next year's.
"""

from __future__ import annotations

from cps import syllabus
from cps.syllabus import read


def test_a_word_a_pdf_split_at_its_hyphen_is_one_word_again():
    assert (
        syllabus._title("the long -run divergence over Units 5 –10")
        == "the long-run divergence over Units 5–10"
    )
    assert (
        syllabus._title("Unit 1 - Prosperity and limits") == "Unit 1 - Prosperity and limits"
    )  # a dash, kept


def test_a_continuation_of_the_last_class_is_no_topic():
    text = (
        "Date Week Topics Materials\n"
        "Tue, 15-09-26 1 Kinematics in one dimension Tipler, Chapter 2\n"
        "Tue, 22-09-26 2 Continuation of last course\n"
        "Tue, 29-09-26 3 Continuation: projectile motion\n"
        "Tue, 06-10-26 4 Suite du cours précédent\n"
    )
    assert [(t.week, t.title) for t in read(text)] == [
        (1, "Kinematics in one dimension"),
        (3, "projectile motion"),
    ]


def test_a_chapter_with_a_paragraph_is_one_topic_and_over_items_their_label():
    paragraph = (
        "Course Description:\n"
        "Chapter 1 — Heat and Temperature\n"
        "Thermal equilibrium; the zeroth law; thermometers and temperature scales;\n"
        "thermal expansion of solids and liquids.\n"
        "\n"
        "Chapter 2 — The First Law\n"
        "Work and heat in thermodynamic processes; internal energy; the first law and its applications to\n"
        "cyclic processes and heat engines.\n"
    )
    assert [t.title for t in read(paragraph)] == ["Heat and Temperature", "The First Law"]
    items = (
        "CAPITOLO 1: INSIEMI\n- Insiemi e funzioni\n- Induzione\n"
        "CAPITOLO 2: NUMERI REALI\n- Estremo superiore\n"
    )
    assert [t.title for t in read(items)] == ["Insiemi e funzioni", "Induzione", "Estremo superiore"]


def test_a_total_of_hours_with_its_split_in_brackets_is_the_total():
    line = "Total number of hours for the current semester: 60h / semester (30h of lectures, 30h of labs)"
    assert syllabus.hours(line) == 60.0
    assert syllabus.hours("Total des heures pour le semestre en cours: 18h CM + 12h TD") == 30.0


def test_weights_in_brackets_or_after_a_colon_are_read():
    grade = (
        "Assessment Methods: Continuous assessment through weekly 5 –10-minute quizzes (25% of\n"
        "the final grade) and one written exam (75% of the final grade). Attendance is required.\n"
    )
    assert [(p.label, p.percent) for p in syllabus.assessment(grade)] == [
        ("Continuous assessment through weekly 5–10-minute quizzes", 25),
        ("One written exam", 75),
    ]
    colons = "Assessment Methods: Essay: 40%., Mid-term Exam: 20%., Final Exam: 40%.\n"
    assert [(p.label, p.percent) for p in syllabus.assessment(colons)] == [
        ("Essay", 40),
        ("Mid-term Exam", 20),
        ("Final Exam", 40),
    ]


SESSIONS = (
    "Course Description: A first course in sociology.\n"
    "Lectures:\n"
    "Session Date Content Objective\n"
    "1 Sep 16th,\n"
    "\n"
    "Theme 1 - Societies and\n"
    "their institutions\n"
    "Students will be able to compare institutions across societies using survey data and\n"
    "explain what changed after industrialisation.\n"
    "2 Oct 14th,\n"
    "\n"
    "Mid-term Exam Covering Themes 1–3, students show they can read a table of survey data.\n"
    "3 Dec 9th, 2026\n"
    "Theme 4 - Inequality\n"
    "Students will be able to measure inequality with a Gini coefficient.\n"
    "4 Jan 13th, 2026\n"
    "Review & Essays Review & Essays\n"
    "Final\n"
    "Exam\n"
    "Jan 27th, 2026\n"
    "Final Exam Covering the whole course, students demonstrate they can apply a model.\n"
    "\n"
    "Language of Instruction: English.\n"
    "Assessment Methods: Essay: 30%., Mid-term Exam: 20%., Final Exam: 50%.\n"
)


def test_a_session_table_keeps_its_topics_its_exams_and_its_dates_forward():
    topics = read(syllabus.course_text(SESSIONS))
    assert [(t.week, t.day, t.kind, t.title) for t in topics] == [
        (1, "09-16", "topic", "Theme 1 - Societies and their institutions"),
        (2, "10-14", "assessment", "Mid-term Exam Covering Themes 1–3"),
        (3, "2026-12-09", "topic", "Theme 4 - Inequality"),
        (4, "2027-01-13", "topic", "Review & Essays"),  # January after December: the next year
        (None, "2027-01-27", "assessment", "Final Exam Covering the whole course"),  # named, not numbered
    ]
    # The schedule's wrapped "Exam" cell is no heading: the weights are the section's.
    assert [(p.label, p.percent) for p in syllabus.assessment(SESSIONS)] == [
        ("Essay", 30),
        ("Mid-term Exam", 20),
        ("Final Exam", 50),
    ]
