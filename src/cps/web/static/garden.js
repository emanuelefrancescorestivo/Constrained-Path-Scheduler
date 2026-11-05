// How it works (D117, D119): the garden in WebGL. Each `.gd-scene[data-scene]` carries the
// server's numbers for its plants (the water at each stop of the scene, the roots'
// length, when a drop falls, when a plant wilts or flowers) and a flat drawing of
// them; where WebGL runs, this draws the same plants in 3D over the flat one.
//
// A topic is a plant in water: the water is how much is remembered, the roots how long
// it lasts. In a light theme it is a succulent rosette in a glass bowl, its leaves in
// the golden-angle spiral, lit from within while it is remembered (a glowing rim, a
// pool of light on the water) and dull, thin and brown as it is forgotten. In a dark
// theme it is a plant that glows in the dark, as brightly as it is remembered, its
// tendrils curling and its light dimming as it is forgotten (the owner's choice, D119).
// Bowls, jars and bases are surfaces of revolution; leaves, stems and roots are swept
// along curves; pebbles, buds and lights are spheres. One small shader lights them.
//
// A frame is drawn only when something changed: the page scrolled, a scene came into
// view, the page was resized or its theme changed. A scene eases to where the page is
// and stops (D93: nothing runs while the page is still). With reduced motion, or where
// the browser does not tie animations to scrolling, each scene is drawn once, complete,
// as its flat drawing is. Layout only: every number is the server's.

const root = document.documentElement;
const scenes = [...document.querySelectorAll(".gd-scene[data-scene]")];
const PI = Math.PI;

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (t) => t * t * (3 - 2 * t);
const span = (p, r) => (r ? clamp((p - r[0]) / (r[1] - r[0])) : 1);
const mix3 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul3 = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (a) => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
const hash = (n) => {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
};
const once = (f) => {
  let v;
  return () => (v ??= f());
};

// ---------------------------------------------------------------- shapes
function spline(pts, per) {
  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    for (let s = 0; s < per; s++) {
      const t = s / per, t2 = t * t, t3 = t2 * t;
      out.push(p1.map((_, d) => 0.5 * (2 * p1[d] + (p2[d] - p0[d]) * t + (2 * p0[d] - 5 * p1[d] + 4 * p2[d] - p3[d]) * t2
        + (3 * p1[d] - p0[d] - 3 * p2[d] + p3[d]) * t3)));
    }
  }
  out.push(pts[pts.length - 1].slice());
  return out;
}

class Geo {
  constructor() {
    this.v = [];
    this.i = [];
  }
  get n() {
    return this.v.length / 9;
  }
  vert(p, n, c) {
    this.v.push(p[0], p[1], p[2], n[0], n[1], n[2], c[0], c[1], c[2]);
  }
  grid(base, rows, cols) {
    for (let j = 0; j < rows; j++) {
      for (let k = 0; k < cols; k++) {
        const a = base + j * (cols + 1) + k, b = a + cols + 1;
        this.i.push(a, b, a + 1, a + 1, b, b + 1);
      }
    }
  }
  append(h) {
    const base = this.n;
    for (const x of h.v) this.v.push(x);
    for (const k of h.i) this.i.push(k + base);
  }
  // A grid of points (rows of cols + 1, each row closed round), its normals from neighbours.
  static fromGrid(pts, cols, colour) {
    const g = new Geo(), rows = pts.length / (cols + 1) - 1;
    const at = (j, k) => pts[clamp(j, 0, rows) * (cols + 1) + ((k + cols) % cols)];
    for (let j = 0; j <= rows; j++) {
      for (let k = 0; k <= cols; k++) {
        const du = sub(at(j, k + 1), at(j, k - 1)), dv = sub(at(j + 1, k), at(j - 1, k));
        g.vert(pts[j * (cols + 1) + k], unit(cross(du, dv)), colour(j / rows, k / cols));
      }
    }
    g.grid(0, rows, cols);
    return g;
  }
}

// A profile of [radius, height] turned about the vertical axis.
function lathe(g, prof, seg, col) {
  const base = g.n, m = prof.length;
  for (let j = 0; j < m; j++) {
    const a = prof[Math.max(0, j - 1)], b = prof[Math.min(m - 1, j + 1)];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1, nr = (b[1] - a[1]) / l, ny = -(b[0] - a[0]) / l;
    for (let k = 0; k <= seg; k++) {
      const t = (k / seg) * PI * 2, cs = Math.cos(t), sn = Math.sin(t), r = prof[j][0];
      const p = [r * cs, prof[j][1], r * sn];
      g.vert(p, [nr * cs, ny, nr * sn], typeof col === "function" ? col(j / (m - 1), p) : col);
    }
  }
  g.grid(base, m - 1, seg);
}

// A tube along a curve, its frame carried along so that it does not twist.
function tube(g, pts, rads, sides, col) {
  const n = pts.length;
  if (n < 2) return;
  let t = unit(sub(pts[1], pts[0]));
  let nn = unit(Math.abs(t[1]) < 0.9 ? cross(t, [0, 1, 0]) : cross(t, [1, 0, 0]));
  const base = g.n;
  for (let i = 0; i < n; i++) {
    t = unit(sub(pts[Math.min(n - 1, i + 1)], pts[Math.max(0, i - 1)]));
    const d = dot(nn, t);
    nn = unit([nn[0] - t[0] * d, nn[1] - t[1] * d, nn[2] - t[2] * d]);
    const bb = cross(t, nn), c = typeof col === "function" ? col(i / (n - 1)) : col;
    for (let k = 0; k <= sides; k++) {
      const a = (k / sides) * PI * 2, dir = add(mul3(nn, Math.cos(a)), mul3(bb, Math.sin(a)));
      g.vert(add(pts[i], mul3(dir, rads[i])), dir, c);
    }
  }
  g.grid(base, n - 1, sides);
}

const unitSphere = (lat, lon) => {
  const v = [];
  for (let j = 0; j <= lat; j++) {
    const th = (j / lat) * PI, y = -Math.cos(th), r = Math.sin(th);
    for (let k = 0; k <= lon; k++) {
      const a = (k / lon) * PI * 2;
      v.push([r * Math.cos(a), y, r * Math.sin(a)]);
    }
  }
  return { v, lat, lon };
};
const SPHERE = unitSphere(10, 14), SMALL = unitSphere(6, 9);
// An ellipsoid along three axes (each a direction times a half-length).
function ellipsoid(g, at, ex, ey, ez, col, tmpl = SPHERE) {
  const base = g.n, lx = dot(ex, ex), ly = dot(ey, ey), lz = dot(ez, ez);
  for (const q of tmpl.v) {
    g.vert(add(at, add(add(mul3(ex, q[0]), mul3(ey, q[1])), mul3(ez, q[2]))),
      unit(add(add(mul3(ex, q[0] / lx), mul3(ey, q[1] / ly)), mul3(ez, q[2] / lz))),
      typeof col === "function" ? col(q) : col);
  }
  g.grid(base, tmpl.lat, tmpl.lon);
}
const ball = (g, at, r, col, tmpl) => ellipsoid(g, at, [r, 0, 0], [0, r, 0], [0, 0, r], col, tmpl);
const ring = (r0, y0, rr, k = 12) => {
  const out = [];
  for (let i = 0; i <= k; i++) {
    const a = (i / k) * PI * 2 - PI / 2;
    out.push([r0 + Math.cos(a) * rr, y0 + Math.sin(a) * rr]);
  }
  return out;
};
const insideOf = (prof) => (y) => {
  for (let i = 1; i < prof.length; i++) {
    if (prof[i][1] >= y) return lerp(prof[i - 1][0], prof[i][0], (y - prof[i - 1][1]) / Math.max(1e-6, prof[i][1] - prof[i - 1][1]));
  }
  return prof[prof.length - 1][0];
};
const DISC = once(() => {
  const g = new Geo();
  lathe(g, [[1, 0], [0, 0]], 48, [1, 1, 1]);
  return g;
});
const DROP = once(() => {
  const g = new Geo();
  for (const q of SMALL.v) {
    const k = 1 - 0.6 * Math.max(0, q[1]);
    g.vert([q[0] * k, q[1] * 1.4, q[2] * k], unit(q), [1, 1, 1]);
  }
  g.grid(0, SMALL.lat, SMALL.lon);
  return g;
});

// ---------------------------------------------------------------- the two plants
// A pot's state: w (water, 0 to 1), droop (0 fresh to 1 parched, a wilt added), wilt,
// roots (0 to 1), bloom (0 to 1), drop (a drop falling, 0 to 1), ripple.
const ROSETTE = (() => {
  const BOWL = spline([[0, 0.03], [0.3, 0.03], [0.47, 0.06], [0.56, 0.15], [0.59, 0.3]], 6);
  const IN = spline([[0, 0.045], [0.29, 0.045], [0.455, 0.075], [0.545, 0.16], [0.575, 0.29]], 6);
  const inR = insideOf(IN);
  const statics = once(() => {
    const glass = new Geo(), water = new Geo(), coaster = new Geo(), pebbles = new Geo();
    lathe(glass, BOWL, 72, [1, 1, 1]);
    lathe(glass, ring(0.584, 0.3, 0.012), 72, [1, 1, 1]);
    lathe(water, IN, 64, [1, 1, 1]);
    lathe(coaster, spline([[0, -0.05], [0.56, -0.05], [0.6, -0.03], [0.6, 0], [0.57, 0.025], [0, 0.025]], 4), 72,
      (t, p) => mix3([0.78, 0.62, 0.46], [0.68, 0.52, 0.38], 0.5 + 0.5 * Math.sin(Math.hypot(p[0], p[2]) * 60)));
    const stones = [[0.95, 0.94, 0.92], [0.8, 0.79, 0.77], [0.88, 0.84, 0.78], [0.68, 0.68, 0.7]];
    for (let i = 0; i < 90; i++) {
      const layer = i < 55 ? 0 : 1, rr = (layer ? 0.4 : 0.5) * Math.sqrt(hash(i * 3.3)), a = i * 2.39996;
      const r = 0.03 + 0.02 * hash(i * 7.1), y = 0.045 + r * 0.8 + layer * 0.05 + 0.02 * hash(i * 1.7);
      if (rr + r > inR(y) - 0.01) continue;
      ellipsoid(pebbles, [Math.cos(a) * rr, y, Math.sin(a) * rr], [r, 0, 0], [0, r * 0.7, 0], [0, 0, r * 0.85], stones[i % 4], SMALL);
    }
    return { glass, water, coaster, pebbles };
  });
  // One fleshy leaf: a pod along `dir`, fat near its tip, pointed.
  function pod(g, base, dir, L, W, T, colour) {
    const side = unit(cross(dir, [0, 1, 0])), up = unit(cross(side, dir)), rows = 9, cols = 12, pts = [];
    for (let j = 0; j <= rows; j++) {
      const s = j / rows, prof = Math.pow(Math.sin(PI * Math.min(1, 0.02 + s * 0.98)), 0.55) * (0.7 + 0.55 * s);
      const c = add(base, add(mul3(dir, L * s), mul3(up, 0.18 * L * s * s)));
      for (let k = 0; k <= cols; k++) {
        const a = (k / cols) * PI * 2, sn = Math.sin(a);
        pts.push(add(c, add(mul3(side, Math.cos(a) * W * 0.5 * prof), mul3(up, sn * T * 0.5 * prof * (sn > 0 ? 0.55 : 1)))));
      }
    }
    g.append(Geo.fromGrid(pts, cols, (s) => colour(s)));
  }
  return {
    pitch: 0.52, reach: 0.62, y0: -0.05, y1: 0.5, y1Bloom: 0.9,
    // Low, wide bowls: two side by side, three in a triangle, the middle one in front,
    // so that the picture is not a thin strip.
    layout: (n) => (n === 3 ? [[-0.68, -0.4], [0, 0.48], [0.68, -0.4]] : n === 2 ? [[-0.64, 0], [0.64, 0]] : [[0, 0]]),
    lights: { sky: [0.72, 0.74, 0.8], ground: [0.46, 0.42, 0.4], light: [0.6, 0.57, 0.53] },
    statics, inR,
    plant(s, seed) {
      const d = s.droop, leaves = new Geo(), roots = new Geo(), N = 40, base = [0, 0.19, 0];
      for (let i = 0; i < N; i++) {
        const t = i / (N - 1), a = i * 2.39996 + seed;
        const L = lerp(0.3, 0.075, Math.pow(t, 0.75)), W = lerp(0.13, 0.05, t), T = lerp(0.05, 0.034, t) * (1 - 0.45 * d);
        const elev = lerp(0.1, 1.25, Math.pow(t, 0.9)) - d * 0.5 * (1 - t);
        const dir = [Math.cos(a) * Math.cos(elev), Math.sin(elev), Math.sin(a) * Math.cos(elev)];
        const brown = clamp(clamp(1 - t * 2.2) * d + s.wilt * 0.6);
        pod(leaves, add(base, mul3(dir, 0.02 + 0.02 * (1 - t))), dir, L, W, T, (u) => {
          const body = mix3(mix3([0.42, 0.8, 0.7], [0.6, 0.52, 0.44], d), [0.5, 0.38, 0.28], brown);
          return mix3(body, mix3([0.98, 0.52, 0.66], [0.56, 0.36, 0.3], d), smooth(clamp((u - 0.72) / 0.28)));
        });
      }
      // In flower: arching stalks of coral bells, as an echeveria flowers.
      if (s.bloom > 0.01) {
        for (let f = 0; f < 3; f++) {
          const a = seed * 2.1 + f * 2.2, h = [Math.cos(a), 0, Math.sin(a)], stalk = [], tall = 0.36 + 0.1 * hash(f + seed);
          for (let k = 0; k <= 12; k++) {
            const u = k / 12;
            stalk.push(add([0, 0.2 + u * tall * s.bloom, 0], mul3(h, (0.05 + Math.sin(u * 1.5) * 0.2) * s.bloom)));
          }
          tube(leaves, stalk, stalk.map((_, k) => lerp(0.017, 0.01, k / 12)), 6, (u) => mix3([0.5, 0.6, 0.42], [0.86, 0.5, 0.4], u));
          for (let k = 0; k < 6; k++) {
            const q = stalk[6 + k], r = 0.042 * s.bloom * (1 - 0.08 * k);
            ellipsoid(leaves, add(q, [0, -r * 0.7, 0]), [r, 0, 0], [0, r * 1.2, 0], [0, 0, r],
              (v) => mix3([0.98, 0.42, 0.3], [1, 0.84, 0.4], 0.5 - 0.5 * v[1]), SMALL);
          }
        }
      }
      const reach = 0.1 + 0.9 * s.roots;
      for (let i = 0; i < 9; i++) {
        const a0 = i * 2.39996 + seed, pts = [], steps = Math.max(3, Math.round(14 * reach));
        for (let k = 0; k <= steps; k++) {
          const u = (k / steps) * reach, y = Math.max(0.055, 0.17 - u * 0.2), r = Math.min(inR(y) - 0.03, 0.03 + u * 0.42);
          const a = a0 + 0.4 * Math.sin(u * 6 + i);
          pts.push([r * Math.cos(a), y, r * Math.sin(a)]);
        }
        tube(roots, pts, pts.map(() => 0.006), 5, [0.95, 0.92, 0.84]);
      }
      return { leaves, roots };
    },
    // What to draw of one pot, around its plant, in the pot's own space.
    parts(s, plant) {
      const st = statics(), level = 0.05 + s.w * 0.15, shine = clamp((s.w - 0.5) / 0.5) * (1 - s.wilt);
      return {
        solid: [
          { geo: st.coaster, mat: [0.12, 20, 0.15, 0] }, { geo: st.pebbles, mat: [0.35, 34, 0.15, 0] },
          { geo: plant.leaves, mat: [0.3, 30, 0.15 + 0.5 * shine, 0.04 + 0.16 * shine], glow: mix3([0.8, 0.74, 0.66], [0.3, 1, 0.82], shine) },
          { geo: plant.roots, mat: [0.25, 30, 0.2, 0.2 * shine], glow: [0.6, 1, 0.9] },
        ],
        shadows: [{ y: 0.026, r: 0.56, a: 0.2 }],
        glass: { geo: st.glass, tint: [0.84, 0.94, 0.96, 0.04], mat: [0, 0, 0.5, 0] },
        water: { body: st.water, through: [plant.roots, st.pebbles], tint: [0.3, 0.62, 0.95, 0.4], topTint: [0.52, 0.8, 1, 0.45], level, r: inR(level) - 0.004 },
        lights: [{ y: level + 0.003, r: 0.52, col: [0.16, 0.86, 0.68], a: 0.55 * shine }, { y: level + 0.005, r: 0.26, col: [0.6, 1, 0.88], a: 0.6 * shine }],
        drop: [0.32, 0.05, 0.75, level],
      };
    },
  };
})();

const GLOW = (() => {
  const JAR = spline([[0, 0.04], [0.22, 0.04], [0.27, 0.08], [0.285, 0.18], [0.285, 0.8], [0.29, 0.85]], 5);
  const IN = spline([[0, 0.06], [0.21, 0.06], [0.255, 0.1], [0.27, 0.18], [0.27, 0.82]], 5);
  const inR = insideOf(IN);
  const statics = once(() => {
    const glass = new Geo(), water = new Geo(), stand = new Geo();
    lathe(glass, JAR, 64, [1, 1, 1]);
    lathe(glass, ring(0.29, 0.85, 0.01), 64, [1, 1, 1]);
    lathe(water, IN, 56, [1, 1, 1]);
    lathe(stand, spline([[0, -0.06], [0.4, -0.06], [0.44, -0.03], [0.42, 0.04], [0, 0.04]], 4), 64, [0.12, 0.13, 0.18]);
    return { glass, water, stand };
  });
  return {
    pitch: 0.18, reach: 0.46, y0: -0.06, y1: 1.42, y1Bloom: 1.5,
    layout: (n) => Array.from({ length: n }, (_, i) => [(i - (n - 1) / 2) * 0.98, 0]),
    lights: { sky: [0.24, 0.27, 0.42], ground: [0.06, 0.06, 0.1], light: [0.3, 0.32, 0.45] },
    statics, inR,
    plant(s, seed) {
      const d = s.droop, glow = new Geo(), core = new Geo(), roots = new Geo(), halos = [];
      const shine = clamp((s.w - 0.45) / 0.55) * (1 - 0.7 * s.wilt), big = 1 + 0.8 * s.bloom;
      for (let i = 0; i < 9; i++) {
        const a = i * 2.39996 + seed, h = [Math.cos(a), 0, Math.sin(a)], pts = [];
        const len = 0.42 + 0.18 * hash(i + seed), curl = 1 + d * 2 + 0.4 * hash(i + 2);
        let p = [0, 0.86, 0];
        for (let k = 0; k <= 22; k++) {
          const th = 0.15 + curl * Math.pow(k / 22, 1.7);
          pts.push(p);
          p = add(p, mul3(add(mul3(h, Math.sin(th)), [0, Math.cos(th), 0]), len / 22));
        }
        const col = (u) => mix3(mix3([0.2, 0.95, 0.9], [0.85, 0.35, 1], u), [0.35, 0.3, 0.5], d);
        tube(core, pts, pts.map((_, k) => lerp(0.014, 0.005, k / 22)), 6, (u) => mul3(col(u), 0.6));
        tube(glow, pts, pts.map((_, k) => lerp(0.03, 0.012, k / 22)), 6, col);
        const tip = pts[pts.length - 1];
        ball(core, tip, 0.028 * big, mix3([1, 0.75, 1], [0.4, 0.35, 0.5], d));
        halos.push({ p: tip, r: 0.13 * big, col: [...mix3([0.75, 0.4, 1], [0.3, 0.25, 0.4], d), 0.9 * shine + 0.1] });
      }
      ball(core, [0, 0.88, 0], 0.05 * big, [0.6, 1, 0.95]);
      halos.push({ p: [0, 0.9, 0], r: 0.22 * big, col: [0.3, 1, 0.9, 0.8 * shine + 0.1] });
      const reach = 0.12 + 0.88 * s.roots;
      for (let i = 0; i < 12; i++) {
        const a0 = i * 2.39996 + seed, pts = [], steps = Math.max(4, Math.round(24 * reach));
        for (let k = 0; k <= steps; k++) {
          const u = (k / steps) * reach, y = 0.84 - u * 0.74, r = Math.min(inR(y) - 0.03, 0.01 + 0.2 * smooth(clamp(u * 1.2)) * (0.4 + 0.6 * hash(i)));
          const a = a0 + 0.7 * Math.sin(u * 8 + i);
          pts.push([r * Math.cos(a), y, r * Math.sin(a)]);
        }
        tube(roots, pts, pts.map(() => 0.006), 4, [0.25, 0.9, 0.75]);
      }
      return { glow, core, roots, halos, shine };
    },
    parts(s, plant) {
      const st = statics(), shine = plant.shine, level = 0.08 + s.w * 0.7, halos = plant.halos.slice();
      for (let i = 0; i < 18; i++) {
        const y = 0.08 + hash(i * 2.2) * (level - 0.1), rr = Math.sqrt(hash(i * 5.1)) * (inR(y) - 0.04), a = i * 2.39996;
        halos.push({ p: [Math.cos(a) * rr, y, Math.sin(a) * rr], r: 0.035, col: [0.4, 1, 0.85, 0.7 * shine] });
      }
      return {
        solid: [{ geo: st.stand, mat: [0.9, 80, 0.3, 0] }, { geo: plant.core, mat: [0, 10, 0.2, 0.9] }],
        glass: { geo: st.glass, tint: [0.4, 0.85, 1, 0.02], mat: [0, 0, 0.35, 0.6] },
        water: { body: st.water, through: [], tint: [0.08, 0.5 * (0.3 + 0.7 * shine), 0.48 * (0.3 + 0.7 * shine), 0.42], topTint: [0.2, 0.9, 0.85, 0.5], level, r: inR(level) - 0.004, glow: 0.6 * shine + 0.1 },
        glow: [{ geo: plant.glow, tint: [1, 1, 1, 0.55 * (0.25 + 0.75 * shine)] }, { geo: plant.roots, tint: [1, 1, 1, 0.7 * (0.3 + 0.7 * shine)] }],
        halos,
        drop: [0.12, 0.04, 1.4, 0.86],
      };
    },
  };
})();

// ---------------------------------------------------------------- matrices
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function lookAt(eye, at) {
  const z = unit(sub(eye, at)), x = unit(cross([0, 1, 0], z)), y = cross(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}
function mat(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}
// A pot at (x, z) on the table, the whole table turned by `turn` about the vertical axis.
function potModel(turn, x, z = 0) {
  const c = Math.cos(turn), s = Math.sin(turn);
  return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, c * x + s * z, 0, -s * x + c * z, 1];
}
const scaleAt = (sx, y, sz = sx, x = 0, z = 0) => [sx, 0, 0, 0, 0, 1, 0, 0, 0, 0, sz, 0, x, y, z, 1];

// ---------------------------------------------------------------- the shader
const VS = `
attribute vec3 aPos; attribute vec3 aNrm; attribute vec3 aCol;
uniform mat4 uModel; uniform mat4 uView;
varying vec3 vN; varying vec3 vW; varying vec3 vC; varying vec3 vL;
void main() {
  vec4 w = uModel * vec4(aPos, 1.0);
  vW = w.xyz; vN = (uModel * vec4(aNrm, 0.0)).xyz; vC = aCol; vL = aPos;
  gl_Position = uView * w;
}`;
const FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec3 vN; varying vec3 vW; varying vec3 vC; varying vec3 vL;
uniform vec3 uEye; uniform vec3 uKey; uniform vec3 uFill;
uniform vec3 uSky; uniform vec3 uGround; uniform vec3 uLight;
uniform vec4 uTint; uniform vec4 uMat; uniform float uMode; uniform float uSide; uniform float uLevel;
uniform vec3 uRipple; uniform vec3 uGlow;
void main() {
  vec3 N = normalize(vN), V = normalize(uEye - vW);
  if (uMode < 0.5) {
    // Leaves, stems, roots, stones: lit on either face, a rim, a light from within.
    if (dot(N, V) < 0.0) N = -N;
    vec3 base = vC * uTint.rgb;
    float wrap = max((dot(N, uKey) + 0.4) / 1.4, 0.0);
    vec3 hemi = mix(uGround, uSky, N.y * 0.5 + 0.5);
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), max(uMat.y, 1.0)) * uMat.x;
    float rim = pow(1.0 - max(dot(N, V), 0.0), 2.6) * uMat.z;
    vec3 rimCol = length(uGlow) > 0.0 ? uGlow : mix(uSky, base, 0.4);
    vec3 col = base * (hemi + wrap * uLight) + sp + rim * rimCol + base * uMat.w;
    gl_FragColor = vec4(col * uTint.a, uTint.a);
  } else if (uMode < 1.5) {
    // Glass: drawn twice, its far side (uSide -1) and then its near side.
    float f = dot(N, V);
    if (f * uSide < 0.0) discard;
    if (uSide < 0.0) N = -N;
    float fr = pow(1.0 - abs(f), 2.2);
    vec3 R = reflect(-V, N);
    float band = smoothstep(0.16, 0.24, R.x) * (1.0 - smoothstep(0.32, 0.42, R.x)) * smoothstep(-0.2, 0.35, R.y);
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), 90.0) * 1.3 + pow(max(dot(N, normalize(uFill + V)), 0.0), 26.0) * 0.3;
    float hi = sp + band * (uSide > 0.0 ? 0.5 : 0.15);
    float a = clamp(uTint.a + fr * uMat.z, 0.0, 1.0);
    vec3 col = uTint.rgb * mix(uGround, uSky, N.y * 0.5 + 0.5) * a + vec3(hi) + uTint.rgb * fr * uMat.w;
    gl_FragColor = vec4(col, clamp(a + hi * 0.6, 0.0, 1.0));
  } else if (uMode < 2.5) {
    // The water, up to its level; it may glow.
    if (vL.y > uLevel) discard;
    if (dot(N, V) < 0.0) discard;
    float fr = pow(1.0 - dot(N, V), 2.0);
    float depth = clamp((uLevel - vL.y) * 2.5, 0.0, 1.0);
    vec3 base = mix(uTint.rgb * 1.1, uTint.rgb * 0.7, depth);
    float a = uTint.a + fr * 0.3;
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), 60.0) * 0.6;
    gl_FragColor = vec4(base * a + sp + base * uMat.w, clamp(a + sp * 0.4, 0.0, 1.0) * (1.0 - step(0.5, uMat.w) * 0.6));
  } else if (uMode < 3.5) {
    // The water's surface, rippling where a drop has just fallen.
    float d = length(vL.xz), x = d - uRipple.x;
    float wave = uRipple.y * cos(x * 40.0) * exp(-abs(x) * 8.0);
    vec3 n = normalize(vec3(0.0, 1.0, 0.0) + vec3(vL.x, 0.0, vL.z) / max(d, 0.001) * wave * 0.9);
    float edge = smoothstep(0.75, 1.0, d);
    float sp = pow(max(dot(n, normalize(uKey + V)), 0.0), 70.0) * 1.1 + abs(wave) * 0.25;
    float a = clamp(uTint.a + edge * 0.2, 0.0, 1.0);
    vec3 col = mix(uTint.rgb * 1.08, uTint.rgb * 0.85, edge) * a + sp + uTint.rgb * uMat.w;
    gl_FragColor = vec4(col, clamp(a + sp * 0.3, 0.0, 1.0) * (1.0 - step(0.5, uMat.w) * 0.5));
  } else if (uMode < 4.5) {
    float d = length(vL.xz);
    gl_FragColor = vec4(0.0, 0.0, 0.0, pow(1.0 - smoothstep(0.0, 1.0, d), 1.6) * uTint.a);
  } else if (uMode < 5.5) {
    // A glow, added to what is behind it.
    float fr = pow(1.0 - abs(dot(N, V)), 1.5);
    gl_FragColor = vec4(vC * uTint.rgb * (0.55 + fr * 0.9) * uTint.a, 0.0);
  } else if (uMode < 6.5) {
    // A halo of light facing the camera, added.
    float d = length(vC.xy);
    float a = exp(-d * d * 3.5) * (1.0 - smoothstep(0.85, 1.0, d)) * uTint.a;
    gl_FragColor = vec4(uTint.rgb * a, 0.0);
  } else {
    // A pool of light on the water, laid over it (it shows on a light page too).
    float d = length(vL.xz);
    float a = pow(1.0 - smoothstep(0.0, 1.0, d), 2.0) * uTint.a;
    gl_FragColor = vec4(uTint.rgb * a, a);
  }
}`;
const isDark = () => root.dataset.theme === "dark"
  || (root.dataset.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);

// ---------------------------------------------------------------- a scene
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches || root.dataset.motion === "reduce";
const timelines = typeof CSS !== "undefined" && CSS.supports && CSS.supports("animation-timeline: view()");
const moving = !reduce && timelines;
let topbar = 0;
const readTopbar = () => {
  topbar = parseFloat(getComputedStyle(root).getPropertyValue("--topbar")) || 0;
};

class Scene {
  constructor(el) {
    this.el = el;
    this.spec = JSON.parse(el.dataset.scene);
    this.pin = el.closest(".code-pin");
    this.story = el.closest(".code-story");
    this.fig = el.closest(".scroll-fig") || el;
    this.shown = 0;
    this.visible = false;
    this.ready = false;
  }

  // Where the page is in this scene's story, 0 to 1, as the stylesheet's timelines say.
  progress() {
    const s = this.spec;
    if (!moving || s.t === "still") return s.rest ?? 1;
    const vh = innerHeight;
    // The track is the pinned stage's, or on a laptop the whole story's (D118), as the
    // stylesheet's --pin timeline is.
    const track = this.pin && getComputedStyle(this.pin).display === "contents" ? this.story : this.pin;
    if (s.t === "pin" && track) {
      const r = track.getBoundingClientRect();
      return clamp((topbar - r.top) / Math.max(1, r.height - (vh - topbar)));
    }
    const r = this.fig.getBoundingClientRect();
    if (s.t === "exit") return clamp(-r.top / Math.max(1, r.height));
    return span((vh - r.top) / (vh + r.height), s.r);
  }

  start() {
    const canvas = document.createElement("canvas");
    canvas.className = "gd-canvas";
    canvas.setAttribute("aria-hidden", "true");
    const opts = { alpha: true, antialias: true, premultipliedAlpha: true, powerPreference: "low-power" };
    const gl = canvas.getContext("webgl2", opts) || canvas.getContext("webgl", opts);
    if (!gl) return false;
    this.el.append(canvas);
    this.canvas = canvas;
    this.gl = gl;
    canvas.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      this.ready = false;
      this.el.classList.remove("gd-3d");
    });
    if (!this.build()) {
      canvas.remove();
      return false;
    }
    return true;
  }

  build() {
    const gl = this.gl;
    const shader = (type, src) => {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      return gl.getShaderParameter(sh, gl.COMPILE_STATUS) ? sh : null;
    };
    const vs = shader(gl.VERTEX_SHADER, VS), fs = shader(gl.FRAGMENT_SHADER, FS);
    if (!vs || !fs) return false;
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
    this.prog = prog;
    this.loc = {};
    for (const name of ["aPos", "aNrm", "aCol"]) this.loc[name] = gl.getAttribLocation(prog, name);
    for (const name of ["uModel", "uView", "uEye", "uKey", "uFill", "uSky", "uGround", "uLight", "uTint", "uMat", "uMode",
      "uSide", "uLevel", "uRipple", "uGlow"]) this.loc[name] = gl.getUniformLocation(prog, name);
    this.uint = !!(gl.getExtension && (gl instanceof WebGLRenderingContext ? gl.getExtension("OES_element_index_uint") : true));
    this.buffers = new Map();
    this.scratch = { vbo: gl.createBuffer(), ibo: gl.createBuffer() };
    this.plants = this.spec.pots.map(() => ({ key: "", made: null }));
    gl.enable(gl.DEPTH_TEST);
    gl.clearColor(0, 0, 0, 0);
    this.ready = true;
    return true;
  }

  // A shape's buffers, made once; a plant's are let go when it is made again.
  buffer(geo) {
    let b = this.buffers.get(geo);
    if (b) return b;
    const gl = this.gl;
    b = { vbo: gl.createBuffer(), ibo: gl.createBuffer() };
    this.upload(b, geo, gl.STATIC_DRAW);
    this.buffers.set(geo, b);
    return b;
  }
  upload(b, geo, usage) {
    const gl = this.gl, big = geo.n > 65535 && this.uint;
    gl.bindBuffer(gl.ARRAY_BUFFER, b.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(geo.v), usage);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, b.ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, big ? new Uint32Array(geo.i) : new Uint16Array(geo.i), usage);
    b.count = geo.i.length;
    b.type = big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
  }
  release(geos) {
    for (const geo of geos) {
      const b = this.buffers.get(geo);
      if (!b) continue;
      this.gl.deleteBuffer(b.vbo);
      this.gl.deleteBuffer(b.ibo);
      this.buffers.delete(geo);
    }
  }

  draw(geo, model, mode, tint, opts = {}) {
    if (!geo || !geo.i.length) return;
    const gl = this.gl, L = this.loc, b = opts.scratch ? this.scratch : this.buffer(geo);
    if (opts.scratch) this.upload(b, geo, gl.STREAM_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, b.vbo);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, b.ibo);
    gl.vertexAttribPointer(L.aPos, 3, gl.FLOAT, false, 36, 0);
    gl.vertexAttribPointer(L.aNrm, 3, gl.FLOAT, false, 36, 12);
    gl.vertexAttribPointer(L.aCol, 3, gl.FLOAT, false, 36, 24);
    gl.uniformMatrix4fv(L.uModel, false, model);
    gl.uniform1f(L.uMode, mode);
    gl.uniform4f(L.uTint, tint[0], tint[1], tint[2], tint[3] ?? 1);
    const m = opts.mat || [0.25, 40, 0.25, 0];
    gl.uniform4f(L.uMat, m[0], m[1], m[2], m[3]);
    gl.uniform1f(L.uSide, opts.side ?? 1);
    gl.uniform1f(L.uLevel, opts.level ?? 99);
    const r = opts.ripple || [0, 0, 1];
    gl.uniform3f(L.uRipple, r[0], r[1], r[2]);
    const g = opts.glow || [0, 0, 0];
    gl.uniform3f(L.uGlow, g[0], g[1], g[2]);
    gl.drawElements(gl.TRIANGLES, b.count, b.type, 0);
  }

  // Each plant's state at progress p, from the server's numbers.
  stateAt(pot, p) {
    let z = pot.z;
    if (Array.isArray(z)) {
      const q = p * (z.length - 1), i = Math.min(z.length - 2, Math.floor(q));
      z = lerp(z[i], z[i + 1], q - i);
    }
    const w = clamp(z / 20), grow = pot.ra ? smooth(span(p, pot.ra)) : 1;
    const wilt = pot.wilt ? smooth(span(p, pot.wilt)) : 0;
    return {
      w, wilt, droop: clamp((1 - w) * 2.2 + wilt * 0.8),
      roots: lerp(pot.rf ?? pot.rt ?? 20, pot.rt ?? 20, grow) / 20,
      bloom: Array.isArray(pot.bloom) ? smooth(span(p, pot.bloom)) : pot.bloom ?? 0,
      drop: pot.drop ? span(p, pot.drop) : 0,
    };
  }

  // The camera: a little above the table, as close as it can be with every pot whole
  // whichever way the table has turned; found by projecting that outline, once for
  // each shape of canvas and each plant.
  camera(kit, aspect, bloom) {
    const key = `${aspect.toFixed(3)}|${kit === GLOW}|${bloom}`;
    if (this.cam && this.cam.key === key) return this.cam;
    const fy = (24 * PI) / 180, el = kit.pitch, n = this.spec.pots.length;
    const reach = Math.max(...kit.layout(n).map(([x, z]) => Math.hypot(x, z))) + kit.reach;
    const top = bloom ? kit.y1Bloom : kit.y1, outline = [];
    for (let k = 0; k < 32; k++) {
      const a = (k / 32) * PI * 2;
      outline.push([Math.cos(a) * reach, kit.y0, Math.sin(a) * reach], [Math.cos(a) * reach * 0.92, top, Math.sin(a) * reach * 0.92]);
    }
    const proj = perspective(fy, aspect, 0.05, 80);
    let dist = 6, ty = (kit.y0 + top) / 2, eye, view;
    for (let it = 0; it < 10; it++) {
      eye = [0, ty + Math.sin(el) * dist, Math.cos(el) * dist];
      view = mat(proj, lookAt(eye, [0, ty, 0]));
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
      for (const q of outline) {
        const w = view[3] * q[0] + view[7] * q[1] + view[11] * q[2] + view[15];
        const x = (view[0] * q[0] + view[4] * q[1] + view[8] * q[2] + view[12]) / w;
        const y = (view[1] * q[0] + view[5] * q[1] + view[9] * q[2] + view[13]) / w;
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
      ty += ((y0 + y1) / 2) * dist * Math.tan(fy / 2);
      dist *= Math.max((x1 - x0) / 2, (y1 - y0) / 2) / 0.92;
    }
    this.cam = { key, eye, view };
    return this.cam;
  }

  render() {
    if (!this.ready) return;
    const gl = this.gl, canvas = this.canvas, L = this.loc, spec = this.spec;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr)), h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(this.prog);
    for (const name of ["aPos", "aNrm", "aCol"]) gl.enableVertexAttribArray(L[name]);

    // The plant of the theme: a glowing rosette when light, a plant of light when dark.
    const kit = isDark() ? GLOW : ROSETTE;
    if (this.kit !== kit) {
      for (const pl of this.plants) if (pl.made) this.release(Object.values(pl.made).filter((x) => x instanceof Geo));
      this.plants.forEach((pl) => { pl.key = ""; pl.made = null; });
      this.kit = kit;
    }
    const p = this.shown, n = spec.pots.length;
    const turn = ((spec.turn ? lerp(spec.turn[0], spec.turn[1], p) : -12) * PI) / 180;
    const at = kit.layout(n);
    const states = spec.pots.map((pot) => this.stateAt(pot, p));
    const { eye, view } = this.camera(kit, w / h, spec.pots.some((pot) => pot.bloom));
    gl.uniformMatrix4fv(L.uView, false, view);
    gl.uniform3fv(L.uEye, eye);
    gl.uniform3fv(L.uKey, unit([-0.5, 0.85, 0.6]));
    gl.uniform3fv(L.uFill, unit([0.75, 0.25, -0.35]));
    gl.uniform3fv(L.uSky, kit.lights.sky);
    gl.uniform3fv(L.uGround, kit.lights.ground);
    gl.uniform3fv(L.uLight, kit.lights.light);

    const pots = states.map((st, i) => {
      const pl = this.plants[i];
      const key = [st.droop, st.wilt, st.roots, st.bloom].map((v) => v.toFixed(3)).join();
      if (pl.key !== key) {
        if (pl.made) this.release(Object.values(pl.made).filter((x) => x instanceof Geo));
        pl.made = kit.plant(st, i * 1.7 + (spec.seed ?? 0));
        pl.key = key;
      }
      const model = potModel(turn, at[i][0], at[i][1]);
      return { st, model, depth: model[14], parts: kit.parts(st, pl.made) };
    });

    // Opaque first; then the see-through, the farthest pot first; then what glows.
    gl.disable(gl.BLEND);
    gl.depthMask(true);
    for (const pt of pots) for (const o of pt.parts.solid) this.draw(o.geo, pt.model, 0, [1, 1, 1, 1], { mat: o.mat, glow: o.glow });
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    for (const pt of pots) {
      for (const s of pt.parts.shadows || []) this.draw(DISC(), mat(pt.model, scaleAt(s.r, s.y)), 4, [0, 0, 0, s.a]);
    }
    // Farthest first: a pot's depth toward the camera is its z in the world.
    const order = pots.slice().sort((a, b) => a.depth - b.depth);
    for (const pt of order) {
      const { parts, model, st } = pt, wt = parts.water;
      this.draw(parts.glass.geo, model, 1, parts.glass.tint, { side: -1, mat: parts.glass.mat });
      this.draw(wt.body, model, 2, wt.tint, { level: wt.level, mat: [0, 0, 0, wt.glow || 0] });
      gl.depthFunc(gl.LEQUAL);
      for (const g of wt.through) this.draw(g, model, 0, [1, 1, 1, 0.55]);
      gl.depthFunc(gl.LESS);
      const ripple = st.drop >= 0.5 && st.drop < 1 ? [((st.drop - 0.5) / 0.5) * 0.9, 0.9 * (1 - (st.drop - 0.5) / 0.5), 1] : [0, 0, 1];
      this.draw(DISC(), mat(model, scaleAt(wt.r, wt.level)), 3, wt.topTint, { ripple, mat: [0, 0, 0, wt.glow || 0] });
      for (const l of parts.lights || []) this.draw(DISC(), mat(model, scaleAt(l.r, l.y)), 7, [...l.col, l.a]);
      if (st.drop > 0 && st.drop < 0.55) {
        const [dx, dz, top, bottom] = parts.drop, y = lerp(top, bottom, Math.pow(st.drop / 0.55, 2));
        this.draw(DROP(), mat(model, [0.028, 0, 0, 0, 0, 0.028, 0, 0, 0, 0, 0.028, 0, dx, y, dz, 1]), 2, [0.5, 0.78, 1, 0.6]);
      }
      this.draw(parts.glass.geo, model, 1, parts.glass.tint, { side: 1, mat: parts.glass.mat });
    }
    gl.blendFunc(gl.ONE, gl.ONE);
    for (const pt of pots) for (const o of pt.parts.glow || []) this.draw(o.geo, pt.model, 5, o.tint);
    const halos = pots.flatMap((pt) => (pt.parts.halos || []).map((hl) => ({ ...hl, model: pt.model })));
    if (halos.length) {
      // Camera-facing squares, made in the world's space each frame, in one buffer.
      const right = unit([view[0], view[4], view[8]]), up = unit([view[1], view[5], view[9]]), g = new Geo();
      for (const hl of halos) {
        const m = hl.model, c = [m[0] * hl.p[0] + m[8] * hl.p[2] + m[12], hl.p[1], m[2] * hl.p[0] + m[10] * hl.p[2] + m[14]];
        const base = g.n;
        for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) g.vert(add(c, add(mul3(right, a * hl.r), mul3(up, b * hl.r))), [0, 0, 1], [a, b, 0]);
        g.i.push(base, base + 1, base + 2, base, base + 2, base + 3);
      }
      // Each halo its own tint: drawn in runs of the same colour.
      let start = 0;
      while (start < halos.length) {
        let end = start + 1;
        const c = halos[start].col;
        while (end < halos.length && halos[end].col.every((v, k) => v === c[k])) end++;
        const part = new Geo();
        part.v = g.v.slice(start * 36, end * 36);
        for (let q = 0; q < end - start; q++) part.i.push(q * 4, q * 4 + 1, q * 4 + 2, q * 4, q * 4 + 2, q * 4 + 3);
        this.draw(part, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], 6, c, { scratch: true });
        start = end;
      }
    }
    gl.depthMask(true);
    if (!this.el.classList.contains("gd-3d")) this.el.classList.add("gd-3d");
  }
}

// ---------------------------------------------------------------- the page
function main() {
  if (!scenes.length) return;
  readTopbar();
  const live = [];
  let frame = 0;
  const tick = () => {
    frame = 0;
    let again = false;
    for (const s of live) {
      if (!s.visible || !s.ready) continue;
      const target = s.progress();
      const step = moving ? target - s.shown : 0;
      if (Math.abs(step) > 0.0005) {
        s.shown += step * 0.22;
        again = true;
      } else {
        s.shown = target;
      }
      if (s.dirty || Math.abs(step) > 0 || s.drawn !== s.shown) {
        s.render();
        s.drawn = s.shown;
        s.dirty = false;
      }
    }
    if (again) frame = requestAnimationFrame(tick);
  };
  const wake = () => {
    if (!frame) frame = requestAnimationFrame(tick);
  };
  const seen = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const s = e.target.gdScene;
      s.visible = e.isIntersecting;
      if (s.visible && !s.started) {
        s.started = true;
        try {
          if (!s.start()) continue;
        } catch {
          continue;
        }
        s.shown = s.progress();
        live.push(s);
      }
      s.dirty = true;
    }
    wake();
  }, { rootMargin: "240px 0px" });
  for (const el of scenes) {
    el.gdScene = new Scene(el);
    seen.observe(el);
  }
  const redraw = () => {
    for (const s of live) s.dirty = true;
    wake();
  };
  if (moving) addEventListener("scroll", wake, { passive: true });
  addEventListener("resize", () => {
    readTopbar();
    redraw();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", redraw);
  new MutationObserver(redraw).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
}

main();
