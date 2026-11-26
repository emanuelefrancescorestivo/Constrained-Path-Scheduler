"""
The focus page's recorded sounds (DECISIONS.md D210), made again from their sources.

The owner, 2026-11-26, after D207's sounds made by the browser: "the sounds for focus are not
really what they are meant to be, pick better more earthy vibes, add lo-fi, i want the hum and
chatter from the café, the rain droplets on the window, the crackle of the fire". Each sound is
now a recording in the public domain (CC0), listed in `SOURCES` with where it comes from; this
script cuts each one into a loop that never jumps, brings them to one loudness and writes the
MP3 files the focus page plays (`src/cps/web/static/sounds/`) and their notice.

A loop is `length` seconds of the recording from `start`, its first `fade` seconds mixed with
the `fade` seconds that follow the loop's end (an equal-power crossfade), so its last sample
leads into its first. A piece of music that ends ends: it gets a moment of silence and starts
again (`fade` 0). The file then carries a quarter of a second of the loop's end before it and of
its start after it (`PAD`), so that a decoder that adds or drops a few milliseconds at the start
still loops between `PAD` and `PAD + length` on the same samples: the page loops there
(`sound.js`, `RECORDED`).

Needs ffmpeg with libmp3lame, NumPy, and the network (Freesound's previews, the Internet
Archive). Run from the repository's root:

    python benchmarks/focus_sounds.py

Prints each sound's loop, loudness before and after, peak and file size.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cps.focussounds import PAD, SOURCES, Source

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "cps" / "web" / "static" / "sounds"
CACHE = ROOT / ".cache" / "focus_sounds"  # the downloads, kept out of the repository
RATE = 44_100
PEAK = -2.0  # dBFS: at most this, whatever the loudness asks
# LAME's variable bitrate: about 115 kbit/s for an ambience, whose noise needs the bits, and
# about 100 for a piece of music, longer and kinder to the encoder.
QUALITY = {"ambience": "6", "music": "7"}


def fetch(source: Source) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{source.key}{Path(source.url).suffix or '.mp3'}"
    if not path.exists():
        request = urllib.request.Request(source.url, headers={"User-Agent": "cps-focus-sounds/1"})
        with urllib.request.urlopen(request, timeout=120) as answer:
            path.write_bytes(answer.read())
    return path


def decode(path: Path) -> np.ndarray:
    """The recording as stereo float samples at `RATE`."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2).astype(np.float64)


def loudness(samples: np.ndarray) -> tuple[float, float]:
    """Integrated loudness (LUFS) and sample peak (dBFS), by ffmpeg's EBU R 128 meter."""
    found = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-f",
            "f32le",
            "-ar",
            str(RATE),
            "-ac",
            "2",
            "-i",
            "-",
            "-af",
            "ebur128=peak=sample",
            "-f",
            "null",
            "-",
        ],
        input=samples.astype("<f4").tobytes(),
        capture_output=True,
        check=True,
    ).stderr.decode()
    summary = found[found.rindex("Summary:") :]
    lufs = float(summary.split("I:")[1].split("LUFS")[0])
    peak = float(summary.split("Peak:")[1].split("dBFS")[0])
    return lufs, peak


def looped(samples: np.ndarray, source: Source) -> np.ndarray:
    """The loop: `length` seconds from `start`, its seam crossfaded, or a piece and its rest."""
    a = int(source.start * RATE)
    n = round(source.length * RATE)
    if source.fade == 0:
        piece = samples[a : a + n]
        return np.concatenate([piece, np.zeros((int(source.rest * RATE), 2))])
    x = int(source.fade * RATE)
    seg = samples[a : a + n + x]
    if len(seg) < n + x:
        raise SystemExit(f"{source.key}: the recording is shorter than start + length + fade")
    t = np.linspace(0, np.pi / 2, x)[:, None]
    out = seg[:n].copy()
    out[:x] = seg[:x] * np.sin(t) + seg[n : n + x] * np.cos(
        t
    )  # equal power: uncorrelated noise keeps its level
    return out


def limited(loop: np.ndarray) -> np.ndarray:
    """The loop with its loudest moments (a crackle, a cup) held under `PEAK` by ffmpeg's
    look-ahead limiter, run over two turns of the loop and the second kept, so the limiter
    comes to the seam already listening and the loop still joins."""
    twice = np.concatenate([loop, loop])
    limit = 10 ** (PEAK / 20)
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(RATE),
            "-ac",
            "2",
            "-i",
            "-",
            "-af",
            f"alimiter=limit={limit:.4f}:attack=2:release=40:level=0:latency=1",
            "-f",
            "f32le",
            "-",
        ],
        input=twice.astype("<f4").tobytes(),
        capture_output=True,
        check=True,
    ).stdout
    out = np.frombuffer(raw, dtype="<f4").reshape(-1, 2).astype(np.float64)
    return out[len(loop) : 2 * len(loop)]


def write(source: Source) -> dict:
    samples = decode(fetch(source))
    if source.mono:
        samples = np.repeat(samples.mean(axis=1, keepdims=True), 2, axis=1)
    loop = looped(samples, source)
    before, crest = loudness(loop)
    crest -= before  # how far its loudest sample stands over its average, in dB
    loop = loop * 10 ** ((source.level - before) / 20)
    if 20 * np.log10(np.abs(loop).max() + 1e-12) > PEAK:
        loop = limited(loop)
    p = int(PAD * RATE)
    padded = np.concatenate([loop[-p:], loop, loop[:p]])
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{source.key}.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(RATE),
            "-ac",
            "2",
            "-i",
            "-",
            *(["-ac", "1"] if source.mono else []),
            "-c:a",
            "libmp3lame",
            "-q:a",
            QUALITY["ambience" if source.fade else "music"],
            "-map_metadata",
            "-1",
            "-id3v2_version",
            "0",
            "-write_xing",
            "1",
            str(target),
        ],
        input=padded.astype("<f4").tobytes(),
        check=True,
    )
    after, top = loudness(loop)
    # Decoded again, the file is its loop and the two margins, to the sample: the encoder's
    # delay is written in its header (`-write_xing`) and taken off.
    decoded = len(decode(target)) / RATE
    return {
        "key": source.key,
        "loop": round(len(loop) / RATE, 3),
        "decoded": round(decoded - len(padded) / RATE, 4),
        "crest": crest,
        "before": before,
        "after": after,
        "peak": top,
        "bytes": target.stat().st_size,
    }


def notice() -> str:
    lines = [
        "# The focus page's sounds",
        "",
        "Recordings in the public domain, cut into loops and brought to one loudness by",
        "`benchmarks/focus_sounds.py` (DECISIONS.md D210). Nothing else of them is changed.",
        "",
    ]
    for s in SOURCES:
        lines += [
            f"## {s.key}.mp3",
            "",
            f"- {s.title}, by {s.author}",
            f"- {s.page}",
            f"- Licence: {s.licence}",
            f"- From {s.start:g} s, {s.length:g} s long"
            + (f", the seam crossfaded over {s.fade:g} s" if s.fade else f", then {s.rest:g} s of silence"),
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    rows = [write(s) for s in SOURCES]
    (OUT / "NOTICE.md").write_text(notice(), encoding="utf-8")
    for r in rows:
        print(
            f"{r['key']:10} loop {r['loop']:7.3f} s (decoded {r['decoded']:+.4f} s)  "
            f"crest {r['crest']:4.1f} dB  {r['before']:6.1f} → {r['after']:6.1f} LUFS  "
            f"peak {r['peak']:5.1f} dBFS  {r['bytes'] / 1024:6.0f} KB"
        )
    print(json.dumps({r["key"]: r["loop"] for r in rows}))
    print(f"total {sum(r['bytes'] for r in rows) / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
