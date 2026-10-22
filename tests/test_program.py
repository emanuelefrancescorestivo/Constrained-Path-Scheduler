"""D90: a whole semester's program in one document, pasted or as a PDF: its courses
found by their names, each course's dated or numbered schedule, its assessments in
their weeks, its hours of classes and credits; the import page that saves them all;
the course page that shows them week by week."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import pdftext, service, syllabus
from cps.syllabus import Topic, place, read
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)

# A made-up semester booklet, as a PDF's text comes out: a contents page, a header
# and a page number on every page, three courses written three ways.
BOOKLET = """Example School of Science
Semester program 2026-2027
Contents
Algebra 3 ................................ 2
Analysis 3 ............................... 3
Advanced Statistics ...................... 4
Programme 2026 · Year 2
1
Programme 2026 · Year 2
Algebra 3
Algebra
Lecturer: A. Teacher
Lecturer's webpage: https://example.org/teacher
Learning outcomes:
- Work with groups and rings.
Course description: The course introduces abstract algebra. It moves from groups to
rings, with many examples.
Date Week Topics Reading
Mon, 28-09-26 1 Groups and subgroups. Examples of
groups from geometry Smith, Chapter 1
Mon, 05-10-26 2 Continuation
Mon, 12-10-26 3 Mock exam 1, Chapters 1 to 2
Mon, 12-10-26 3 Cyclic groups Smith, Chapter 3
Mon, 19-10-26 4 Cosets and the order of a group [19 Oct: no class]
Language of instruction: English.
Number of credits : 6
Total number of hours for the semester: 48h Lectures + 24h Tutorials / semester.
Assessment methods:
- Quizzes during the semester: 20%
- Final exam: 80%
2
Programme 2026 · Year 2
Analysis 3
Learning outcomes:
- Differentiate in several variables.
Course description:
Limits and continuity
- Open sets
- Continuous maps
Differentiation
- Partial derivatives
- Chain rule
Number of credits : 5
Assessment methods: Partial exam 1 (25%), Partial exam 2 (25%), Final exam (50%)
3
Programme 2026 · Year 2
Advanced Statistics
Learning outcomes:
- Estimate parameters.
Course description:
Week 1: Random vectors
Week 2: Inequalities, the laws of large numbers and the central limit
theorem
Week 3: Estimators
Total number of hours for the semester: 21h lectures + 21h tutorials
Number of credits : 3
Assessment methods:
- 2 quizzes in weeks 2 and 3: 30% (15% + 15%)
- Final exam: 70%
4
"""


def _found(text: str, names: tuple[str, ...] = ()) -> dict[str, syllabus.Course]:
    return {c.name: c for c in syllabus.courses(text, names)}


# -- the reader ------------------------------------------------------------------------


def test_a_booklet_is_cut_into_its_courses_without_its_page_furniture():
    found = _found(BOOKLET)
    assert list(found) == ["Algebra 3", "Analysis 3", "Advanced Statistics"]
    algebra = found["Algebra 3"].text
    assert "Programme 2026" not in algebra and "\n2\n" not in algebra  # header, page number
    assert "Analysis 3" not in algebra
    # Without its contents page, each course is found by the title above its headings;
    # "Algebra" under "Algebra 3" is a subtitle, and the booklet's own title is not one.
    bare = "\n".join(line for line in BOOKLET.splitlines() if "....." not in line)
    assert list(_found(bare)) == ["Algebra 3", "Analysis 3", "Advanced Statistics"]
    # A text with one course, or none found, is given whole.
    assert [c.name for c in syllabus.courses("Week 1: Sets\nWeek 2: Maps")] == [""]


def test_a_dated_schedule_gives_one_topic_a_row_with_its_reading_and_its_mock_exam():
    topics = read(syllabus.course_text(_found(BOOKLET)["Algebra 3"].text))
    assert [(t.week, t.day, t.kind, t.title, t.material) for t in topics] == [
        (
            1,
            "2026-09-28",
            "topic",
            "Groups and subgroups. Examples of groups from geometry",
            "Smith, Chapter 1",
        ),
        # "Continuation" is not a topic; a mock exam is an assessment in its week.
        (3, "2026-10-12", "assessment", "Mock exam 1", "Chapters 1 to 2"),
        (3, "2026-10-12", "topic", "Cyclic groups", "Smith, Chapter 3"),
        (4, "2026-10-19", "topic", "Cosets and the order of a group", None),  # the note dropped
    ]


def test_a_weekday_settles_which_number_is_the_day():
    rows = "Tue 03/11/2026 Groups\nTue 10/11/2026 Rings"  # Tuesdays in November, day first
    assert [t.day for t in read(rows)] == ["2026-11-03", "2026-11-10"]
    us = "Tue 11/03/2026 Groups\nTue 11/10/2026 Rings"  # the same Tuesdays, month first
    assert [t.day for t in read(us)] == ["2026-11-03", "2026-11-10"]
    named = "lun. 5 oct. Ensembles\nlun. 12 oct. Relations"  # French, no year
    assert [(t.day, t.title) for t in read(named)] == [("10-05", "Ensembles"), ("10-12", "Relations")]
    # A number alone is a section of a list, not a date.
    assert [t.day for t in read("12.3 Groups\n12.4 Rings")] == [None, None]


def test_numbered_rows_are_weeks_only_under_a_week_column():
    table = "Week Content\n1 Computability and decidable\nlanguages\n2 Turing machines\n3 Midterm. Lambda"
    assert [(t.week, t.kind, t.title) for t in read(table)] == [
        (1, "topic", "Computability and decidable languages"),
        (2, "topic", "Turing machines"),
        (3, "assessment", "Midterm"),
        (3, "topic", "Lambda"),
    ]
    listed = "1. Introduction\n2. Linear models\n3. Backpropagation"  # the order of a list
    assert [t.week for t in read(listed)] == [None, None, None]


def test_a_list_skips_prose_and_the_labels_over_its_items_and_joins_wrapped_lines():
    analysis = read(syllabus.course_text(_found(BOOKLET)["Analysis 3"].text))
    assert [t.title for t in analysis] == [
        "Open sets",
        "Continuous maps",
        "Partial derivatives",
        "Chain rule",
    ]
    stats = read(syllabus.course_text(_found(BOOKLET)["Advanced Statistics"].text))
    assert [(t.week, t.title) for t in stats][1] == (
        2,
        "Inequalities, the laws of large numbers and the central limit theorem",
    )
    assert read("Semaine 3 : Pas de cours (vacances)\nSemaine 4 : Séries") == [Topic("Séries", week=4)]


def test_assessment_parts_keep_their_numbers_and_weeks_and_skip_their_splits():
    parts = {name: syllabus.assessment(c.text) for name, c in _found(BOOKLET).items()}
    assert [(p.label, p.percent) for p in parts["Analysis 3"]] == [
        ("Partial exam 1", 25),
        ("Partial exam 2", 25),
        ("Final exam", 50),
    ]
    assert [(p.label, p.percent, p.weeks) for p in parts["Advanced Statistics"]] == [
        ("2 quizzes in weeks 2 and 3", 30, (2, 3)),  # "(15% + 15%)" splits the 30 %
        ("Final exam", 70, ()),
    ]
    wrapped = (
        "Assessment: Quizzes 20% (at the start of each class), midterm 40% (written), exam\n40% (written)."
    )
    assert [(p.label, p.percent) for p in syllabus.assessment(wrapped)] == [
        ("Quizzes", 20),
        ("Midterm", 40),
        ("Exam", 40),
    ]


@pytest.mark.parametrize(
    ("line", "hours"),
    [
        ("Total number of hours for the semester: 48h Lectures + 24h Tutorials / semester.", 72.0),
        ("Total des heures pour le semestre en cours : 21 hours CM / 21 hours TP", 42.0),
        ("Ore di lezione: 48", 48.0),
        ("Contact hours: 45", 45.0),
        ("Hours: 3h per week", None),  # a week's hours, not the semester's
        ("Office hours: 14h-15h", None),  # the teacher's, not the course's
    ],
)
def test_hours_of_classes_are_read_from_the_line_that_gives_them(line, hours):
    assert syllabus.hours(line) == hours


def test_credits_on_their_own_line_are_not_mistaken_for_a_number_above():
    assert syllabus.credits("Contact hours: 45\nCredits: 4") == 4.0


def test_a_day_places_a_topic_in_the_teaching_week_it_falls_in():
    mondays = [date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 19)]  # a week off on 12 Oct
    topics = [
        Topic("a", week=1, day="2026-10-20"),  # the day, not the week number
        Topic("b", day="10-06"),  # no year: the teaching week's
        Topic("c", week=3, day="2026-10-14"),  # the week off: the teaching week before it
        Topic("d", week=4, day="2027-03-01"),  # after the last teaching week: none
    ]
    assert place(topics, [3, 3, 3], [1, 2, 3], mondays) == [3, 2, 2, None]
    # A schedule of another year: no day falls in a teaching week, the weeks given count.
    last_year = [Topic("a", week=1, day="2025-09-29"), Topic("b", week=2, day="2025-10-06")]
    assert place(last_year, [3, 3, 3], [1, 2, 3], mondays) == [1, 2]


# -- the PDF -----------------------------------------------------------------------------


def pdf(pages: list[list[str]]) -> bytes:
    """A small PDF made by hand: one line of Helvetica per entry, a page per list."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for lines in pages:
        shown = " ".join(
            "(" + x.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*" for x in lines
        )
        data = f"BT /F1 11 Tf 14 TL 50 780 Td {shown} ET".encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % len(objects)
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _pages(text: str, per_page: int = 30) -> list[list[str]]:
    lines = text.splitlines()
    return [lines[i : i + per_page] for i in range(0, len(lines), per_page)]


def test_a_pdf_s_text_is_read_page_after_page():
    found = pdftext.text(pdf(_pages(BOOKLET)))
    assert found.pages == found.read == len(_pages(BOOKLET)) > 1
    assert [c.name for c in syllabus.courses(found.text)] == [
        "Algebra 3",
        "Analysis 3",
        "Advanced Statistics",
    ]


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"BEGIN:VCALENDAR", "not_pdf"),
        (b"%PDF-1.4\n" + b"0" * (pdftext.MAX_BYTES + 1), "large"),
        (b"%PDF-1.4\n1 0 obj << broken", "unreadable"),
        (pdf([[]]), "empty"),  # a page without text, as a scan has
    ],
    # Short names: pytest puts a case's name in an environment variable, which Windows
    # caps at 32,767 characters; the large case's bytes would be its name.
    ids=["not-pdf", "large", "unreadable", "empty"],
)
def test_a_pdf_without_text_says_why(data, reason):
    with pytest.raises(pdftext.PdfError) as error:
        pdftext.text(data)
    assert error.value.reason == reason


def test_a_locked_pdf_says_so():
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf([["Week 1: Sets"]]))))
    writer.encrypt(user_password="secret", algorithm="RC4-128")
    locked = io.BytesIO()
    writer.write(locked)
    with pytest.raises(pdftext.PdfError) as error:
        pdftext.text(locked.getvalue())
    assert error.value.reason == "locked"


# -- the service and the pages ------------------------------------------------------------

COURSES = ("Algebra 3", "Analysis 3", "Advanced Statistics", "Deep Learning 1")


def _plan(store: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(name, None, familiarity=3) for name in COURSES],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(store, sub)
    return sub


def test_each_course_found_is_offered_to_the_plan_s_course_of_the_same_name(tmp_path):
    view = service.program_view(tmp_path, _plan(tmp_path), BOOKLET)
    assert [(c["name"], c["choice"]) for c in view["found"]] == [
        ("Algebra 3", 0),
        ("Analysis 3", 1),
        ("Advanced Statistics", 2),
    ]
    algebra = view["found"][0]
    # The schedule's days fall in the timetable's weeks of Algebra 3 (from 28 Sep).
    assert [(r["title"], r["when"]) for r in algebra["dated"]] == [("Mock exam 1", "Week of 12 Oct")]
    assert algebra["facts"]["hours"] == "72 hours of classes in the semester."
    # Names are matched by their words: "ALGEBRA 3 - CM" is Algebra 3, "Algebra 2" is not.
    assert service._matches(["Algebra 3", "Algebra 2"], ["Analysis 3", "ALGEBRA 3 - CM"]) == [1, None]


def test_saving_the_semester_puts_its_schedule_on_the_course_s_weeks(tmp_path):
    sub = _plan(tmp_path)
    saved, n = service.save_program(tmp_path, sub, BOOKLET, {0: 0, 1: 1, 2: 2}, share=False, now=NOW)
    assert n == 3
    course = service.course_view(tmp_path, saved, 0, NOW)
    weeks = {w["n"]: w for w in course["weeks"]}
    assert weeks[1]["readings"] == ["Smith, Chapter 1"]
    assert weeks[3]["assessments"] == ["Mock exam 1"] and weeks[3]["topics"] == ["Cyclic groups"]
    # 6 credits stand for 150 to 180 h; the syllabus's 72 h of classes leave 78 to 108.
    own = course["workload"]["own"]
    assert own["classes"] == 72 and "78 to 108 h" in own["says"]
    weeks_taught = len(course["weeks"])
    assert own["weekly"].startswith(f"About {78 // weeks_taught} to ")
    # An assessment held "in weeks 2 and 3" is shown in those weeks.
    stats = {w["n"]: w for w in service.course_view(tmp_path, saved, 2, NOW)["weeks"]}
    assert "2 quizzes in weeks 2 and 3" in stats[2]["assessments"]
    # A session reviews the week's topics, not its mock exam.
    index = service._syllabus_index(saved)
    assert all("Mock exam 1" not in titles for titles in index.values())


def test_a_topic_saved_again_from_its_line_keeps_its_day_and_reading(tmp_path):
    sub = _plan(tmp_path)
    saved, _ = service.save_program(tmp_path, sub, BOOKLET, {0: 0}, share=False, now=NOW)
    text = service.course_topics_view(tmp_path, saved, 0)["text"]
    assert "Week 1: Groups and subgroups" in text  # the box holds lines, without the reading
    again = service.save_course_topics(tmp_path, saved, 0, text, share=False)
    items = again.options["topics"]["algebra 3"]["items"]
    assert items[0]["material"] == "Smith, Chapter 1" and items[0]["day"] == "2026-09-28"


def test_a_course_without_a_topic_list_keeps_its_topics_and_takes_the_facts(tmp_path):
    sub = _plan(tmp_path)
    first = service.save_course_topics(tmp_path, sub, 3, "Week 1: Perceptrons", share=False)
    prose = (
        "Deep Learning 1\nCourse description: An introduction to neural networks. It is taught in "
        "English.\nNumber of credits : 3"
    )
    saved, n = service.save_program(tmp_path, first, prose, {0: 3}, share=False, now=NOW)
    entry = saved.options["topics"]["deep learning 1"]
    assert (
        n == 1 and [i["title"] for i in entry["items"]] == ["Perceptrons"] and entry["facts"]["credits"] == 3
    )


def test_the_import_page_reads_a_pdf_and_saves_what_the_student_chose(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        assert "/courses/import" in web.get(f"/p/{token}/courses").text
        assert web.get(f"/p/{token}/courses/import").status_code == 200
        upload = {"pdf": ("program.pdf", pdf(_pages(BOOKLET)), "application/pdf")}
        read_back = web.post(f"/p/{token}/courses/import", data={"action": "read"}, files=upload)
        assert read_back.status_code == 200
        page = read_back.text
        assert "Algebra 3" in page and "Mock exam 1" in page and 'name="course-0"' in page
        digest = page.split('name="digest" value="')[1].split('"')[0]
        text = service.pdf_text(pdf(_pages(BOOKLET)))[0]
        plan = service.load_subscription(tmp_path, token)
        algebra = service.topic_courses(plan).index("Algebra 3")
        # A text changed since it was read is read again, not saved.
        changed = web.post(
            f"/p/{token}/courses/import",
            data={"action": "save", "digest": digest, "text": text + "\nmore", "course-0": str(algebra)},
        )
        assert changed.status_code == 200 and "Found in your document" in changed.text
        # The text comes back as a browser sends a text box: its line breaks as CRLF.
        sent = service.program_text(text).replace("\n", "\r\n")
        done = web.post(
            f"/p/{token}/courses/import",
            data={"action": "save", "digest": digest, "text": sent, "course-0": str(algebra)},
            follow_redirects=False,
        )
        assert done.status_code == 303 and done.headers["location"].endswith("/courses?imported=1")
        assert "Program saved for 1 course." in web.get(done.headers["location"]).text
        course = web.get(f"/p/{token}/courses/{algebra}").text
        assert "Mock exam 1" in course and "Smith, Chapter 1" in course
        bad = web.post(
            f"/p/{token}/courses/import", files={"pdf": ("x.pdf", b"not a pdf", "application/pdf")}
        )
        assert bad.status_code == 400 and "That file is not a PDF." in bad.text
