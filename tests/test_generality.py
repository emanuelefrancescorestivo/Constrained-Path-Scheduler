"""
The planner is for any student, not for the timetable it was built on (the owner,
2026-10-08: "don't overfit on my calendar"). Each case below is a synthetic
semester in the shape another system or another country writes: the exams must
be found, each course's lectures tied to its exam, and a plan made. A case that
fails is a shape the reader does not understand yet; AUDIT.md item 41 lists what
was found this way.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar, Event

from cps import cardmaker, service, syllabus
from cps.calendar_io import course_of
from cps.syllabus import read as read_syllabus

START = date(2026, 9, 28)  # a Monday


def _ics(
    lectures: list[str], exams: list[str], tz: str = "Europe/Paris", all_day_exams: bool = False
) -> bytes:
    """Twelve weeks of the given weekly lectures (one a day from Monday), then the
    exams, one a day, in the week after."""
    zone = ZoneInfo(tz)
    cal = Calendar()
    cal.add("prodid", "-//test//generality//EN")
    cal.add("version", "2.0")
    for i, title in enumerate(lectures):
        event = Event()
        first = datetime(2026, 9, 28, 9 + 2 * (i // 5), 0, tzinfo=zone) + timedelta(days=i % 5)
        event.add("summary", title)
        event.add("dtstart", first)
        event.add("dtend", first + timedelta(hours=1, minutes=30))
        event.add("rrule", {"freq": "weekly", "count": 12})
        event.add("uid", f"lecture-{i}@test")
        cal.add_component(event)
    for i, title in enumerate(exams):
        event = Event()
        day = date(2027, 1, 11) + timedelta(days=i)
        event.add("summary", title)
        if all_day_exams:
            event.add("dtstart", day)
            event.add("dtend", day + timedelta(days=1))
        else:
            begin = datetime(day.year, day.month, day.day, 9, 0, tzinfo=zone)
            event.add("dtstart", begin)
            event.add("dtend", begin + timedelta(hours=3))
        event.add("uid", f"exam-{i}@test")
        cal.add_component(event)
    return cal.to_ical()


# name: (lecture titles, exam titles, the courses expected, time zone)
SHAPES = {
    "ADE (France)": (
        ["Algebra 3, Grp: CM ., Salle: Salle 4", "Analysis 3, Grp: TD ., Salle: B12"],
        ["Examen, Algebra 3, Salle: Amphi A", "Examen, Analysis 3"],
        ["Algebra 3", "Analysis 3"],
        "Europe/Paris",
    ),
    "Hyperplanning (France)": (
        ["Mathématiques - TD - Salle B204", "Économie - CM - Amphi 2"],
        ["Examen terminal - Mathématiques - Salle B204", "Partiel - Économie"],
        ["Mathématiques", "Économie"],
        "Europe/Paris",
    ),
    "Celcat (UK)": (
        ["Algebra 3 (Lecture) - Room 101", "Probability (Tutorial) - Room 12"],
        ["Algebra 3 (Exam) - Sports Hall", "Probability (Exam) - Sports Hall"],
        ["Algebra 3", "Probability"],
        "Europe/London",
    ),
    "Outlook, course codes (UK)": (
        ["MA1001 Linear Algebra Lecture", "PH1002 Mechanics Lecture"],
        ["MA1001 Linear Algebra Examination", "PH1002 Mechanics Examination"],
        ["MA1001 Linear Algebra", "PH1002 Mechanics"],
        "Europe/London",
    ),
    "Italian university": (
        ["Analisi Matematica 1 - Lezione - Aula 3", "Fisica 1 - Esercitazione - Aula 5"],
        ["Appello - Analisi Matematica 1", "Esame di Fisica 1"],
        ["Analisi Matematica 1", "Fisica 1"],
        "Europe/Rome",
    ),
    "German university": (
        ["Vorlesung Lineare Algebra", "Übung Analysis I"],
        ["Klausur Lineare Algebra", "Prüfung Analysis I"],
        ["Lineare Algebra", "Analysis I"],
        "Europe/Berlin",
    ),
    "Spanish university": (
        ["Cálculo - Clase - Aula 2", "Álgebra - Clase - Aula 4"],
        ["Examen final de Cálculo", "Examen parcial de Álgebra"],
        ["Cálculo", "Álgebra"],
        "Europe/Madrid",
    ),
    "Dutch university": (
        ["Statistiek - Hoorcollege - Zaal 1", "Calculus - Werkcollege - Zaal 3"],
        ["Tentamen Statistiek", "Tentamen Calculus"],
        ["Statistiek", "Calculus"],
        "Europe/Amsterdam",
    ),
    "Portuguese university": (
        ["Cálculo I - Aula Teórica", "Física - Aula Prática"],
        ["Exame de Cálculo I", "Exame de Física"],
        ["Cálculo I", "Física"],
        "Europe/Lisbon",
    ),
    "US university": (
        ["CS 101 Intro to Programming - Lecture", "MATH 221 Calculus - Discussion"],
        ["CS 101 Intro to Programming - Final Exam", "MATH 221 Calculus - Midterm"],
        ["CS 101 Intro to Programming", "MATH 221 Calculus"],
        "America/New_York",
    ),
    "Japanese university": (
        ["Linear Algebra - Lecture", "Statistics - Lecture"],
        ["Linear Algebra - Final Exam", "Statistics - Final Exam"],
        ["Linear Algebra", "Statistics"],
        "Asia/Tokyo",
    ),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_exams_and_lectures_are_found_in_every_shape(shape):
    lectures, exams, courses, tz = SHAPES[shape]
    report = service.analyse_calendar(_ics(lectures, exams, tz), start=START, tz=tz)
    found = sorted(a.subject for a in report.assessments)
    assert found == sorted(courses), shape
    # Each course's lectures are tied to its exam (topics come from this).
    for title, course in zip(lectures, courses, strict=True):
        assert course_of(title).casefold() == course.casefold(), (shape, title, course_of(title))


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_plan_is_made_in_every_shape_inside_the_student_s_hours(shape):
    lectures, exams, courses, tz = SHAPES[shape]
    now = datetime(2026, 9, 28, 6, 0, tzinfo=ZoneInfo(tz)).astimezone(UTC)
    sub = service.start_subscription(tz=tz, ics=_ics(lectures, exams, tz), now=now)
    assert sorted(s["name"] for s in sub.subjects) == sorted(courses)
    plan = service.PlanReport.from_dict(sub.plan)
    assert plan.sessions, shape
    # Lectures became topics: sessions review weeks of lectures, not a whole course.
    assert any(" · " in s.title for s in plan.sessions if s.kind in ("review", "first review")), shape
    zone = ZoneInfo(tz)
    for s in plan.sessions:
        local = datetime.fromisoformat(s.start).astimezone(zone)
        assert 8 <= local.hour < 22, (shape, s.start)


def test_an_exam_written_as_an_all_day_event_is_found():
    report = service.analyse_calendar(
        _ics(["Algebra - Lecture"], ["Algebra - Exam"], all_day_exams=True), start=START, tz="Europe/Paris"
    )
    assert [a.subject for a in report.assessments] == ["Algebra"]


def test_a_personal_calendar_without_exams_is_planned_from_deadlines():
    """A student whose calendar is their own Google Calendar: no exams in it, only
    life. The plan comes from the deadlines they add."""
    ics = _ics(["Gym", "Dinner with Sam", "Work shift"], [])
    report = service.analyse_calendar(ics, start=START, tz="Europe/Paris")
    assert report.assessments == ()
    sub = service.new_subscription(
        subjects=[],
        start=START,
        tz="Europe/Paris",
        ics=ics,
        tasks=[service.TaskSpec("Essay", "2026-10-16 18:00", 6.0, "History")],
        preferences=service.DEFAULT_PREFERENCES,
        now=datetime(2026, 9, 28, 6, 0, tzinfo=UTC),
    )
    plan = service.PlanReport.from_dict(sub.plan)
    assert [s.title for s in plan.sessions] and all(s.title == "Essay" for s in plan.sessions)


def test_words_that_only_look_like_exams_are_not_exams():
    """A course whose name contains an exam word is not an exam."""
    report = service.analyse_calendar(
        _ics(["Testing and Verification - Lecture", "Final Cut Pro workshop"], []),
        start=START,
        tz="Europe/Paris",
    )
    assert report.assessments == ()


@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_trip_to_campus_follows_each_day_s_classes_in_every_shape(shape):
    """The trip (D42) is read from the timetable's own times: in each country's time
    zone, through its change of clocks, before the day's first class and after its
    last, local wall-clock time."""
    lectures, exams, _courses, tz = SHAPES[shape]
    report = service.analyse_calendar(
        _ics(lectures, exams, tz), start=START, tz=tz, days=70, commute_minutes=30
    )
    zone = ZoneInfo(tz)
    classes: dict[date, list[tuple[datetime, datetime]]] = {}
    trips: dict[date, list[tuple[datetime, datetime]]] = {}
    for e in report.events:
        begin = datetime.fromisoformat(e.start).astimezone(zone)
        end = datetime.fromisoformat(e.end).astimezone(zone)
        (trips if e.source == "commute" else classes).setdefault(begin.date(), []).append((begin, end))
    assert trips and set(trips) == set(classes), shape
    for day, held in classes.items():
        first, last = min(b for b, _ in held), max(e for _, e in held)
        assert sorted(trips[day]) == [
            (first - timedelta(minutes=30), first),
            (last, last + timedelta(minutes=30)),
        ]
        assert (first - timedelta(minutes=30)).hour == 8, (shape, day)  # 08:30 before a 09:00 class


def test_an_all_day_exam_is_not_a_trip():
    report = service.analyse_calendar(
        _ics(["Algebra - Lecture"], ["Algebra - Exam"], all_day_exams=True),
        start=START,
        tz="Europe/Paris",
        commute_minutes=20,
    )
    exam_day = date(2027, 1, 11)
    assert not any(e.source == "commute" and e.start.startswith(exam_day.isoformat()) for e in report.events)


def test_a_course_with_many_hours_a_week_is_reviewed_in_parts():
    """A US course with two lectures and a lab a week (4.5 hours) is two topics a
    week; a course with one lecture stays one (DECISIONS.md D50)."""
    tz = "America/New_York"
    lectures = [
        "CHEM 101 General Chemistry - Lecture",
        "CHEM 101 General Chemistry - Lecture",
        "CHEM 101 General Chemistry - Lab",
        "MATH 221 Calculus - Lecture",
    ]
    exams = ["CHEM 101 General Chemistry - Final Exam", "MATH 221 Calculus - Final Exam"]
    report = service.analyse_calendar(_ics(lectures, exams, tz), start=START, tz=tz)
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    plan = service.make_schedule(report, subjects, weekly_hours=15)
    chem = [s["topic"] for s in plan.specs if s["subject"] == "CHEM 101 General Chemistry"]
    math = [s["topic"] for s in plan.specs if s["subject"] == "MATH 221 Calculus"]
    assert len(chem) == 2 * len(math) == 24
    assert chem[:2] == ["week of 28 Sep (1 of 2)", "week of 28 Sep (2 of 2)"]
    assert math[0] == "week of 28 Sep"


# A course's program pasted in each language's words (D81): the week or the lecture
# each topic names, and its title without the numbering.
SYLLABI = {
    "English": "Week 1: Sets\nWeek 2 - Relations\nLecture 5: Functions",
    "French": "Semaine 1 : Ensembles\nSemaine 2 – Relations\nCours 5 : Fonctions",
    "French, lessons": "Séance 1 : Ensembles\nLeçon 2 : Relations\nCours 5 : Fonctions",
    "Italian": "Settimana 1: Insiemi\nSettimana 2 - Relazioni\nLezione 5: Funzioni",
    "Spanish": "Semana 1: Conjuntos\nSemana 2 - Relaciones\nClase 5: Funciones",
    "Portuguese": "Semana 1: Conjuntos\nSemana 2 - Relações\nAula 5: Funções",
    "German": "Woche 1: Mengen\nWoche 2 - Relationen\nVorlesung 5: Funktionen",
    "Dutch": "Week 1: Verzamelingen\nWeek 2 - Relaties\nHoorcollege 5: Functies",
    "US, abbreviated": "Wk 1: Sets\nWk. 2 - Relations\nLec 5: Functions",
}


@pytest.mark.parametrize("shape", SYLLABI)
def test_a_syllabus_is_read_in_each_language_s_words(shape):
    topics = read_syllabus(SYLLABI[shape])
    marks = [(t.week, t.lecture) for t in topics]
    if shape == "French, lessons":
        assert marks == [(None, 1), (None, 2), (None, 5)]
    else:
        assert marks == [(1, None), (2, None), (None, 5)]
    assert all(t.title[0].isupper() and not t.title[0].isdigit() for t in topics)


# A whole course page in each language's words (D83): the items the ECTS Users' Guide
# recommends for every course unit, under each university's own headings. Topics come
# from the content section only; the assessment's weights and the credits beside it.
COURSE_PAGES = {
    "English": (
        "MATH 2040 Linear Algebra\nCredits: 6 ECTS\nLearning outcomes\nDiagonalise a matrix.\n"
        "Course content\nWeek 1: Vectors\nWeek 2: Linear maps\nAssessment\n"
        "Written exam 70 % and coursework 30 %\nReading\nStrang, Linear Algebra\n",
        [("Written exam", 70), ("Coursework", 30)],
        6.0,
    ),
    "French": (
        "Algèbre linéaire — 6 crédits ECTS\nObjectifs\nDiagonaliser.\nContenu du cours\n"
        "Semaine 1 : Vecteurs\nSemaine 2 : Applications linéaires\n"
        "Modalités d'évaluation : examen en ligne 40 % et examen terminal 60 %\n",
        [("Examen en ligne", 40), ("Examen terminal", 60)],
        6.0,
    ),
    "Italian": (
        "Algebra lineare\nCFU: 9\nObiettivi formativi\nDiagonalizzare.\nProgramma\n"
        "Settimana 1: Vettori\nSettimana 2: Applicazioni lineari\nModalità d'esame\n"
        "esame scritto 60%, orale 40%\n",
        [("Esame scritto", 60), ("Orale", 40)],
        9.0,
    ),
    "Spanish": (
        "Álgebra lineal (6 créditos ECTS)\nObjetivos\nDiagonalizar.\nContenidos\n"
        "Semana 1: Vectores\nSemana 2: Aplicaciones lineales\nSistema de evaluación\n"
        "Examen final (60 %) y prácticas (40 %)\n",
        [("Examen final", 60), ("Prácticas", 40)],
        6.0,
    ),
    "German": (
        "Lineare Algebra, 5 LP\nLernziele\nDiagonalisieren.\nInhalt\nWoche 1: Vektoren\n"
        "Woche 2: Lineare Abbildungen\nPrüfungsform\nKlausur 100 %\n",
        [("Klausur", 100)],
        5.0,
    ),
    "Dutch": (
        "Lineaire algebra (6 EC)\nLeerdoelen\nDiagonaliseren.\nInhoud\nWeek 1: Vectoren\n"
        "Week 2: Lineaire afbeeldingen\nToetsing\nTentamen 80 % en opdrachten 20 %\n",
        [("Tentamen", 80), ("Opdrachten", 20)],
        6.0,
    ),
}


@pytest.mark.parametrize("shape", COURSE_PAGES)
def test_a_whole_course_page_is_read_in_each_language_s_words(shape):
    text, parts, credit = COURSE_PAGES[shape]
    assert {"outcomes", "content", "assessment"} <= set(syllabus.sections(text))
    topics = read_syllabus(syllabus.course_text(text))
    assert [t.week for t in topics] == [1, 2]  # the outcomes and the reading are not topics
    assert [(p.label, p.percent) for p in syllabus.assessment(text)] == parts
    assert syllabus.credits(text) == credit


# A whole semester's program in one document (D90), as other systems write it: the
# courses found by their titles (no contents page in these), each one's schedule by
# date or week, its assessment, its credits under the country's name for them, and its
# hours of classes. A document's own title is not a course's.
PROGRAMS = {
    "French, a semester booklet": (
        "Licence 2 Mathématiques — Livret du semestre 3\nAlgèbre linéaire 2\nResponsable : M. Exemple\n"
        "Objectifs\n- Diagonaliser une matrice.\nContenu\n"
        "lun. 5 oct. Espaces vectoriels, bases et dimension, "
        "sous-espaces et sommes directes\nde sous-espaces. Chapitre 1\n"
        "lun. 12 oct. Applications linéaires\n"
        "lun. 19 oct. Partiel 1. Matrices et déterminants\nVolume horaire : 24 h CM + 24 h TD\n"
        "Crédits ECTS : 6\nModalités d'évaluation : contrôle continu 40 % et examen terminal 60 %\n"
        "Probabilités\nResponsable : Mme Exemple\nObjectifs\n- Calculer une espérance.\nContenu\n"
        "Semaine 1 : Dénombrement\nSemaine 2 : Pas de cours (vacances)\nSemaine 3 : Variables aléatoires\n"
        "Volume horaire : 36 h\nCrédits ECTS : 4\nModalités d'évaluation : partiel (30 %), examen (70 %)\n",
        {
            "Algèbre linéaire 2": (6.0, 48.0, [("Contrôle continu", 40), ("Examen terminal", 60)]),
            "Probabilités": (4.0, 36.0, [("Partiel", 30), ("Examen", 70)]),
        },
        [
            (
                None,
                "10-05",
                "topic",
                "Espaces vectoriels, bases et dimension, sous-espaces et sommes directes de sous-espaces",
            ),
            (None, "10-12", "topic", "Applications linéaires"),
            (None, "10-19", "assessment", "Partiel 1"),
            (None, "10-19", "topic", "Matrices et déterminants"),
        ],
    ),
    "Italian, a degree course's guide": (
        "Corso di laurea in Fisica — Guida al secondo semestre\nAnalisi matematica 2\n"
        "Docente: Prof. Esempio\n"
        "Obiettivi formativi\n- Studiare funzioni di più variabili.\nProgramma\n"
        "lun 01/03/2027 Successioni e serie di funzioni\nlun 08/03/2027 Continuazione\n"
        "mer 10/03/2027 Esonero 1\nlun 15/03/2027 Limiti in più variabili. Cap. 4\n"
        "Ore di lezione: 48\nCFU: 6\n"
        "Modalità d'esame: esame scritto 70%, orale 30%\nFisica generale 2\nDocente: Prof. Altro\n"
        "Obiettivi formativi\n- Elettromagnetismo.\nProgramma\nSettimana 1: Campo elettrico\n"
        "Settimana 2: Potenziale\nOre di lezione: 72\nCFU: 9\nModalità d'esame: scritto 50%, orale 50%\n",
        {
            "Analisi matematica 2": (6.0, 48.0, [("Esame scritto", 70), ("Orale", 30)]),
            "Fisica generale 2": (9.0, 72.0, [("Scritto", 50), ("Orale", 50)]),
        },
        [
            (None, "2027-03-01", "topic", "Successioni e serie di funzioni"),
            (None, "2027-03-10", "assessment", "Esonero 1"),
            (None, "2027-03-15", "topic", "Limiti in più variabili"),
        ],
    ),
    "US, month first and month names": (
        "Fall 2026 Course Booklet\nCalculus II\nInstructor: Dr. Example\nCourse objectives:\n- Integrate.\n"
        "Course schedule:\nTue 9/15/2026 Techniques of integration Stewart, Ch. 7\n"
        "Thu 9/17/2026 Integration by parts\nTue 9/22/2026 Quiz 1. Trigonometric integrals\n"
        "Contact hours: 45\nCredits: 4\nGrading: Homework 30%, Midterm 30%, Final exam 40%\nPhysics I\n"
        "Instructor: Dr. Other\nCourse objectives:\n- Model motion.\nCourse schedule:\nSept 14 Kinematics\n"
        "Sept 21 Newton's laws\nSept 28 Midterm\nContact hours: 60\nCredits: 5\n"
        "Grading: Labs 25%, Midterm 25%, Final 50%\n",
        {
            "Calculus II": (4.0, 45.0, [("Homework", 30), ("Midterm", 30), ("Final exam", 40)]),
            "Physics I": (5.0, 60.0, [("Labs", 25), ("Midterm", 25), ("Final", 50)]),
        },
        [
            (None, "2026-09-15", "topic", "Techniques of integration"),
            (None, "2026-09-17", "topic", "Integration by parts"),
            (None, "2026-09-22", "assessment", "Quiz 1"),
            (None, "2026-09-22", "topic", "Trigonometric integrals"),
        ],
    ),
    "German, a module handbook": (
        "Modulhandbuch Informatik, 3. Semester\nDatenbanken\nDozent: Prof. Beispiel\nLernziele\n"
        "- Datenbanken entwerfen.\nInhalt\nWoche 1: Relationales Modell\nWoche 2: SQL\n"
        "Woche 3: Normalformen\n"
        "Präsenzzeit: 60 Stunden\nLeistungspunkte: 5\nPrüfungsform: Klausur 100 %\nBetriebssysteme\n"
        "Dozent: Prof. Muster\nLernziele\n- Prozesse verstehen.\nInhalt\nWoche 1: Prozesse und Threads\n"
        "Woche 2: Speicherverwaltung\nPräsenzzeit: 45 Stunden\nLeistungspunkte: 5\n"
        "Prüfungsform: Klausur 80 % und Übungen 20 %\n",
        {
            "Datenbanken": (5.0, 60.0, [("Klausur", 100)]),
            "Betriebssysteme": (5.0, 45.0, [("Klausur", 80), ("Übungen", 20)]),
        },
        [
            (1, None, "topic", "Relationales Modell"),
            (2, None, "topic", "SQL"),
            (3, None, "topic", "Normalformen"),
        ],
    ),
}


@pytest.mark.parametrize("shape", PROGRAMS)
def test_a_semester_program_is_cut_into_its_courses_in_each_system(shape):
    text, facts, first = PROGRAMS[shape]
    found = syllabus.courses(text)
    assert [c.name for c in found] == list(facts)
    for course in found:
        credit, hours, parts = facts[course.name]
        assert syllabus.credits(course.text) == credit
        assert syllabus.hours(course.text) == hours
        assert [(p.label, p.percent) for p in syllabus.assessment(course.text)] == parts
    topics = read_syllabus(syllabus.course_text(found[0].text))
    assert [(t.week, t.day, t.kind, t.title) for t in topics] == first
    if shape.startswith("French"):  # a week without class is not a topic
        second = read_syllabus(syllabus.course_text(found[1].text))
        assert [(t.week, t.title) for t in second] == [(1, "Dénombrement"), (3, "Variables aléatoires")]


# Notes in each language's words (D84): a definition sentence gives its term and the
# sentence, a question and its answer a card, and "It is a…" nothing.
NOTES = {
    "English": ("An eigenvalue is a number λ such that Av = λv.\nIt is a scalar.", "Eigenvalue", "Q", "A"),
    "French": (
        "Une valeur propre est un nombre λ tel que Av = λv.\nC'est un scalaire.",
        "Valeur propre",
        "Question",
        "Réponse",
    ),
    "Italian": (
        "Un autovalore è un numero λ tale che Av = λv.\nQuesto è uno scalare.",
        "Autovalore",
        "D",
        "R",
    ),
    "Spanish": (
        "Un autovalor es un número λ tal que Av = λv.\nEsto es un escalar.",
        "Autovalor",
        "Pregunta",
        "Respuesta",
    ),
    "German": (
        "Ein Eigenwert ist eine Zahl λ mit Av = λv.\nDies ist ein Skalar.",
        "Eigenwert",
        "Frage",
        "Antwort",
    ),
    "Dutch": (
        "Een eigenwaarde is een getal λ met Av = λv.\nDit is een scalair.",
        "Eigenwaarde",
        "Vraag",
        "Antwoord",
    ),
    "Portuguese": (
        "Um autovalor é um número λ tal que Av = λv.\nIsso é um escalar.",
        "Autovalor",
        "Pergunta",
        "Resposta",
    ),
}


@pytest.mark.parametrize("shape", NOTES)
def test_notes_become_cards_in_each_language_s_words(shape):
    text, term, q, a = NOTES[shape]
    found = cardmaker.read(f"{text}\n\n{q}: 2 + 2?\n{a}: 4")
    assert [(c.kind, c.front) for c in found] == [("definition", term), ("qa", "2 + 2?")]
    assert found[0].back.endswith("Av = λv") and found[1].back == "4"
