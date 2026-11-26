// D207: sounds to concentrate by, on the focus page, each made by the browser as it plays
// (the Web Audio API): no recording is fetched, nothing needs a licence, and it plays offline.
// The page lists the sounds and the student's last choice (service.focus_sound); this script
// makes them, plays and stops them, and saves the choice and the volume as they change.
// Nature, rain, sea, the fire and the café are noise shaped by filters with random events (a
// bird, a drop, a crackle, a cup); binaural is two tones 10 Hz apart, one in each ear; jazz and
// classical are generated notes: a soft swing on four chords, and an arpeggio on the chords of
// Pachelbel's Canon with its upper line. Nothing here loops visibly on the page (D93).

const box = document.getElementById("focus-sound");
if (box && (window.AudioContext || window.webkitAudioContext)) {
  box.hidden = false;
  const toggle = document.getElementById("sound-toggle");
  const nameEl = document.getElementById("sound-name");
  const volume = document.getElementById("sound-volume");
  const chips = [...box.querySelectorAll("[data-sound-key]")];
  let chosen = box.dataset.sound || "";
  let ctx = null;
  let master = null;
  let playing = null; // { key, stop }
  let noises = null;
  let reverb = null;

  // -- the building blocks ---------------------------------------------------------------
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const hz = (midi) => 440 * 2 ** ((midi - 69) / 12);

  function noiseBuffers() {
    // Four seconds of white, pink and brown noise, the two ears apart, made once.
    const length = ctx.sampleRate * 4;
    const make = (shape) => {
      const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
      for (let ch = 0; ch < 2; ch++) {
        const data = buffer.getChannelData(ch);
        let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, last = 0;
        for (let i = 0; i < length; i++) {
          const white = Math.random() * 2 - 1;
          if (shape === "white") data[i] = white * 0.5;
          else if (shape === "pink") {
            // Paul Kellet's filter: -3 dB an octave.
            b0 = 0.99886 * b0 + white * 0.0555179;
            b1 = 0.99332 * b1 + white * 0.0750759;
            b2 = 0.969 * b2 + white * 0.153852;
            b3 = 0.8665 * b3 + white * 0.3104856;
            b4 = 0.55 * b4 + white * 0.5329522;
            b5 = -0.7616 * b5 - white * 0.016898;
            data[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.11;
            b6 = white * 0.115926;
          } else {
            last = (last + 0.02 * white) / 1.02;
            data[i] = last * 3.5;
          }
        }
      }
      return buffer;
    };
    return { white: make("white"), pink: make("pink"), brown: make("brown") };
  }

  function impulse(seconds) {
    // A room's tail for the instruments: decaying noise, a little different in each ear.
    const length = Math.floor(ctx.sampleRate * seconds);
    const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const data = buffer.getChannelData(ch);
      for (let i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / length) ** 3;
    }
    return buffer;
  }

  function noise(kind, out, gain = 1) {
    const source = ctx.createBufferSource();
    source.buffer = noises[kind];
    source.loop = true;
    source.loopStart = rand(0, 1);
    const level = ctx.createGain();
    level.gain.value = gain;
    source.connect(level).connect(out);
    source.start(0, rand(0, 3));
    return { source, level };
  }

  function filter(type, frequency, q = 0.7) {
    const node = ctx.createBiquadFilter();
    node.type = type;
    node.frequency.value = frequency;
    node.Q.value = q;
    return node;
  }

  function lfo(rate, depth, param) {
    // A slow wave moving a parameter up and down around where it is.
    const osc = ctx.createOscillator();
    osc.frequency.value = rate;
    const amount = ctx.createGain();
    amount.gain.value = depth;
    osc.connect(amount).connect(param);
    osc.start();
    return osc;
  }

  function panned(out, pan) {
    if (!ctx.createStereoPanner) return out;
    const node = ctx.createStereoPanner();
    node.pan.value = pan;
    node.connect(out);
    return node;
  }

  function burst(out, at, { length = 0.01, freq = 3000, q = 1, gain = 0.3, pan = 0 } = {}) {
    // A short noise burst through a band: a raindrop, a crackle, a brush.
    const source = ctx.createBufferSource();
    source.buffer = noises.white;
    const band = filter("bandpass", freq, q);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(gain, at + 0.002);
    env.gain.exponentialRampToValueAtTime(0.0001, at + length);
    source.connect(band).connect(env).connect(panned(out, pan));
    source.start(at, rand(0, 3), length + 0.05);
  }

  function tone(out, at, freq, { length = 1, gain = 0.2, type = "sine", attack = 0.005, harmonics = [], pan = 0 } = {}) {
    // A plucked or struck note: a sine (and its overtones) dying away.
    const env = ctx.createGain();
    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(gain, at + attack);
    env.gain.exponentialRampToValueAtTime(0.0001, at + length);
    env.connect(panned(out, pan));
    const parts = [[1, 1], ...harmonics];
    for (const [ratio, level] of parts) {
      const osc = ctx.createOscillator();
      osc.type = type;
      osc.frequency.value = freq * ratio;
      const g = ctx.createGain();
      g.gain.value = level;
      osc.connect(g).connect(env);
      osc.start(at);
      osc.stop(at + length + 0.05);
    }
  }

  function chirp(out, at, from, to, length, gain, pan) {
    // One note of a bird: a sine sweeping from one pitch to another.
    const osc = ctx.createOscillator();
    osc.frequency.setValueAtTime(from, at);
    osc.frequency.exponentialRampToValueAtTime(to, at + length);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(gain, at + length * 0.2);
    env.gain.exponentialRampToValueAtTime(0.0001, at + length);
    osc.connect(env).connect(panned(out, pan));
    osc.start(at);
    osc.stop(at + length + 0.02);
  }

  // A scheduler a little ahead of time, as the Web Audio API asks for steady rhythms.
  function every(ms, step) {
    let next = ctx.currentTime + 0.1;
    const id = setInterval(() => {
      while (next < ctx.currentTime + 1.2) next = step(next);  // ahead of a throttled timer
    }, ms);
    return () => clearInterval(id);
  }

  // -- the eight sounds ----------------------------------------------------------------------
  const SOUNDS = {
    rain(out) {
      const hiss = filter("highpass", 500);
      const air = filter("lowpass", 7000);
      hiss.connect(air).connect(out);
      const bed = noise("pink", hiss, 0.9);
      const wash = filter("bandpass", 1200, 0.4);
      wash.connect(out);
      const low = noise("brown", wash, 0.6);
      const swell = lfo(0.07, 0.25, low.level.gain);
      const stop = every(60, (at) => {
        burst(out, at, { length: rand(0.01, 0.03), freq: rand(2500, 7000), q: 2, gain: rand(0.05, 0.18), pan: rand(-0.9, 0.9) });
        return at + rand(0.02, 0.12);
      });
      return () => { stop(); swell.stop(); bed.source.stop(); low.source.stop(); };
    },

    sea(out) {
      const body = filter("lowpass", 700, 0.5);
      body.connect(out);
      const waves = noise("brown", body, 1.1);
      const foamBand = filter("highpass", 1800);
      foamBand.connect(out);
      const foam = noise("pink", foamBand, 0.25);
      // Two slow waves together, so the breakers never repeat exactly.
      const a = lfo(0.09, 450, body.frequency);
      const b = lfo(0.13, 0.45, waves.level.gain);
      const c = lfo(0.09, 0.22, foam.level.gain);
      return () => { [a, b, c].forEach((o) => o.stop()); waves.source.stop(); foam.source.stop(); };
    },

    fireplace(out) {
      const roarBand = filter("lowpass", 420);
      roarBand.connect(out);
      const roar = noise("brown", roarBand, 1.2);
      const flicker = lfo(0.3, 0.3, roar.level.gain);
      const stop = every(60, (at) => {
        const pop = Math.random() < 0.08;
        burst(out, at, { length: pop ? 0.05 : rand(0.003, 0.012), freq: pop ? 900 : rand(1500, 5000), q: 1.5, gain: pop ? 0.5 : rand(0.08, 0.35), pan: rand(-0.4, 0.4) });
        return at + rand(0.03, 0.4);
      });
      return () => { stop(); flicker.stop(); roar.source.stop(); };
    },

    cafe(out) {
      // A room of voices: bands of noise that rise and fall at the pace of syllables.
      const room = ctx.createGain();
      room.gain.value = 0.9;
      room.connect(out);
      const stops = [];
      for (const [freq, rate] of [[350, 3.1], [600, 4.3], [900, 2.6], [1300, 3.7]]) {
        const band = filter("bandpass", freq, 1.2);
        band.connect(room);
        const voice = noise("pink", band, 0.5);
        stops.push(lfo(rate, 0.35, voice.level.gain), lfo(0.05, 0.2, voice.level.gain), voice.source);
      }
      const humBand = filter("lowpass", 250);
      humBand.connect(out);
      stops.push(noise("brown", humBand, 0.5).source);
      // Now and then a cup on a saucer: a bright note with inharmonic overtones.
      const stop = every(250, (at) => {
        tone(reverb.input, at, rand(2200, 3400), { length: 0.6, gain: rand(0.015, 0.04), harmonics: [[2.76, 0.4], [5.4, 0.2]], pan: rand(-0.8, 0.8) });
        return at + rand(2.5, 9);
      });
      return () => { stop(); stops.forEach((n) => n.stop()); };
    },

    nature(out) {
      const windBand = filter("bandpass", 700, 0.35);
      windBand.connect(out);
      const wind = noise("pink", windBand, 0.7);
      const gust = lfo(0.06, 0.35, wind.level.gain);
      const sweep = lfo(0.04, 250, windBand.frequency);
      const leavesBand = filter("highpass", 3500);
      leavesBand.connect(out);
      const leaves = noise("white", leavesBand, 0.03);
      const rustle = lfo(0.2, 0.02, leaves.level.gain);
      // Birds: a phrase of two to six chirps, far or near, every few seconds.
      const stop = every(250, (at) => {
        const base = rand(2200, 4200);
        const pan = rand(-0.9, 0.9);
        const near = rand(0.02, 0.07);
        let t = at;
        for (let i = 0, n = Math.floor(rand(2, 7)); i < n; i++) {
          const up = Math.random() < 0.6;
          chirp(out, t, base * (up ? 0.85 : 1.25), base * (up ? 1.25 : 0.85), rand(0.05, 0.14), near, pan);
          t += rand(0.08, 0.2);
        }
        return at + rand(1.5, 6);
      });
      return () => { stop(); [gust, sweep, rustle].forEach((o) => o.stop()); wind.source.stop(); leaves.source.stop(); };
    },

    binaural(out) {
      // 200 Hz in the left ear and 210 Hz in the right: the beat of 10 Hz is heard only with
      // headphones. A quiet bed of pink noise under it.
      const merger = ctx.createChannelMerger(2);
      merger.connect(out);
      const oscs = [200, 210].map((f, ear) => {
        const osc = ctx.createOscillator();
        osc.frequency.value = f;
        const g = ctx.createGain();
        g.gain.value = 0.18;
        osc.connect(g).connect(merger, 0, ear);
        osc.start();
        return osc;
      });
      const bedBand = filter("lowpass", 1200);
      bedBand.connect(out);
      const bed = noise("pink", bedBand, 0.15);
      return () => { oscs.forEach((o) => o.stop()); bed.source.stop(); };
    },

    jazz(out) {
      // A slow swing at 84 beats a minute: Dm9, G13, Cmaj9, A7(b9), a soft piano's chords on the
      // first beat and the "and" of two, a walking bass, brushes on two and four, a ride.
      const beat = 60 / 84;
      const swing = beat * 0.66;
      const chords = [
        { bass: 38, notes: [53, 57, 60, 64] }, // Dm9: F A C E over D
        { bass: 43, notes: [53, 57, 59, 64] }, // G13: F A B E over G
        { bass: 36, notes: [52, 55, 59, 62] }, // Cmaj9: E G B D over C
        { bass: 45, notes: [55, 58, 61, 64] }, // A7(b9): G Bb C# E over A
      ];
      const keys = ctx.createGain();
      keys.gain.value = 0.9;
      keys.connect(out);
      keys.connect(reverb.input);
      let bar = 0;
      let step = 0;
      let bassNote = chords[0].bass;
      const stop = every(50, (at) => {
        const chord = chords[bar % chords.length];
        const next = chords[(bar + 1) % chords.length];
        const beatIn = step % 4;
        const t = at;
        // Bass: the root on one, then chord tones, and a step towards the next root on four.
        if (beatIn === 0) bassNote = chord.bass;
        else if (beatIn === 3) bassNote = next.bass + pick([-1, 1, 2]);
        else bassNote = chord.bass + pick([3, 4, 7, 10, 12]);
        tone(out, t, hz(bassNote), { length: beat * 0.95, gain: 0.22, type: "triangle", attack: 0.01 });
        // Piano: on one, and a short push on the "and" of two.
        if (beatIn === 0 || (beatIn === 1 && Math.random() < 0.7)) {
          const when = beatIn === 1 ? t + swing : t;
          for (const n of chord.notes) tone(keys, when + rand(0, 0.015), hz(n), { length: 1.8, gain: 0.045, harmonics: [[2, 0.25], [3, 0.08]] });
        }
        // Now and then a note of melody, from the chord, an octave up.
        if (Math.random() < 0.35) tone(keys, t + (Math.random() < 0.5 ? 0 : swing), hz(pick(chord.notes) + 12), { length: 1.2, gain: 0.04, harmonics: [[2, 0.2], [4, 0.05]], pan: 0.2 });
        // Brushes on two and four, the ride on every beat and its swung "and".
        if (beatIn === 1 || beatIn === 3) burst(out, t, { length: 0.18, freq: 4000, q: 0.5, gain: 0.05, pan: -0.2 });
        burst(out, t, { length: 0.25, freq: 9000, q: 3, gain: 0.03, pan: 0.3 });
        if (Math.random() < 0.6) burst(out, t + swing, { length: 0.15, freq: 9000, q: 3, gain: 0.02, pan: 0.3 });
        step += 1;
        if (step % 4 === 0) bar += 1;
        return at + beat;
      });
      return () => stop();
    },

    classical(out) {
      // Pachelbel's Canon in D (public domain): its eight chords, each arpeggiated by a piano
      // in sixteenths at 60 beats a minute, the upper line F#, E, D, C#, B, A, B, C# above.
      const sixteenth = 60 / 60 / 4;
      const chords = [
        [50, 57, 62, 66], // D
        [45, 52, 57, 61], // A
        [47, 54, 59, 62], // Bm
        [42, 49, 54, 57], // F#m
        [43, 50, 55, 59], // G
        [38, 45, 50, 54], // D
        [43, 50, 55, 59], // G
        [45, 52, 57, 61], // A
      ];
      const line = [78, 76, 74, 73, 71, 69, 71, 73];
      const pattern = [0, 1, 2, 3, 2, 1, 2, 3];
      const piano = ctx.createGain();
      piano.gain.value = 0.9;
      piano.connect(out);
      piano.connect(reverb.input);
      let step = 0;
      const stop = every(50, (at) => {
        const chord = Math.floor(step / 8) % chords.length;
        const notes = chords[chord];
        const i = step % 8;
        tone(piano, at, hz(notes[pattern[i]] + (i > 3 ? 12 : 0)), { length: 1.6, gain: 0.05, harmonics: [[2, 0.3], [3, 0.1], [4, 0.04]] });
        if (i === 0) {
          tone(piano, at, hz(notes[0] - 12), { length: 2.6, gain: 0.06, harmonics: [[2, 0.3]] });
          tone(piano, at, hz(line[chord]), { length: 2.2, gain: 0.05, harmonics: [[2, 0.2], [3, 0.05]], pan: 0.15 });
        }
        step += 1;
        return at + sixteenth;
      });
      return () => stop();
    },
  };

  // Each sound brought to about the same loudness, measured at its output in Chromium (D207).
  const TRIM = { nature: 1.2, rain: 0.9, binaural: 0.9, sea: 0.5, cafe: 1, jazz: 3.2, classical: 3.8, fireplace: 0.6 };

  // -- playing, stopping, saving --------------------------------------------------------------
  function ready() {
    if (ctx) return ctx.resume();
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = level();
    // A limiter at the end, so no sound at full volume ever clips.
    const limiter = ctx.createDynamicsCompressor();
    limiter.threshold.value = -6;
    limiter.ratio.value = 12;
    master.connect(limiter).connect(ctx.destination);
    noises = noiseBuffers();
    const convolver = ctx.createConvolver();
    convolver.buffer = impulse(2.2);
    const wet = ctx.createGain();
    wet.gain.value = 0.35;
    convolver.connect(wet).connect(master);
    reverb = { input: convolver };
    return ctx.resume();
  }

  const level = () => (Number(volume?.value ?? 50) / 100) ** 2 * 1.6; // the ear hears loudness as a square

  function stopSound(fade = 0.8) {
    if (!playing) return;
    const { gain, stop } = playing;
    gain.gain.cancelScheduledValues(ctx.currentTime);
    gain.gain.setValueAtTime(gain.gain.value, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0, ctx.currentTime + fade);
    setTimeout(() => { stop(); gain.disconnect(); }, fade * 1000 + 100);
    playing = null;
  }

  async function play(key) {
    await ready();
    stopSound();
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(TRIM[key] || 1, ctx.currentTime + 1.5);
    gain.connect(master);
    playing = { key, gain, stop: SOUNDS[key](gain) };
  }

  function show() {
    const on = Boolean(playing);
    box.classList.toggle("is-playing", on);
    toggle.hidden = !chosen;
    toggle.setAttribute("aria-pressed", String(on));
    for (const chip of chips) {
      chip.classList.toggle("is-chosen", chip.dataset.soundKey === chosen);
      chip.setAttribute("aria-pressed", String(on && chip.dataset.soundKey === chosen));
    }
    const chip = chips.find((c) => c.dataset.soundKey === chosen);
    if (chip) nameEl.textContent = chip.dataset.name;
  }

  let saving = null;
  function save() {
    clearTimeout(saving);
    saving = setTimeout(() => {
      const body = new FormData();
      body.set("key", chosen);
      body.set("volume", volume ? volume.value : "50");
      fetch(box.dataset.save, { method: "POST", body, credentials: "same-origin" }).catch(() => {});
    }, 400);
  }

  for (const chip of chips) {
    chip.addEventListener("click", async () => {
      const key = chip.dataset.soundKey;
      if (playing && playing.key === key) stopSound();
      else {
        chosen = key;
        await play(key);
        save();
      }
      show();
    });
  }
  toggle.addEventListener("click", async () => {
    if (playing) stopSound();
    else if (chosen) await play(chosen);
    show();
  });
  volume?.addEventListener("input", () => {
    if (master) master.gain.setTargetAtTime(level(), ctx.currentTime, 0.05);
  });
  volume?.addEventListener("change", save);
  show();
}
