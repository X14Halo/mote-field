// MoteField randomness and the dust's starting state: the JavaScript twin of motefield/rng.py.
//
// Every value here must come out bit for bit the same as the Python. The rules that make that true:
//   * the hash is whole-number arithmetic (Math.imul and >>> 0 give exact uint32 wrap-around);
//   * uniform numbers keep 24 bits, so they are exact as float32;
//   * exp, log and pow are built from + - * / and sqrt only, in the same order as the Python (IEEE 754 makes each of
//     those exact), and every result is rounded to float32 with Math.fround, as numpy's astype(float32) does.
// Change one side and the other in the same commit; tests/test_rng_parity.py checks them against each other.

export const M32 = 0xffffffff;
export const U24 = 1.0 / 16777216.0;
export const DUST = 0x44555354, MODES = 0x4d4f4445, EMBER = 0x454d4252;
export const GRIT = 0, FLAKE = 1, FIBER = 2, FLUFF = 3;
export const H_POS = 0, H_ATTR = 3, N_ATTR = 10;
export const MODE_SETS = [[0, 40], [1, 20]];

// ---- the hash ------------------------------------------------------------------------------------------------------
export function pcg(v) {
  v = (Math.imul(v >>> 0, 747796405) + 2891336453) >>> 0;
  const w = Math.imul(((v >>> ((v >>> 28) + 4)) ^ v) >>> 0, 277803737) >>> 0;
  return ((w >>> 22) ^ w) >>> 0;
}

export function hashWords(...words) {
  let h = pcg(words[0] >>> 0);
  for (let i = 1; i < words.length; i++) h = pcg((h + (words[i] >>> 0)) >>> 0);
  return h;
}

export function uniform(h) { return (h >>> 8) * U24; }

// ---- exp, log, pow from + - * / only -------------------------------------------------------------------------------
const LN2_HI = 6.93147180369123816490e-01, LN2_LO = 1.90821492927058770002e-10, INV_LN2 = 1.44269504088896338700e+00;
const SQRT_HALF = 0.7071067811865476;
const EXP_N = 16;
const EXP_C = [1.0];
{ let f = 1; for (let k = 1; k <= EXP_N; k++) { f *= k; EXP_C.push(1.0 / f); } }
const LOG_C = []; for (let i = 0; i < 11; i++) LOG_C.push(1.0 / (2 * i + 1));

const _dv = new DataView(new ArrayBuffer(8));
function pow2(n) {               // exactly 2^n, for -1022 <= n <= 1023
  _dv.setUint32(0, ((n + 1023) << 20) >>> 0); _dv.setUint32(4, 0);
  return _dv.getFloat64(0);
}
function ldexp(x, n) {           // x * 2^n, exact (split so neither factor overflows)
  if (n > 1023) return x * pow2(1023) * pow2(n - 1023);
  if (n < -1022) return x * pow2(-1022) * pow2(n + 1022);
  return x * pow2(n);
}
function frexp(x) {              // [m, e] with x = m * 2^e and 0.5 <= m < 1, for finite x > 0 (like Python's math.frexp)
  _dv.setFloat64(0, x);
  const hi = _dv.getUint32(0), E = (hi >>> 20) & 0x7ff;
  if (E === 0) { const r = frexp(x * pow2(54)); return [r[0], r[1] - 54]; }
  _dv.setUint32(0, ((hi & 0x800fffff) | (1022 << 20)) >>> 0);
  return [_dv.getFloat64(0), E - 1022];
}

const EXP_RANGE = 700.0;          // exp's input is clamped to +-700, as in the Python
export function xexp(x) {
  x = Math.min(Math.max(x, -EXP_RANGE), EXP_RANGE);
  const n = Math.floor(x * INV_LN2 + 0.5);
  const r = (x - n * LN2_HI) - n * LN2_LO;
  let p = EXP_C[EXP_N];
  for (let k = EXP_N - 1; k >= 0; k--) p = p * r + EXP_C[k];
  return ldexp(p, n);
}

export function xlog(x) {
  let [m, e] = frexp(x);
  if (m < SQRT_HALF) { m = m * 2.0; e = e - 1; }
  const s = (m - 1.0) / (m + 1.0), z = s * s;
  let p = LOG_C[10];
  for (let k = 9; k >= 0; k--) p = p * z + LOG_C[k];
  return e * LN2_HI + (e * LN2_LO + (2.0 * s) * p);
}

export function xpow(a, b) { return a <= 0.0 ? 0.0 : xexp(b * xlog(a)); }

function gauss(words) {          // Irwin-Hall: 12 hashed uniforms, exact arithmetic
  let s = 0.0;
  for (let i = 0; i < 12; i++) s = s + uniform(hashWords(...words, i));
  return s - 6.0;
}

// ---- the swirling air ----------------------------------------------------------------------------------------------
// count modes of set `which` as a Float32Array (count * 8): K(3) for a 1 m eddy, A(3), phase, angular speed for 1 m.
export function modeTable(seed, which, count) {
  const out = new Float32Array(count * 8);
  const amp = Math.sqrt(6.0 / count), twoPi = 2.0 * Math.PI;
  for (let m = 0; m < count; m++) {
    const base = [seed >>> 0, MODES, which * 1024 + m];
    let v, r2;
    for (let attempt = 0; attempt < 64; attempt++) {
      v = [0, 1, 2].map((c) => uniform(hashWords(...base, attempt * 3 + c)) * 2.0 - 1.0);
      r2 = (v[0] * v[0] + v[1] * v[1]) + v[2] * v[2];
      if (1e-6 < r2 && r2 <= 1.0) break;
    }
    const r = Math.sqrt(r2);
    const k = [v[0] / r, v[1] / r, v[2] / r];
    const kmag = twoPi * xexp(0.35 * gauss([...base, 200]));
    let a, al;
    for (let attempt = 0; attempt < 64; attempt++) {
      a = [0, 1, 2].map((c) => gauss([...base, 300 + attempt * 3 + c]));
      const d = (a[0] * k[0] + a[1] * k[1]) + a[2] * k[2];
      a = [a[0] - k[0] * d, a[1] - k[1] * d, a[2] - k[2] * d];
      al = Math.sqrt((a[0] * a[0] + a[1] * a[1]) + a[2] * a[2]);
      if (al > 1e-3) break;
    }
    const phase = uniform(hashWords(...base, 400)) * twoPi;
    const omega = (0.4 + 0.6 * uniform(hashWords(...base, 401))) * kmag;
    out.set([k[0] * kmag, k[1] * kmag, k[2] * kmag, a[0] / al * amp, a[1] / al * amp, a[2] / al * amp, phase, omega],
            m * 8);
  }
  return out;
}

export function modeTables(seed) { return MODE_SETS.map(([which, count]) => modeTable(seed, which, count)); }

// ---- the dust's starting state -------------------------------------------------------------------------------------
// e: the engine values (Settings.engine() in Python: the master dials already applied).
export function moteCount(e) {
  const su = e.scene_unit;
  const size = e.box_size.map((s) => Math.max(s * su, 0.02));
  const vol = (size[0] * size[1]) * size[2];
  return [Math.max(0, Math.min(Math.trunc(e.max_motes), Math.floor(e.density * vol + 0.5))), size];
}

export function dustStart(e, n) {
  const [count, size] = moteCount(e);
  if (n === undefined) n = count;
  const local = new Float32Array(n * 3), h = new Float32Array(n * N_ATTR), kind = new Uint8Array(n);
  const settle = new Float32Array(n), tau = new Float32Array(n), gritD = new Float32Array(n);
  const seed = e.seed >>> 0;
  const fFib = e.fibers / 100.0, fFlf = e.fluff / 100.0, fFlk = e.flakes / 100.0;
  const smin = e.size_min, ratio = e.size_max / Math.max(smin, 1e-3), bias = e.size_bias;
  const u = new Float64Array(H_ATTR + N_ATTR);
  for (let i = 0; i < n; i++) {
    const hb = hashWords(seed, DUST, i);
    for (let k = 0; k < H_ATTR + N_ATTR; k++) u[k] = uniform(pcg((hb + k) >>> 0));
    for (let c = 0; c < 3; c++) local[i * 3 + c] = Math.fround((u[H_POS + c] - 0.5) * size[c]);
    for (let k = 0; k < N_ATTR; k++) h[i * N_ATTR + k] = u[H_ATTR + k];     // 24-bit: exact as float32
    const h0 = u[H_ATTR];
    const kd = h0 < fFib ? FIBER : h0 < fFib + fFlf ? FLUFF : h0 < (fFib + fFlf) + fFlk ? FLAKE : GRIT;
    kind[i] = kd;
    const d = smin * xpow(ratio, xpow(u[H_ATTR + 1], bias));
    const q = d / 50.0;
    settle[i] = kd === GRIT ? 0.15 + Math.min(q * q, 4.0) : kd === FLAKE ? 0.5 : kd === FIBER ? 0.22 : 0.12;
    tau[i] = kd >= FIBER ? 0.22 : 0.35;
    gritD[i] = kd === GRIT ? d : 0.0;
  }
  return { n, size, local, h, kind, settle, tau, grit_d: gritD };
}
