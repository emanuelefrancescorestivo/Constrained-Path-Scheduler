"""
A set of notes as PDFs (DECISIONS.md D95): beside or instead of the photos and the
text. What must hold: a PDF is kept without what names its author and without what
runs when it opens; it is seen only by those its post is for, and saved rather than
shown inside a page; its text makes flashcards; it goes with its post and its plan.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pypdf")
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from test_program import pdf

from cps import pdftext, service, social
from cps.store import Store
from cps.web import Config, create_app

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
NOTES = [["Mitochondria: the powerhouse of the cell", "Osmosis: water through a membrane"], ["Second page"]]


def _signed_pdf(pages=NOTES, author="Ana Rossi") -> bytes:
    """A PDF as an app writes one: its author's name in its metadata, and a script
    that runs when it opens."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf(pages))))
    writer.add_metadata({"/Author": author, "/Creator": "GoodNotes"})
    writer._root_object[NameObject("/OpenAction")] = DictionaryObject(
        {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("app.alert(1)")}
    )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        client.store = Store(tmp_path)
        yield client


def _start(client) -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    return (
        client.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )


def _share(client, token, *files, visibility="me", body=""):
    data = {"course": "Biology", "title": "Cells", "visibility": visibility, "own_work": "1", "body": body}
    return client.post(
        f"/p/{token}/notes/new",
        data=data,
        files=[("photos", (name, content, "application/octet-stream")) for name, content in files],
        follow_redirects=False,
    )


def test_a_pdf_is_kept_without_its_author_and_without_what_runs(tmp_path):
    dirty = _signed_pdf()
    assert b"Ana Rossi" in dirty and b"/JavaScript" in dirty
    kept = pdftext.clean(dirty)
    assert kept.pages == 2 and "Mitochondria" in kept.text
    for trace in (b"Ana Rossi", b"GoodNotes", b"/JavaScript", b"/OpenAction"):
        assert trace not in kept.data, trace
    again = PdfReader(io.BytesIO(kept.data))
    assert len(again.pages) == 2 and again.metadata is None
    assert "Osmosis" in again.pages[0].extract_text()


@pytest.mark.parametrize(
    ("data", "refusal"),
    [
        (b"%PDF-1.4\n" + b"0" * (pdftext.MAX_BYTES + 1), "at most 10 MB"),
        (b"%PDF-1.4\n1 0 obj << broken", "could not be read"),
        (b"GIF89a, not a PDF at all", "neither a photo nor a PDF"),
    ],
    ids=["large", "unreadable", "not-pdf"],
)
def test_a_pdf_that_cannot_be_kept_is_refused_with_a_reason(data, refusal):
    with pytest.raises(social.SocialError, match=refusal):
        social.clean_pdf(data)


def test_a_scan_with_no_text_is_kept_all_the_same():
    kept = pdftext.clean(pdf([[]]))
    assert kept.pages == 1 and kept.text.strip() == ""


def test_notes_as_a_pdf_are_shared_downloaded_and_make_flashcards(web):
    token = _start(web)
    answer = _share(web, token, ("Cells, week 3.pdf", _signed_pdf()))
    assert answer.status_code == 303, answer.text
    post_id = answer.headers["location"].split("/")[4].split("?")[0]
    page = web.get(answer.headers["location"]).text
    link = re.search(r'href="(/p/[^"]+\.pdf)" download', page).group(1)
    assert "Cells, week 3" in page and "2 pages" in page and "Make flashcards from these notes" in page
    saved = web.get(link)
    assert saved.headers["content-type"] == "application/pdf"
    # Saved, then opened by the device's viewer: never shown inside a page of the site.
    assert saved.headers["content-disposition"] == 'attachment; filename="Cells_ week 3.pdf"'
    assert b"Ana Rossi" not in saved.content
    cards = web.get(f"/p/{token}/cards/make?note={post_id}").text
    assert "Mitochondria" in cards and "powerhouse of the cell" in cards


def test_a_pdf_is_seen_only_by_those_its_post_is_for(web):
    token = _start(web)
    answer = _share(web, token, ("cells.pdf", pdf(NOTES)))
    name = re.search(r"/m/([A-Za-z0-9_-]{12}\.pdf)", web.get(answer.headers["location"]).text).group(1)
    other = _start(web)
    assert web.get(f"/m/{name}").status_code == 404
    assert web.get(f"/p/{other}/m/{name}").status_code == 404
    assert web.get(f"/p/{token}/m/{name}").status_code == 200


def test_a_set_takes_at_most_three_pdfs_and_photos_beside_them(web):
    token = _start(web)
    four = [(f"part {n}.pdf", pdf(NOTES)) for n in range(4)]
    assert "at most 3 PDFs" in _share(web, token, *four).text
    assert web.store.count() == 1 and service.social.posts_of(web.store, token) == []


def test_a_pdf_goes_with_its_post_and_with_its_plan(web):
    token = _start(web)
    first = _share(web, token, ("a.pdf", pdf(NOTES)))
    _share(web, token, ("b.pdf", pdf(NOTES)))
    files = sorted(p.name for p in web.store.photos.iterdir())
    assert len(files) == 2 and all(f.endswith(".pdf") for f in files)
    copy = web.get(f"/p/{token}/export").json()
    assert set(copy["photos"]) == set(files)  # the plan's copy has them (D43)
    post_id = first.headers["location"].split("/")[4].split("?")[0]
    web.post(f"/p/{token}/posts/{post_id}/delete")
    assert len(list(web.store.photos.iterdir())) == 1
    web.post(f"/p/{token}/delete")
    assert list(web.store.photos.iterdir()) == []
