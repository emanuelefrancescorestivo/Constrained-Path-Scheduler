"""
How an exam went (DECISIONS.md D56, Phase D of the engine review): Today asks for
three weeks after each exam; the answer is kept with the plan beside the recall the
model predicted for that exam, and the pilot's measures show it only when the
student agreed (`cps metrics`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import cli, service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SEMESTER = EXAMPLES / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
# The sample semester's first two exams: Computer Programming 3 on 25 January and
# Algebra 3 on 26 January 2027.
AFTER = datetime(2027, 1, 27, 7, 0, tzinfo=UTC)  # 08:00 in Paris, before the third exam at 09:00


@pytest.fixture(scope="module")
def plan_then():
    """A plan set up at the start of the semester, refreshed the day after its
    second exam."""
    first = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    return service.refresh_subscription(first, now=AFTER, reread=False)


def test_today_asks_about_each_exam_for_three_weeks(plan_then):
    assert service.exam_questions(plan_then, MORNING) == []
    asked = [q["course"] for q in service.exam_questions(plan_then, AFTER)]
    assert asked == ["Computer Programming 3", "Algebra 3"]
    # Three weeks on, those two are no longer asked about; the later exams still are.
    later = [
        q["course"] for q in service.exam_questions(plan_then, AFTER + timedelta(days=service.EXAM_ASK_DAYS))
    ]
    assert "Algebra 3" not in later and "Computer Programming 3" not in later and later


def test_an_answer_is_kept_with_the_prediction_and_shared_only_with_consent(plan_then):
    answered = service.answer_exam(plan_then, "Algebra 3", {"result": "well", "mark": " 28 / 30 "}, AFTER)
    record = answered.options["exam_results"]["Algebra 3"]
    plan = service.PlanReport.from_dict(plan_then.plan)
    assert record["result"] == "well" and record["mark"] == "28 / 30" and record["consent"] is False
    assert record["predicted"] == pytest.approx(service.recall_predicted(plan, "Algebra 3"), abs=1e-4)
    assert 0 < record["predicted"] < 1
    assert [q["course"] for q in service.exam_questions(answered, AFTER)] == ["Computer Programming 3"]
    skipped = service.answer_exam(answered, "Computer Programming 3", {"skip": "1"}, AFTER)
    assert skipped.options["exam_results"]["Computer Programming 3"]["skipped"] is True
    assert service.exam_questions(skipped, AFTER) == []
    for form, words in (
        ({"result": "excellent"}, "choose how it went"),
        ({"result": "passed", "mark": "x" * 21}, "at most 20 characters"),
    ):
        with pytest.raises(service.InvalidInput, match=words):
            service.answer_exam(plan_then, "Algebra 3", form, AFTER)
    with pytest.raises(service.InvalidInput, match="not waiting for an answer"):
        service.answer_exam(answered, "Algebra 3", {"result": "passed"}, AFTER)
    with pytest.raises(service.InvalidInput, match="not waiting for an answer"):
        service.answer_exam(plan_then, "Deep Learning 1", {"result": "passed"}, AFTER)


def test_the_measures_show_shared_answers_without_course_or_plan(tmp_path, plan_then, capsys):
    shared = service.answer_exam(
        plan_then, "Algebra 3", {"result": "just", "mark": "18/30", "consent": "1"}, AFTER
    )
    service.save_subscription(tmp_path, shared)
    kept = service.answer_exam(
        service.start_subscription(
            tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=AFTER - timedelta(days=200)
        ),
        "Algebra 3",
        {"result": "failed"},
        AFTER,
    )
    service.save_subscription(tmp_path, kept)
    exams = service.calibration(tmp_path)["exams"]
    predicted = shared.options["exam_results"]["Algebra 3"]["predicted"]
    assert exams == {
        "shared": [{"predicted": predicted, "result": "just", "mark": "18/30"}],
        "private": 1,
        "skipped": 0,
    }
    assert cli.main(["metrics", "--db", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "after the exam         1 answers shared, 1 kept private, 0 not given" in printed
    assert f"predicted {predicted:.0%}: just (18/30)" in printed
    assert "Algebra" not in printed and shared.token not in printed


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


@pytest.mark.parametrize(
    ("lang", "question", "consent", "thanks"),
    [
        ("en", "How did the Algebra 3 exam go?", "The owner of this app may read this answer", "Thank you."),
        ("fr", "Comment s'est passé l'examen de Algebra 3 ?", "Le responsable de cette appli", "Merci."),
        ("it", "Com'è andato l'esame di Algebra 3?", "Il gestore di questa app", "Grazie."),
    ],
)
def test_today_asks_and_the_privacy_page_says_so(tmp_path, lang, question, consent, thanks):
    now = {"at": MORNING}
    config = Config(store=tmp_path, clock=lambda: now["at"], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        token = web.post(
            "/start", data={"tz": "Europe/Paris", "lang": lang}, files=files, follow_redirects=False
        )
        token = token.headers["location"].split("/")[2]
        assert question not in unescape(web.get(f"/p/{token}").text)
        now["at"] = AFTER
        today = unescape(web.get(f"/p/{token}").text)
        assert question in today and consent in today
        assert 'name="consent" value="1">' in today  # not ticked for the student
        answer = web.post(
            f"/p/{token}/exam",
            data={"course": "Algebra 3", "result": "passed", "mark": "", "consent": "1"},
            follow_redirects=False,
        )
        assert answer.status_code == 303 and answer.headers["location"] == f"/p/{token}?saved=exam"
        after = unescape(web.get(answer.headers["location"]).text)
        assert thanks in after and question not in after
        stored = service.load_subscription(tmp_path, token)
        assert stored is not None and stored.options["exam_results"]["Algebra 3"]["consent"] is True
        refused = web.post(f"/p/{token}/exam", data={"course": "Algebra 3", "result": "passed"})
        assert refused.status_code == 400
        assert web.get(f"/p/{token}/export").json()["plan"]["options"]["exam_results"]["Algebra 3"]
        privacy = unescape(web.get(f"/privacy?lang={lang}").text)
        assert {"en": "After each exam", "fr": "Après chaque examen", "it": "Dopo ogni esame"}[
            lang
        ] in privacy
