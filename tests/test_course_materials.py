"""
A course's materials, read from where its teacher keeps them (DECISIONS.md D174). The
owner: "not all courses are managed the same way by teachers, so it will need to be
handled situation by situation". The pages below are written for these tests in the
shapes six real courses took (sections by kind with numbered files, a section per
session, dated subsections, a teacher's site with a dated programme, a reading list week
by week); none is a real course's page. What must hold: each item is read with its kind
and address and placed by its own name, then its subsection's, then its section's; what
names nothing is for the whole course, what names an exam is exam practice; forums and
grades are left out; the items land on the timetable's teaching weeks; a page read again
keeps what the student moved; a Moodle page links the course to Moodle; nothing of the
page is kept but names and addresses.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

from cps import coursepages, service

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # the second teaching week
SITE = "https://moodle.example.edu"


def _activity(number: int, modtype: str, name: str, icon: str = "f/pdf") -> str:
    return (
        f'<li class="activity activity-wrapper {modtype} modtype_{modtype}  hasinfo " id="module-{number}" '
        f'data-for="cmitem" data-id="{number}">'
        f'<div class="activity-item focus-control " data-activityname="{name}" data-region="activity-card">'
        f'<img src="{SITE}/theme/image.php/boost/core/1/{icon}?filtericon=1" class="activityicon" alt="">'
        f'<a href="{SITE}/mod/{modtype}/view.php?id={number}" class="aalink stretched-link">'
        f'<span class="instancename">{name}<span class="accesshide"> File</span></span></a></div></li>'
    )


def _section(number: int, name: str, items: list[str], delegated: bool = False) -> str:
    kind = " delegated-section" if delegated else ""
    return (
        f'<li id="section-{number}" class="section course-section main clearfix{kind}" '
        f'data-sectionid="{number}" '
        f'data-for="section" data-number="{number}" data-sectionname="{name}">'
        f'<div class="course-section-header"><h3 class="h4 sectionname">{name}</h3></div>'
        f'<ul class="section" data-for="cmlist">{"".join(items)}</ul></li>'
    )


def _subsection(number: int, section: int, name: str, items: list[str]) -> str:
    return (
        f'<li class="activity activity-wrapper subsection modtype_subsection" id="module-{number}" '
        f'data-for="cmitem" data-id="{number}"><div class="activity-item" data-activityname="{name}">'
        f"{_section(section, name, items, delegated=True)}</div></li>"
    )


def moodle_page(title: str, course_id: int, sections: list[str], lang: str = "en") -> str:
    return (
        f'<!DOCTYPE html><html dir="ltr" lang="{lang}"><head><title>Course: {title} | Moodle</title>'
        '<script>M.cfg = {"wwwroot":"https:\\/\\/moodle.example.edu","sesskey":"abc"};</script></head>'
        f'<body id="page-course-view-topics" class="format-topics path-course course-{course_id} context-9">'
        f'<div class="course-content"><ul class="topics">{"".join(sections)}</ul></div>'
        '<footer><a href="https://docs.moodle.org/">Moodle documentation</a></footer></body></html>'
    )


# Sections by kind, their files numbered: lectures "L1.", exercise sheets "1." with their
# solutions, a mock exam, books and a link for the whole course; a forum and a grades file
# that are not material.
BY_KIND = moodle_page(
    "Analysis 3",
    501,
    [
        _section(
            0,
            "General",
            [
                _activity(1, "forum", "Announcements", "monologo"),
                _activity(2, "resource", "Syllabus"),
                _activity(3, "page", "How the course runs", "monologo"),
            ],
        ),
        _section(
            1,
            "Lectures",
            [
                _activity(11, "resource", "L1. Norms and inner products"),
                _activity(12, "resource", "L2. Open sets"),
                _activity(13, "resource", "L3. Sequences"),
                _activity(14, "resource", "L4. Compact sets"),
            ],
        ),
        _section(
            2,
            "Tutorials",
            [
                _activity(21, "resource", "1. Norms"),
                _activity(22, "resource", "1. Norms SOLVED"),
                _activity(23, "resource", "3. Sequences"),
            ],
        ),
        _section(
            3,
            "Additional material",
            [
                _activity(31, "resource", "Revision sheet 1-4"),
                _activity(32, "resource", "Mock exam 1 SOLVED"),
                _activity(33, "url", "Partial derivatives, drawn", "monologo"),
                _activity(34, "folder", "Old notes", "monologo"),
            ],
        ),
        _section(4, "Grades", [_activity(41, "resource", "Grades of quiz 1")]),
    ],
)

# A section per session, the slides' and references' names numbered at their end, two
# sessions named without their number after the sixth; a dated subsection.
BY_SESSION = moodle_page(
    "Economics of Data",
    502,
    [
        _section(0, "General", [_activity(1, "forum", "Announcements", "monologo")]),
        _section(
            1,
            "Introduction",
            [_activity(2, "resource", "Course_Outline"), _activity(3, "resource", "Data_00")],
        ),
        _section(
            2,
            "Session 1",
            [_activity(4, "resource", "Data_01"), _activity(5, "folder", "Readings_01", "monologo")],
        ),
        _section(3, "Session 2", [_activity(6, "resource", "Data_02")]),
        _section(4, "Session: Markets for data", [_activity(7, "resource", "Data-Markets")]),
        _section(5, "Session: Privacy", [_activity(8, "resource", "Data-Privacy")]),
        _section(
            6,
            "Part two",
            [_subsection(90, 7, "Session 1 (18 Nov.)", [_activity(9, "resource", "Practical session")])],
        ),
    ],
)

# A teacher's own site: a dated programme, each class's exercises and solutions linked
# relatively; its authors' pages in the introduction; further reading and credits.
TEACHER_SITE = """<!DOCTYPE html><html lang="en"><head><title>Theory of Computing</title></head><body>
<h1>Theory of Computing</h1>
<p>Taught by <a href="https://example.org/~ada">Ada</a> and <a href="https://example.org/~alan">Alan</a>.</p>
<h3>Lecture notes</h3><p><a href="materials/notes.pdf">Lecture notes</a>, updated after each class.</p>
<h3>Programme</h3>
<table><tr><th>#</th><th>Date</th><th>Topic(s)</th><th>Materials</th></tr>
<tr><td>01</td><td>Mon 28 Sep</td><td>Computable functions</td>
<td><a href="materials/tut-01.pdf">exercises</a> <a href="materials/tut-01-sol.pdf">solutions</a></td></tr>
<tr><td>02</td><td>Wed 30 Sep</td><td>Turing machines (<a href="materials/lec-02.pdf">slides</a>)</td>
<td><a href="materials/tut-02.pdf">exercises</a> <a href="materials/quiz-02.pdf">quiz</a></td></tr>
<tr><td>03</td><td>Mon 5 Oct</td><td>Lambda calculus</td>
<td><a href="materials/tut-03.pdf">exercises</a></td></tr>
</table>
<h3>Further reading</h3><p><a href="https://example.com/book">A book on automata</a></p>
<h3>Acknowledgements</h3><p><a href="https://example.org/~friend">A friend</a></p>
</body></html>"""

# A reading list as a shared document exports it: weeks spelled out with their dates in
# a bold paragraph, references with their address written after them, one address alone
# on its line under its reference, a link through Google's redirect.
READING_LIST = """<html><head><title>Ethics Reading List</title></head><body>
<p><span>Course folder: </span><a href="https://www.google.com/url?q=https://drive.google.com/drive/folders/abc123&amp;sa=D">Readings</a></p>
<p><span style="font-weight:700">Week one (What is AI?) Sep 30th</span></p>
<p><span>Primary</span></p>
<p><span>Turing, A. (1950). Computing machinery and intelligence. Mind. https://doi.org/10.1093/mind/LIX.236.433</span></p>
<p><span>Searle, J. (1980). Minds, brains, and programs.</span></p>
<p><span>https://example.com/searle.pdf</span></p>
<p><span style="font-weight:700">Week two (Moral status) Oct 7th</span></p>
<p><span>Nagel, T. (1974). What is it like to be a bat? </span><a href="https://www.google.com/url?q=https://doi.org/10.2307/2183914&amp;sa=D">https://doi.org/10.2307/2183914</a></p>
</body></html>"""


def test_a_moodle_page_by_kind_is_read_item_by_item():
    found = coursepages.read(BY_KIND)
    assert (found.source, found.name, found.site, found.course_id) == ("moodle", "Analysis 3", SITE, 501)
    rows = [(m.title, m.kind, m.lecture, m.shelf, m.group) for m in found.items]
    assert rows == [
        ("Syllabus", "file", None, "course", "General"),
        ("How the course runs", "page", None, "course", "General"),
        ("L1. Norms and inner products", "file", 1, "course", "Lectures"),
        ("L2. Open sets", "file", 2, "course", "Lectures"),
        ("L3. Sequences", "file", 3, "course", "Lectures"),
        ("L4. Compact sets", "file", 4, "course", "Lectures"),
        ("1. Norms", "file", 1, "course", "Tutorials"),
        ("1. Norms SOLVED", "file", 1, "course", "Tutorials"),
        ("3. Sequences", "file", 3, "course", "Tutorials"),
        ("Revision sheet 1-4", "file", None, "course", "Additional material"),
        ("Mock exam 1 SOLVED", "file", None, "exams", "Additional material"),
        ("Partial derivatives, drawn", "link", None, "course", "Additional material"),
        ("Old notes", "folder", None, "course", "Additional material"),
    ]  # the forum and the grades are not material
    assert found.items[2].url == f"{SITE}/mod/resource/view.php?id=11"


def test_sessions_go_on_numbering_and_a_dated_subsection_gives_its_day():
    found = coursepages.read(BY_SESSION)
    where = {m.title: (m.lecture, m.day) for m in found.items}
    assert where["Data_00"] == (None, None)  # the introduction: for the whole course
    assert where["Data_01"] == (1, None) and where["Readings_01"] == (1, None)
    assert where["Data_02"] == (2, None)
    assert where["Data-Markets"] == (3, None) and where["Data-Privacy"] == (4, None)
    assert where["Practical session"] == (1, "11-18")


def test_a_teachers_site_gives_each_class_its_files():
    found = coursepages.read(TEACHER_SITE, "https://example.org/toc/")
    rows = [(m.title, m.url.rsplit("/", 1)[1], m.lecture, m.day, m.shelf) for m in found.items]
    assert rows == [
        ("Lecture notes", "notes.pdf", None, None, "course"),
        ("Computable functions · exercises", "tut-01.pdf", 1, "09-28", "course"),
        ("Computable functions · solutions", "tut-01-sol.pdf", 1, "09-28", "course"),
        ("Turing machines · slides", "lec-02.pdf", 2, "09-30", "course"),
        ("Turing machines · exercises", "tut-02.pdf", 2, "09-30", "course"),
        ("Turing machines · quiz", "quiz-02.pdf", None, None, "exams"),
        ("Lambda calculus · exercises", "tut-03.pdf", 3, "10-05", "course"),
        ("A book on automata", "book", None, None, "course"),
    ]  # the authors' pages and the credits are not the course's
    assert found.items[1].url == "https://example.org/toc/materials/tut-01.pdf"
    saved = coursepages.read(TEACHER_SITE)  # saved without its address: relative links lost
    assert saved.unresolved == 7 and [m.title for m in saved.items] == ["A book on automata"]


def test_a_reading_list_places_each_reference_on_its_week():
    found = coursepages.read(READING_LIST)
    rows = [(m.title[:40], m.url, m.week, m.day) for m in found.items]
    assert rows == [
        ("Readings", "https://drive.google.com/drive/folders/abc123", None, None),
        ("Turing, A. (1950). Computing machinery a", "https://doi.org/10.1093/mind/LIX.236.433", 1, "09-30"),
        ("Searle, J. (1980). Minds, brains, and pr", "https://example.com/searle.pdf", 1, "09-30"),
        ("Nagel, T. (1974). What is it like to be ", "https://doi.org/10.2307/2183914", 2, "10-07"),
    ]
    assert found.items[0].kind == "folder" and found.items[2].kind == "file"


def test_another_page_of_moodle_gives_nothing():
    dashboard = (
        "<html><head><title>My courses | Moodle</title>"
        '<script>M.cfg = {"wwwroot":"https:\\/\\/moodle.example.edu"};</script></head>'
        '<body id="page-my-index"><nav><a href="/user/profile.php">Profile</a></nav></body></html>'
    )
    found = coursepages.read(dashboard)
    assert found.source == "moodle" and found.items == ()


# --------------------------------------------------------------------------- #
# In the app
# --------------------------------------------------------------------------- #

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402

FETCHED: list[str] = []


def _fetch(url: str) -> tuple[bytes, str]:
    FETCHED.append(url)
    if "docs.google.com" in url:
        return READING_LIST.encode(), url
    return TEACHER_SITE.encode(), "https://example.org/toc/"


@pytest.fixture
def web(tmp_path):
    FETCHED.clear()
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, fetch_page=_fetch)
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _plan(web) -> tuple[str, int]:
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    service.save_subscription(web.root, sub)
    return sub.token, service.topic_courses(sub).index("Analysis 3")


def _upload(web, token, index, html: str):
    return web.post(
        f"/p/{token}/courses/{index}/materials",
        files={"page": ("Course Analysis 3 Moodle.html", html.encode(), "text/html")},
    )


def test_a_saved_moodle_page_lands_on_the_course_weeks(web):
    token, index = _plan(web)
    answer = _upload(web, token, index, BY_KIND)
    assert answer.status_code == 200
    page = unescape(answer.text)
    assert "13 items read" in page and "on 2 weeks" in page and "1 for exam practice" in page
    assert "The course is linked to its page on Moodle too." in page
    sub = service.load_subscription(web.root, token)
    assert sub.options["moodle_courses"][service.course_key("Analysis 3")] == {"site": SITE, "id": 501}
    weeks = {x["title"]: x["week"] for x in sub.options["materials"][service.course_key("Analysis 3")]}
    # Two lectures a week: lectures 1 and 2 in the first teaching week, 3 and 4 in the second.
    assert weeks["L1. Norms and inner products"] == weeks["L2. Open sets"] == "2026-09-28"
    assert weeks["L3. Sequences"] == weeks["3. Sequences"] == "2026-10-05"
    assert weeks["Syllabus"] == "" and weeks["Mock exam 1 SOLVED"] == ""
    # The course page lists each week's materials; the focus screen this week's.
    course = unescape(web.get(f"/p/{token}/courses/{index}").text)
    assert "L3. Sequences" in course and f"{SITE}/mod/resource/view.php?id=13" in course
    view = service.focus_view(service.start_focus(sub, "Analysis 3", now=NOW), now=NOW)
    assert [m["title"] for m in view["materials"]] == ["L3. Sequences", "L4. Compact sets", "3. Sequences"]


def test_an_item_moved_stays_where_the_student_put_it_when_the_page_is_read_again(web):
    token, index = _plan(web)
    _upload(web, token, index, BY_KIND)
    items = service.load_subscription(web.root, token).options["materials"][service.course_key("Analysis 3")]
    revision = next(x for x in items if x["title"] == "Revision sheet 1-4")
    moved = web.post(
        f"/p/{token}/courses/{index}/materials/{revision['id']}/move",
        data={"where": "2026-10-05"},
        follow_redirects=False,
    )
    assert moved.status_code == 303
    _upload(web, token, index, BY_KIND)  # the teacher added nothing; the page read again
    items = service.load_subscription(web.root, token).options["materials"][service.course_key("Analysis 3")]
    assert len(items) == 13
    assert next(x for x in items if x["title"] == "Revision sheet 1-4")["week"] == "2026-10-05"
    page = unescape(web.get(f"/p/{token}/courses/{index}/materials").text)
    assert page.index("Revision sheet 1-4") < page.index(
        'id="mw-course"'
    )  # on week 2, above the course's own
    # Removing what the page gave keeps the links added by hand.
    web.post(f"/p/{token}/courses/{index}/links", data={"url": "https://example.com/mine", "title": "Mine"})
    web.post(f"/p/{token}/courses/{index}/materials/source", data={"source": f"moodle:{SITE}#501"})
    left = service.load_subscription(web.root, token).options["materials"][service.course_key("Analysis 3")]
    assert [x["title"] for x in left] == ["Mine"]


def test_a_moodle_address_links_the_course_and_asks_for_the_page(web):
    token, index = _plan(web)
    answer = web.post(
        f"/p/{token}/courses/{index}/materials", data={"address": f"{SITE}/course/view.php?id=501"}
    )
    assert answer.status_code == 200 and "Moodle shows a course only after you sign in" in unescape(
        answer.text
    )
    assert FETCHED == []  # Moodle is never asked: it would want a sign-in
    sub = service.load_subscription(web.root, token)
    assert sub.options["moodle_courses"][service.course_key("Analysis 3")]["id"] == 501


def test_a_teachers_site_and_a_shared_document_are_read_from_their_address(web):
    token, index = _plan(web)
    answer = web.post(f"/p/{token}/courses/{index}/materials", data={"address": "https://example.org/toc/"})
    assert answer.status_code == 200 and "8 items read" in unescape(answer.text)
    doc = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit?usp=sharing"
    answer = web.post(f"/p/{token}/courses/{index}/materials", data={"address": doc})
    assert FETCHED[-1] == "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/export?format=html"
    assert "4 items read" in unescape(answer.text)
    page = unescape(web.get(f"/p/{token}/courses/{index}/materials").text)
    assert page.count('class="mat-row"') == 12 and "example.org" in page and "docs.google.com" in page


def test_a_page_that_is_not_a_course_is_refused_and_nothing_is_kept(web):
    token, index = _plan(web)
    dashboard = (
        "<html><head><title>My courses | Moodle</title>"
        '<script>M.cfg = {"wwwroot":"https:\\/\\/moodle.example.edu"};</script></head><body></body></html>'
    )
    answer = _upload(web, token, index, dashboard)
    assert answer.status_code == 400 and "open the course, then save its page" in unescape(answer.text)
    answer = _upload(web, token, index, "%PDF-1.7 not a page")
    assert answer.status_code == 400
    sub = service.load_subscription(web.root, token)
    assert not (sub.options.get("materials") or {}).get(service.course_key("Analysis 3"))
    stored = "".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in Path(web.root).rglob("*") if p.is_file()
    )
    assert "sesskey" not in stored


def test_materials_read_from_pages_do_not_use_up_the_links_added_by_hand(web):
    token, index = _plan(web)
    _upload(web, token, index, BY_KIND)
    for n in range(service.LINKS_MOST):
        answer = web.post(
            f"/p/{token}/courses/{index}/links",
            data={"url": f"https://example.com/{n}"},
            follow_redirects=False,
        )
        assert answer.status_code == 303, n
    # Today's links: this week's materials and the links added by hand, not every shelf.
    view = service.workspace_view(service.load_subscription(web.root, token), NOW)
    row = next(r for r in view["courses"] if r["name"] == "Analysis 3")
    assert row["links"][0]["title"] == "L3. Sequences" and not any(
        x["title"] == "Syllabus" for x in row["links"]
    )


def test_every_material_sentence_has_its_translations():
    from cps.locales import fr, it

    page = (ROOT / "src" / "cps" / "web" / "templates" / "course_materials.html").read_text(encoding="utf-8")
    said = set(re.findall(r'_\("([^"]+)"', page)) | set(re.findall(r"_\('([^']+)'", page))
    missing = [s for s in said if s not in fr.MESSAGES or s not in it.MESSAGES]
    assert not missing, missing
