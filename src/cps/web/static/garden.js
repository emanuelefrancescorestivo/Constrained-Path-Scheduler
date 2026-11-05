// How it works (D117): the garden in WebGL. Each `.gd-scene[data-scene]` carries the
// server's numbers for its plants (the water at each stop of the scene, the roots'
// length, when a drop falls, when a plant wilts or flowers) and a flat drawing of
// them; where WebGL runs, this draws the same plants in 3D over the flat one.
//
// A topic is a hyacinth in a glass: the water is how much is remembered, the roots
// how long it lasts. The glasses, the bulbs and the table are surfaces of revolution
// (a profile turned about its axis), the stems and roots tubes swept along curves,
// the leaves curved ribbons, the buds and flowers spheres. One small shader lights
// them: a sky and a ground, a key light, glass by its angle to the eye (Fresnel).
//
// A frame is drawn only when something changed: the page scrolled, a scene came into
// view, the page was resized or its theme changed. A scene eases to where the page is
// and stops (D93: nothing runs while the page is still). With reduced motion, or
// where the browser does not tie animations to scrolling, each scene is drawn once,
// complete, as its flat drawing is. Layout only: every number is the server's.

const root = document.documentElement;
const scenes = [...document.querySelectorAll(".gd-scene[data-scene]")];

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (t) => t * t * (3 - 2 * t);
const span = (p, r) => (r ? clamp((p - r[0]) / (r[1] - r[0])) : 1);
const mix3 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (a) => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
const hash = (n) => {
  const x = Math.sin(n * 12.9898) * 43758.5453;
  return x - Math.floor(x);
};

// ---------------------------------------------------------------- the shapes
// Profiles are [radius, height] pairs, a glass one unit tall. The glass is a hyacinth
// glass: a round bowl for the water, a narrow neck, a cup that holds the bulb.
const GLASS = [
  [0.0, 0.0], [0.16, 0.0], [0.215, 0.014], [0.25, 0.06], [0.278, 0.16], [0.29, 0.27],
  [0.272, 0.39], [0.222, 0.5], [0.152, 0.6], [0.112, 0.672], [0.108, 0.725], [0.14, 0.79],
  [0.196, 0.86], [0.222, 0.93], [0.228, 0.992],
];
// The inside of the bowl and neck, where the water is: the glass less its thickness.
const INSIDE = [
  [0.0, 0.035], [0.15, 0.035], [0.2, 0.045], [0.236, 0.085], [0.264, 0.165], [0.276, 0.27],
  [0.258, 0.39], [0.208, 0.5], [0.139, 0.6], [0.099, 0.672], [0.095, 0.725],
];
const BULB = [
  [0.0, -0.125], [0.06, -0.118], [0.115, -0.075], [0.142, 0.0], [0.128, 0.075], [0.085, 0.13],
  [0.045, 0.17], [0.03, 0.2], [0.0, 0.215],
];
const BULB_AT = 0.835;
const DROP = [[0.0, -1.0], [0.55, -0.85], [0.95, -0.35], [0.85, 0.25], [0.45, 0.85], [0.0, 1.7]];
const LOW = 0.04; // the water's height when empty, and its range up the glass
const HIGH = 0.66;
const ROOT_TOP = 0.715;
const ROOT_DEPTH = 0.66;

// Catmull-Rom through the points: a smooth curve, `per` samples a segment.
function spline(pts, per) {
  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    for (let s = 0; s < per; s++) {
      const t = s / per, t2 = t * t, t3 = t2 * t;
      out.push([0, 1].map((d) => 0.5 * (2 * p1[d] + (p2[d] - p0[d]) * t
        + (2 * p0[d] - 5 * p1[d] + 4 * p2[d] - p3[d]) * t2 + (3 * p1[d] - p0[d] - 3 * p2[d] + p3[d]) * t3)));
    }
  }
  out.push(pts[pts.length - 1].slice());
  return out;
}

const INSIDE_CURVE = spline(INSIDE, 8);
function insideAt(y) {
  const c = INSIDE_CURVE;
  if (y <= c[1][1]) return c[1][0];
  for (let i = 1; i < c.length; i++) {
    if (c[i][1] >= y) {
      const t = (y - c[i - 1][1]) / Math.max(1e-6, c[i][1] - c[i - 1][1]);
      return lerp(c[i - 1][0], c[i][0], t);
    }
  }
  return c[c.length - 1][0];
}
const levelOf = (zq) => LOW + (clamp(zq, 0, 20) / 20) * (HIGH - LOW);

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
  // A grid of (rows + 1) x (cols + 1) vertices from `base`, as triangles.
  grid(base, rows, cols) {
    for (let j = 0; j < rows; j++) {
      for (let k = 0; k < cols; k++) {
        const a = base + j * (cols + 1) + k, b = a + cols + 1;
        this.i.push(a, b, a + 1, a + 1, b, b + 1);
      }
    }
  }
}

// A profile turned about the vertical axis; `at` moves it, `col` is a colour or a
// colour along the profile (0 at its start, 1 at its end).
function lathe(g, prof, seg, col, at = [0, 0, 0], scale = 1) {
  const base = g.n, m = prof.length;
  for (let j = 0; j < m; j++) {
    const a = prof[Math.max(0, j - 1)], b = prof[Math.min(m - 1, j + 1)];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const nr = (b[1] - a[1]) / l, ny = -(b[0] - a[0]) / l;
    for (let k = 0; k <= seg; k++) {
      const t = (k / seg) * Math.PI * 2, cs = Math.cos(t), sn = Math.sin(t);
      const r = prof[j][0] * scale;
      const c = typeof col === "function" ? col(j / (m - 1), t) : col;
      g.vert([at[0] + r * cs, at[1] + prof[j][1] * scale, at[2] + r * sn], [nr * cs, ny, nr * sn], c);
    }
  }
  g.grid(base, m - 1, seg);
}

// A tube along a curve, its radius at each point; the frame carried along the curve
// so that the tube does not twist.
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
    const bb = cross(t, nn);
    const c = typeof col === "function" ? col(i / (n - 1)) : col;
    for (let k = 0; k <= sides; k++) {
      const a = (k / sides) * Math.PI * 2, cs = Math.cos(a), sn = Math.sin(a);
      const dir = [nn[0] * cs + bb[0] * sn, nn[1] * cs + bb[1] * sn, nn[2] * cs + bb[2] * sn];
      const r = rads[i];
      g.vert([pts[i][0] + dir[0] * r, pts[i][1] + dir[1] * r, pts[i][2] + dir[2] * r], dir, c);
    }
  }
  g.grid(base, n - 1, sides);
}

// A sphere, stretched to (rx, ry, rz), from a unit sphere made once (a finer one for the
// buds, a coarser one for the petals, which are small).
const unitSphere = (lat, lon) => {
  const v = [];
  for (let j = 0; j <= lat; j++) {
    const th = (j / lat) * Math.PI, y = -Math.cos(th), r = Math.sin(th);
    for (let k = 0; k <= lon; k++) {
      const a = (k / lon) * Math.PI * 2;
      v.push([r * Math.cos(a), y, r * Math.sin(a)]);
    }
  }
  return { v, lat, lon };
};
const SPHERE = unitSphere(8, 12);
const PETAL = unitSphere(5, 8);
function sphere(g, at, rx, ry, rz, col) {
  const base = g.n;
  for (const p of SPHERE.v) {
    g.vert([at[0] + p[0] * rx, at[1] + p[1] * ry, at[2] + p[2] * rz], unit([p[0] / rx, p[1] / ry, p[2] / rz]), col);
  }
  g.grid(base, SPHERE.lat, SPHERE.lon);
}
// An ellipsoid along three perpendicular axes (each a direction times a half-length).
function ellipsoid(g, at, ex, ey, ez, col, tmpl = PETAL) {
  const base = g.n, lx = dot(ex, ex), ly = dot(ey, ey), lz = dot(ez, ez);
  for (const p of tmpl.v) {
    g.vert(
      [at[0] + ex[0] * p[0] + ey[0] * p[1] + ez[0] * p[2], at[1] + ex[1] * p[0] + ey[1] * p[1] + ez[1] * p[2],
        at[2] + ex[2] * p[0] + ey[2] * p[1] + ez[2] * p[2]],
      unit([ex[0] * p[0] / lx + ey[0] * p[1] / ly + ez[0] * p[2] / lz, ex[1] * p[0] / lx + ey[1] * p[1] / ly
        + ez[1] * p[2] / lz, ex[2] * p[0] / lx + ey[2] * p[1] / ly + ez[2] * p[2] / lz]),
      col,
    );
  }
  g.grid(base, tmpl.lat, tmpl.lon);
}
const scaled = (v, k) => [v[0] * k, v[1] * k, v[2] * k];

// A leaf: a ribbon along a curve that leaves the bulb nearly upright and bends out and
// down along its length; `bend` is how far it has turned at the tip. Its edges rise a
// little, as a hyacinth's leaf is a shallow channel.
function leaf(g, o, ang, len, wid, tilt, bend, fold, cBase, cTip) {
  const d = [Math.cos(ang), 0, Math.sin(ang)];
  const side = [-d[2], 0, d[0]];
  const rows = 18, cols = 4, base = g.n;
  let p = o.slice();
  for (let i = 0; i <= rows; i++) {
    const s = i / rows, th = tilt + bend * (0.35 * s + 0.65 * s * s);
    const t = [Math.sin(th) * d[0], Math.cos(th), Math.sin(th) * d[2]];
    const up = unit(cross(side, t));
    const w = wid * Math.pow(Math.sin(Math.PI * Math.min(1, 0.07 + s * 0.93)), 0.6) * (1 - 0.25 * s);
    const c = mix3(cBase, cTip, s);
    for (let j = 0; j <= cols; j++) {
      const v = -1 + (2 * j) / cols, lift = fold * w * v * v, slope = 2 * fold * v;
      g.vert(
        [p[0] + side[0] * v * w + up[0] * lift, p[1] + up[1] * lift, p[2] + side[2] * v * w + up[2] * lift],
        unit([up[0] - side[0] * slope, up[1], up[2] - side[2] * slope]),
        c,
      );
    }
    p = [p[0] + (t[0] * len) / rows, p[1] + (t[1] * len) / rows, p[2] + (t[2] * len) / rows];
  }
  g.grid(base, rows, cols);
}

// ---------------------------------------------------------------- the plant
// One plant at one moment: `thirst` 0 (full) to 1 (dry), `wilt` 0 to 1, `roots` the
// share of their full length, `bloom` 0 (green buds) to 1 (in flower).
function plant(state, seed, pal) {
  const g = new Geo(), under = new Geo();
  const { thirst, wilt, roots, bloom } = state;
  const droop = thirst + 0.8 * wilt;
  const dry = clamp(thirst * 2.4 + wilt * 0.8);
  const deep = mix3(pal.leafDeep, pal.dryDeep, dry), tip = mix3(pal.leaf, pal.dry, dry);
  // Leaves, around the bulb's top at the golden angle.
  for (let i = 0; i < 7; i++) {
    const ang = seed * 1.7 + i * 2.39996;
    const len = 0.64 + 0.3 * hash(seed * 7 + i);
    const tilt = 0.14 + 0.16 * hash(seed + i * 3) + droop * 0.4;
    const bend = 0.75 + 0.5 * hash(seed * 3 + i) + droop * 2.3;
    const o = [Math.cos(ang) * 0.035, BULB_AT + 0.17, Math.sin(ang) * 0.035];
    leaf(g, o, ang, len, 0.078 + 0.016 * hash(i + seed), tilt, bend, 0.32 * (1 - 0.6 * droop), deep, tip);
  }
  // The flower stalk, bowing as the plant dries, longer as it flowers; along its top,
  // green buds that open into star-shaped flowers, six petals each.
  const sa = seed * 2.3 + 0.6, sd = [Math.cos(sa), 0, Math.sin(sa)];
  const stem = [], rads = [], ups = [];
  let p = [0, BULB_AT + 0.13, 0];
  const hs = 0.44 + 0.3 * bloom - 0.08 * droop;
  for (let i = 0; i <= 16; i++) {
    const s = i / 16, th = droop * 1.3 * s * s;
    const t = [Math.sin(th) * sd[0], Math.cos(th), Math.sin(th) * sd[2]];
    stem.push(p);
    ups.push(t);
    rads.push(lerp(0.024, 0.016, s));
    p = [p[0] + (t[0] * hs) / 16, p[1] + (t[1] * hs) / 16, p[2] + (t[2] * hs) / 16];
  }
  tube(g, stem, rads, 7, (s) => mix3(deep, tip, s));
  const flower = pal.flowers[seed % pal.flowers.length];
  const budCol = mix3(pal.bud, tip, dry), n = 34;
  for (let k = 0; k < n; k++) {
    const s = 0.4 + (0.6 * k) / (n - 1), at = stem[Math.round(s * 16)], up = ups[Math.round(s * 16)];
    const a = k * 2.39996 + seed, taper = 1 - 0.45 * (s - 0.4) / 0.6;
    // Out from the stalk, a little upward; u and v span the flower's face.
    const o = unit([Math.cos(a), 0.35, Math.sin(a)]);
    const u = unit(cross(o, up)), v = cross(o, u);
    const open = smooth(clamp(bloom * 1.3 - (s - 0.4) * 0.5));
    const r = lerp(0.022, 0.068, open) * taper;
    const c = mix3(budCol, flower, open);
    const off = 0.02 + r * 0.55;
    const at2 = [at[0] + o[0] * off, at[1] + o[1] * off, at[2] + o[2] * off];
    if (open < 0.08) {
      sphere(g, at2, r * 0.85, r, r * 0.85, c);
      continue;
    }
    for (let q = 0; q < 6; q++) {
      const th = (q / 6) * Math.PI * 2 + k;
      const d = [u[0] * Math.cos(th) + v[0] * Math.sin(th), u[1] * Math.cos(th) + v[1] * Math.sin(th),
        u[2] * Math.cos(th) + v[2] * Math.sin(th)];
      const side = cross(o, d), len = r * 0.62;
      const c0 = [at2[0] + d[0] * len * 0.9 - o[0] * r * 0.1, at2[1] + d[1] * len * 0.9 - o[1] * r * 0.1,
        at2[2] + d[2] * len * 0.9 - o[2] * r * 0.1];
      ellipsoid(g, c0, scaled(d, len), scaled(side, r * 0.3), scaled(o, r * 0.14), mix3(c, [1, 1, 1], 0.12 * (q % 2)));
    }
    sphere(g, at2, r * 0.3, r * 0.3, r * 0.3, mix3(c, [1, 0.95, 0.7], 0.55));
  }
  // Roots: from the bulb's base down through the neck, spreading in the bowl.
  const length = clamp(roots, 0.03, 1);
  for (let i = 0; i < 11; i++) {
    const a0 = i * 2.39996 + seed * 0.7, spread = 0.45 + 0.5 * hash(i * 5 + seed);
    const pts = [], rr = [], steps = Math.max(3, Math.round(24 * length));
    for (let k = 0; k <= steps; k++) {
      const u = (length * k) / steps, y = ROOT_TOP - u * ROOT_DEPTH * (0.9 + 0.1 * hash(i + 2 * seed));
      const room = insideAt(y) - 0.018;
      const r = Math.min(room, 0.02 + room * spread * smooth(clamp((u - 0.1) / 0.55)));
      const a = a0 + 0.45 * Math.sin(u * 7 + i * 1.3);
      pts.push([r * Math.cos(a), y, r * Math.sin(a)]);
      rr.push(0.0105 * (1 - 0.7 * (k / steps)) + 0.002);
    }
    tube(under, pts, rr, 6, (s) => mix3(pal.root, pal.rootTip, s));
  }
  return { shoot: g, roots: under };
}

function staticGeo(builder) {
  const g = new Geo();
  builder(g);
  return g;
}

// ---------------------------------------------------------------- matrices
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function lookAt(eye, at) {
  const z = unit(sub(eye, at)), x = unit(cross([0, 1, 0], z)), y = cross(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
    -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}
function mul(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}
// Scaled by (sx, sy, sz), set at (x, y, z), and the whole turned by `turn` about the
// vertical axis, as a thing standing on a turntable.
function place(turn, x, y, z, sx = 1, sy = sx, sz = sx) {
  const c = Math.cos(turn), s = Math.sin(turn);
  return [c * sx, 0, -s * sx, 0, 0, sy, 0, 0, s * sz, 0, c * sz, 0, c * x + s * z, y, -s * x + c * z, 1];
}

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
uniform vec4 uTint; uniform float uMode; uniform float uSide; uniform float uLevel; uniform vec2 uRipple;
void main() {
  vec3 N = normalize(vN);
  vec3 V = normalize(uEye - vW);
  vec3 col; float a = 1.0;
  if (uMode < 0.5) {
    // Leaves, stems, roots, bulbs, buds: matte, lit on either face.
    if (dot(N, V) < 0.0) N = -N;
    vec3 base = vC * uTint.rgb;
    float wrap = max((dot(N, uKey) + 0.35) / 1.35, 0.0);
    vec3 hemi = mix(uGround, uSky, N.y * 0.5 + 0.5);
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), 36.0) * 0.22;
    float rim = pow(1.0 - max(dot(N, V), 0.0), 3.0) * 0.22;
    col = base * (hemi + wrap * uLight) + sp + rim * uSky;
    a = uTint.a;
    gl_FragColor = vec4(col * a, a);
    return;
  } else if (uMode < 1.5) {
    // Glass: drawn twice, the far side first (uSide -1), then the near (uSide 1).
    float f = dot(N, V);
    if (f * uSide < 0.0) discard;
    if (uSide < 0.0) N = -N;
    float fr = pow(1.0 - abs(f), 2.2);
    vec3 R = reflect(-V, N);
    float band = smoothstep(0.18, 0.26, R.x) * (1.0 - smoothstep(0.34, 0.44, R.x)) * smoothstep(-0.2, 0.35, R.y);
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), 90.0) * 1.4 + pow(max(dot(N, normalize(uFill + V)), 0.0), 24.0) * 0.3;
    float hi = sp + band * (uSide > 0.0 ? 0.55 : 0.18);
    a = clamp(uTint.a + fr * 0.5, 0.0, 1.0);
    col = uTint.rgb * mix(uGround, uSky, N.y * 0.5 + 0.5) * a + vec3(hi);
    gl_FragColor = vec4(col, clamp(a + hi * 0.6, 0.0, 1.0));
    return;
  } else if (uMode < 2.5) {
    // The water in the bowl, up to its level.
    if (vL.y > uLevel) discard;
    if (dot(N, V) < 0.0) discard;
    float fr = pow(1.0 - dot(N, V), 2.0);
    float depth = clamp((uLevel - vL.y) * 2.2, 0.0, 1.0);
    vec3 base = mix(uTint.rgb * 1.1, uTint.rgb * 0.72, depth);
    a = uTint.a + fr * 0.3;
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), 60.0) * 0.6;
    col = base * a + sp;
    gl_FragColor = vec4(col, clamp(a + sp * 0.4, 0.0, 1.0));
    return;
  } else if (uMode < 3.5) {
    // The water's surface: a disc, rippling where a drop has just fallen.
    float d = length(vL.xz);
    float x = d - uRipple.x;
    float wave = uRipple.y * cos(x * 48.0) * exp(-abs(x) * 9.0);
    vec3 n = normalize(vec3(0.0, 1.0, 0.0) + vec3(vL.x, 0.0, vL.z) / max(d, 0.001) * wave * 0.9);
    N = normalize((vec4(n, 0.0)).xyz);
    float edge = smoothstep(0.72, 1.0, d);
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), 70.0) * 1.1 + abs(wave) * 0.25;
    a = clamp(uTint.a + edge * 0.25, 0.0, 1.0);
    col = mix(uTint.rgb * 1.08, uTint.rgb * 0.85, edge) * a + sp;
    gl_FragColor = vec4(col, clamp(a + sp * 0.3, 0.0, 1.0));
    return;
  } else if (uMode < 4.5) {
    // The table: wood, its rings around the centre.
    float r = length(vL.xz);
    float wav = sin(atan(vL.z, vL.x) * 5.0 + r * 7.0) * 0.35 + sin(atan(vL.z, vL.x) * 2.0) * 0.5;
    float ring = smoothstep(0.6, 1.0, 0.5 + 0.5 * sin(r * 30.0 + wav));
    vec3 base = mix(uTint.rgb, uTint.rgb * 0.92, ring) * mix(0.94, 1.04, smoothstep(0.0, 1.0, r)) * vC;
    float lit = max(dot(N, uKey), 0.0);
    vec3 hemi = mix(uGround, uSky, N.y * 0.5 + 0.5);
    float sp = pow(max(dot(N, normalize(uKey + V)), 0.0), 28.0) * 0.12;
    col = base * (hemi + lit * uLight) + sp;
  } else {
    // A soft shadow on the table or under it.
    float d = length(vL.xz);
    a = pow(1.0 - smoothstep(0.0, 1.0, d), 1.6) * uTint.a;
    gl_FragColor = vec4(0.0, 0.0, 0.0, a);
    return;
  }
  gl_FragColor = vec4(col, a);
}`;

const PALETTES = {
  light: {
    sky: [0.66, 0.69, 0.74], ground: [0.42, 0.39, 0.36], light: [0.62, 0.6, 0.56],
    glass: [0.8, 0.92, 0.96, 0.05], water: [0.24, 0.58, 0.98, 0.42], top: [0.5, 0.78, 1.0, 0.55],
    wood: [0.8, 0.62, 0.44], shadow: 0.26, ground_shadow: 0.2,
  },
  dark: {
    sky: [0.5, 0.55, 0.66], ground: [0.22, 0.21, 0.25], light: [0.6, 0.58, 0.56],
    glass: [0.84, 0.94, 1.0, 0.07], water: [0.3, 0.62, 1.0, 0.44], top: [0.52, 0.8, 1.0, 0.56],
    wood: [0.72, 0.55, 0.39], shadow: 0.5, ground_shadow: 0.45,
  },
  leaf: [0.27, 0.66, 0.38], leafDeep: [0.12, 0.42, 0.24], dry: [0.78, 0.68, 0.38], dryDeep: [0.55, 0.45, 0.25],
  root: [0.96, 0.93, 0.85], rootTip: [0.99, 0.98, 0.94], bud: [0.52, 0.74, 0.4],
  bulb: [0.6, 0.42, 0.48], bulbTop: [0.88, 0.76, 0.66],
  flowers: [[0.42, 0.45, 0.95], [0.97, 0.46, 0.68], [0.99, 0.8, 0.36]],
};
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
    this.track = el.closest(".code-pin");
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
    if (s.t === "pin" && this.track) {
      const r = this.track.getBoundingClientRect();
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
    for (const name of ["uModel", "uView", "uEye", "uKey", "uFill", "uSky", "uGround", "uLight", "uTint", "uMode",
      "uSide", "uLevel", "uRipple"]) this.loc[name] = gl.getUniformLocation(prog, name);

    const pots = this.spec.pots;
    const n = pots.length, gap = n === 3 ? 0.8 : 0.92;
    this.xs = pots.map((_, i) => (i - (n - 1) / 2) * gap);
    this.table = (gap * (n - 1)) / 2 + 0.44;
    const R = this.table;
    this.meshes = {
      glass: this.mesh(staticGeo((g) => {
        lathe(g, spline(GLASS, 6), 56, [1, 1, 1]);
        const lip = [];
        for (let k = 0; k <= 12; k++) {
          const a = (k / 12) * Math.PI * 2 - Math.PI / 2;
          lip.push([0.222 + Math.cos(a) * 0.009, 0.992 + Math.sin(a) * 0.009]);
        }
        lathe(g, lip, 56, [1, 1, 1]);
      })),
      water: this.mesh(staticGeo((g) => lathe(g, INSIDE_CURVE, 48, [1, 1, 1]))),
      disc: this.mesh(staticGeo((g) => lathe(g, [[1, 0], [0, 0]], 48, [1, 1, 1]))),
      bulb: this.mesh(staticGeo((g) => lathe(g, spline(BULB, 5), 40, (t, a) => mix3(
        mix3(PALETTES.bulb, PALETTES.bulbTop, smooth(t)), PALETTES.bulbTop, 0.35 * Math.pow(0.5 + 0.5 * Math.cos(a * 7), 6),
      ), [0, BULB_AT, 0]))),
      table: this.mesh(staticGeo((g) => lathe(g, spline([[0, -0.092], [R - 0.06, -0.092], [R - 0.014, -0.08],
        [R, -0.046], [R - 0.012, -0.014], [R - 0.06, 0], [0, 0]], 5), 72, [1, 1, 1]))),
      drop: this.mesh(staticGeo((g) => lathe(g, spline(DROP, 4), 16, [1, 1, 1]))),
      shade: this.mesh(staticGeo((g) => lathe(g, [[1, 0], [0, 0]], 32, [0, 0, 0]))),
    };
    this.plants = pots.map(() => ({ shoot: this.mesh(null, true), roots: this.mesh(null, true), key: "" }));
    gl.enable(gl.DEPTH_TEST);
    gl.clearColor(0, 0, 0, 0);
    this.ready = true;
    return true;
  }

  mesh(geo, dynamic = false) {
    const gl = this.gl;
    const m = { vbo: gl.createBuffer(), ibo: gl.createBuffer(), count: 0, dynamic };
    if (geo) this.fill(m, geo);
    return m;
  }

  fill(m, geo) {
    const gl = this.gl, usage = m.dynamic ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(geo.v), usage);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(geo.i), usage);
    m.count = geo.i.length;
  }

  draw(m, model, mode, tint, extra = {}) {
    const gl = this.gl, L = this.loc;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.vbo);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ibo);
    gl.vertexAttribPointer(L.aPos, 3, gl.FLOAT, false, 36, 0);
    gl.vertexAttribPointer(L.aNrm, 3, gl.FLOAT, false, 36, 12);
    gl.vertexAttribPointer(L.aCol, 3, gl.FLOAT, false, 36, 24);
    gl.uniformMatrix4fv(L.uModel, false, model);
    gl.uniform1f(L.uMode, mode);
    gl.uniform4f(L.uTint, tint[0], tint[1], tint[2], tint[3] ?? 1);
    gl.uniform1f(L.uSide, extra.side ?? 1);
    gl.uniform1f(L.uLevel, extra.level ?? 9);
    gl.uniform2f(L.uRipple, ...(extra.ripple ?? [0, 0]));
    gl.drawElements(gl.TRIANGLES, m.count, gl.UNSIGNED_SHORT, 0);
  }

  // The camera: a little above the table, as close as it can be with the whole table
  // and every plant's top in the frame, whichever way the table has turned. Found by
  // projecting that outline and moving the camera until it fits, once for each shape
  // of canvas.
  camera(aspect) {
    if (this.cam && this.cam.aspect === aspect) return this.cam;
    const fy = (24 * Math.PI) / 180, el = 0.2, R = this.table;
    const reach = Math.max(...this.xs.map(Math.abs)) + 0.42;
    const top = this.spec.pots.some((pot) => pot.bloom) ? 1.8 : 1.66;
    const outline = [];
    for (let k = 0; k < 32; k++) {
      const a = (k / 32) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
      outline.push([c * R, 0, s * R], [c * R, -0.095, s * R], [c * reach, top, s * reach]);
    }
    const proj = perspective(fy, aspect, 0.1, 60);
    let dist = 6, ty = 0.75, eye, view;
    for (let it = 0; it < 8; it++) {
      eye = [0, ty + Math.sin(el) * dist, Math.cos(el) * dist];
      view = mul(proj, lookAt(eye, [0, ty, 0]));
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
      for (const q of outline) {
        const cw = view[3] * q[0] + view[7] * q[1] + view[11] * q[2] + view[15];
        const x = (view[0] * q[0] + view[4] * q[1] + view[8] * q[2] + view[12]) / cw;
        const y = (view[1] * q[0] + view[5] * q[1] + view[9] * q[2] + view[13]) / cw;
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
      ty += ((y0 + y1) / 2) * dist * Math.tan(fy / 2);
      dist *= Math.max((x1 - x0) / 2, (y1 - y0) / 2) / 0.92;
    }
    this.cam = { aspect, eye, view };
    return this.cam;
  }

  // Each plant's state at progress p, from the server's numbers.
  stateAt(pot, p) {
    let z = pot.z;
    if (Array.isArray(z)) {
      const q = p * (z.length - 1), i = Math.min(z.length - 2, Math.floor(q));
      z = lerp(z[i], z[i + 1], q - i);
    }
    const grow = pot.ra ? smooth(span(p, pot.ra)) : 1;
    const roots = lerp(pot.rf ?? pot.rt ?? 20, pot.rt ?? 20, grow) / 20;
    const wilt = pot.wilt ? smooth(span(p, pot.wilt)) : 0;
    const bloom = Array.isArray(pot.bloom) ? smooth(span(p, pot.bloom)) : pot.bloom ?? 0;
    const drop = pot.drop ? span(p, pot.drop) : 0;
    return { z, thirst: (20 - z) / 20, roots, wilt, bloom, drop };
  }

  render() {
    if (!this.ready) return;
    const gl = this.gl, canvas = this.canvas, L = this.loc;
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

    const pal = isDark() ? PALETTES.dark : PALETTES.light;
    const p = this.shown, spec = this.spec;
    const turn = ((spec.turn ? lerp(spec.turn[0], spec.turn[1], p) : -12) * Math.PI) / 180;

    const { eye, view } = this.camera(w / h);
    gl.uniformMatrix4fv(L.uView, false, view);
    gl.uniform3fv(L.uEye, eye);
    gl.uniform3fv(L.uKey, unit([-0.5, 0.85, 0.6]));
    gl.uniform3fv(L.uFill, unit([0.75, 0.25, -0.35]));
    gl.uniform3fv(L.uSky, pal.sky);
    gl.uniform3fv(L.uGround, pal.ground);
    gl.uniform3fv(L.uLight, pal.light);

    const states = spec.pots.map((pot) => this.stateAt(pot, p));
    const models = this.xs.map((x) => place(turn, x, 0, 0));

    // Opaque: the table, the bulbs, the plants.
    gl.disable(gl.BLEND);
    gl.depthMask(true);
    this.draw(this.meshes.table, place(turn, 0, 0, 0), 4, pal.wood);
    states.forEach((st, i) => {
      const key = [st.thirst, st.roots, st.wilt, st.bloom].map((v) => v.toFixed(3)).join();
      const pl = this.plants[i];
      if (pl.key !== key) {
        const made = plant(st, i + (spec.seed ?? 0), PALETTES);
        this.fill(pl.shoot, made.shoot);
        this.fill(pl.roots, made.roots);
        pl.key = key;
      }
      this.draw(this.meshes.bulb, models[i], 0, [1, 1, 1]);
      this.draw(pl.shoot, models[i], 0, [1, 1, 1]);
      this.draw(pl.roots, models[i], 0, [1, 1, 1]);
    });

    // See-through, the farthest glass first: shadows, then each glass's far side, its
    // water, a falling drop, and its near side.
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    this.draw(this.meshes.shade, place(turn, 0, -0.094, 0, this.table * 1.22, 1, this.table * 1.22), 5, [0, 0, 0, pal.ground_shadow]);
    models.forEach((m) => this.draw(this.meshes.shade, mul(m, place(0, 0, 0.003, 0, 0.42)), 5, [0, 0, 0, pal.shadow]));
    // Farthest first: a glass's depth toward the camera is -sin(turn) x.
    const order = this.xs.map((x, i) => i).sort((a, b) => Math.sin(turn) * (this.xs[b] - this.xs[a]));
    for (const i of order) {
      const m = models[i], st = states[i], level = levelOf(st.z);
      this.draw(this.meshes.glass, m, 1, pal.glass, { side: -1 });
      this.draw(this.meshes.water, m, 2, pal.water, { level });
      gl.depthFunc(gl.LEQUAL);
      this.draw(this.plants[i].roots, m, 0, [1, 1, 1, 0.55]);
      gl.depthFunc(gl.LESS);
      const falling = st.drop > 0 && st.drop < 0.55;
      const ripple = st.drop >= 0.5 && st.drop < 1 ? [(st.drop - 0.5) / 0.5 * 0.9, 0.9 * (1 - (st.drop - 0.5) / 0.5)] : [0, 0];
      const r = insideAt(level) - 0.002;
      this.draw(this.meshes.disc, mul(m, place(0, 0, level, 0, r, 1, r)), 3, pal.top, { ripple });
      if (falling) {
        const y = lerp(1.75, 0.98, Math.pow(st.drop / 0.55, 2));
        this.draw(this.meshes.drop, mul(m, place(0, 0.17, y, 0.04, 0.05)), 2, pal.top, { level: 9 });
      }
      this.draw(this.meshes.glass, m, 1, pal.glass, { side: 1 });
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
