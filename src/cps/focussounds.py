"""
The recordings behind the focus page's sounds (DECISIONS.md D210).

D207 made every sound in the browser; the owner, 2026-11-26: "the sounds for focus are not
really what they are meant to be, pick better more earthy vibes, add lo-fi, i want the hum and
chatter from the café, the rain droplets on the window, the crackle of the fire". Each sound but
binaural is now a recording in the public domain (CC0), named here with where it comes from and
the part of it the page loops. `benchmarks/focus_sounds.py` makes the files from this table
(`web/static/sounds/<key>.mp3`) and their notice; `service.focus_sound` tells the page which
sounds are recorded and how long each loop is. Binaural stays made by the browser: two tones a
few hertz apart are what it is.

Rejected: recordings under a licence that asks for credit or forbids changes (CC BY, CC BY-NC,
Pixabay's): CC0 asks nothing, so the files can be cut, mixed and shipped with the app without a
condition to keep. Recordings longer than the loops: a minute or more of an ambience does not
show its seam, and each file stays under about a megabyte, fetched only when its sound is played.
"""

from __future__ import annotations

from dataclasses import dataclass

PAD = 0.25  # seconds of the loop's other end kept on each side of it in the file


@dataclass(frozen=True)
class Source:
    key: str  # the sound's key in `service.FOCUS_SOUNDS`
    title: str  # as its author named it
    author: str
    page: str  # where it is described, with its licence
    url: str  # the file fetched
    licence: str
    start: float  # seconds into the recording where the loop starts
    length: float  # the loop's length in seconds
    fade: float  # the crossfade at the loop's seam; 0 for a piece that ends and starts again
    rest: float = 0.0  # seconds of silence after a piece that ends
    mono: bool = False
    level: float = -24.0  # LUFS: every sound alike, but a fire's crackles, held lower than its peaks ask

    @property
    def loop(self) -> float:
        """The loop's length in the file, its rest included: the page loops from `PAD` to
        `PAD + loop`."""
        return round(self.length + self.rest, 3)


CC0 = "CC0 1.0 Universal (public domain)"
FREESOUND = "https://cdn.freesound.org/previews"

# Each chosen among Freesound's recordings under CC0 for its kind, the most downloaded with the
# steadiest minute (`benchmarks/focus_sounds.py` measures each loop), and its licence read on its
# own page; the Bach is from the Internet Archive, where its pianist released it under CC0.
SOURCES: tuple[Source, ...] = (
    Source(
        "nature",
        "MidwestForestSummerMorningMay2018.WAV",
        "kvgarlic",
        "https://freesound.org/people/kvgarlic/sounds/427769/",
        f"{FREESOUND}/427/427769_1050391-hq.mp3",
        CC0,
        start=26,
        length=70,
        fade=5,
    ),
    Source(
        "rain",
        "RAIN on glass window.wav",
        "nicoproson",
        "https://freesound.org/people/nicoproson/sounds/648529/",
        f"{FREESOUND}/648/648529_457982-hq.mp3",
        CC0,
        start=15,
        length=75,
        fade=5,
    ),
    Source(
        "sea",
        "ocean waves 04",
        "rucisko",
        "https://freesound.org/people/rucisko/sounds/583836/",
        f"{FREESOUND}/583/583836_2978883-hq.mp3",
        CC0,
        start=20,
        length=80,
        fade=6,
    ),
    Source(
        "cafe",
        "Coffeeshop in Vienna, Austria",
        "Alcappuccino",
        "https://freesound.org/people/Alcappuccino/sounds/320462/",
        f"{FREESOUND}/320/320462_4536481-hq.mp3",
        CC0,
        start=5.5,
        length=60,
        fade=5,
    ),
    # Made as a loop: 36 bars at 87 beats a minute are 99.31 seconds, the file's length.
    Source(
        "lofi",
        "Sad & Relaxing Lo-fi Melody Loop (87 BPM)",
        "Jadis0x",
        "https://freesound.org/people/Jadis0x/sounds/840001/",
        f"{FREESOUND}/840/840001_11915007-hq.mp3",
        CC0,
        start=0,
        length=99.31,
        fade=0,
    ),
    Source(
        "jazz",
        "Jazz Chords Gm approximately",
        "nicpressley",
        "https://freesound.org/people/nicpressley/sounds/770969/",
        f"{FREESOUND}/770/770969_16510175-hq.mp3",
        CC0,
        start=1.9,
        length=122.9,
        fade=0,
        rest=2,
    ),
    Source(
        "classical",
        "J. S. Bach, Prelude No. 1 in C major, BWV 846",
        "Kimiko Ishizaka",
        "https://archive.org/details/bach-well-tempered-clavier-book-1",
        "https://archive.org/download/bach-well-tempered-clavier-book-1/"
        "Kimiko%20Ishizaka%20-%20Bach-%20Well-Tempered%20Clavier%2C%20Book%201%20-%2001%20Prelude"
        "%20No.%201%20in%20C%20major%2C%20BWV%20846.mp3",
        CC0,
        start=1.3,
        length=155.5,
        fade=0,
        rest=2.5,
    ),
    Source(
        "fireplace",
        "Wood fire crackling, near flames",
        "felix.blume",
        "https://freesound.org/people/felix.blume/sounds/779877/",
        f"{FREESOUND}/779/779877_1661766-hq.mp3",
        CC0,
        start=40,
        length=70,
        fade=5,
        level=-27,
    ),
)
RECORDED = {s.key: s for s in SOURCES}
