"""Randomness that comes out the same everywhere: in numpy, in plain Python, in JavaScript and on the GPU.

Every random number is an integer hash (PCG) of what it is for: (seed, what kind of thing, which one, which number).
There is no random stream to keep in step, so:

  * any mote or ember can be made on its own, in any order, on any machine;
  * the viewer builds the dust's starting state itself, without waiting for Python;
  * raising the density adds motes and leaves the existing ones where they were (mote i is always mote i).

Uniform numbers keep 24 bits, so they are exact in 32-bit floats too. The few values that need exp, log or pow (the
swirl tables, grit sizes) use the functions below, built from + - * / and sqrt only. IEEE 754 makes those exact in every
language, so Python and JavaScript agree bit for bit; each result is then rounded to 32 bits, which is what both
renderers use. viewer/js/mfseed.js is the JavaScript twin of this file; tests/test_rng_parity.py checks they agree.
"""

import math

import numpy as np

M32 = 0xFFFFFFFF
U24 = 1.0 / 16777216.0

# what a random number is for (the second word of every hash)
DUST = 0x44555354        # 'DUST'
MODES = 0x4D4F4445       # 'MODE'
EMBER = 0x454D4252       # 'EMBR'

# mote kinds
GRIT, FLAKE, FIBER, FLUFF = 0, 1, 2, 3
KIND_NAMES = ('grit', 'flake', 'fibre', 'fluff')

# streams of each mote's hash: position x y z, then the attributes h0..h9
H_POS = 0
H_ATTR = 3
N_ATTR = 10

# the swirling air: 40 large modes and 20 small ones
MODE_SETS = ((0, 40), (1, 20))


# ---------------------------------------------------------------------------------------------------------------------
#  The hash
# ---------------------------------------------------------------------------------------------------------------------
def pcg(v):
    """PCG hash of a whole number, uint32 -> uint32 (plain Python ints)."""
    v = (v * 747796405 + 2891336453) & M32
    w = (((v >> ((v >> 28) + 4)) ^ v) * 277803737) & M32
    return ((w >> 22) ^ w) & M32


def hash_words(*words):
    """One uint32 from several: h = pcg(w0), then h = pcg(h + w) for each word after."""
    h = pcg(words[0] & M32)
    for w in words[1:]:
        h = pcg((h + w) & M32)
    return h


def uniform(h):
    """0 <= u < 1 from a hash, keeping 24 bits (exact in float32)."""
    return (h >> 8) * U24


_C1, _C2, _C3 = np.uint32(747796405), np.uint32(2891336453), np.uint32(277803737)


def pcg_np(v):
    """PCG hash of a uint32 array (wraps around like the GPU's uint)."""
    v = np.asarray(v, np.uint32)
    with np.errstate(over='ignore'):
        v = v * _C1 + _C2
        w = ((v >> ((v >> np.uint32(28)) + np.uint32(4))) ^ v) * _C3
    return (w >> np.uint32(22)) ^ w


def hash_np(words0, *words):
    """hash_words for arrays: words may be arrays or whole numbers."""
    # explicit types throughout: numpy 1.23 (Nuke 15) and numpy 2 promote mixed integer types differently
    h = pcg_np((np.asarray(words0).astype(np.uint64) & np.uint64(M32)).astype(np.uint32))
    with np.errstate(over='ignore'):
        for w in words:
            h = pcg_np(h + np.uint32(w) if np.isscalar(w) else h + np.asarray(w, np.uint32))
    return h


def uniform_np(h):
    return (np.asarray(h, np.uint32) >> np.uint32(8)).astype(np.float64) * U24


# ---------------------------------------------------------------------------------------------------------------------
#  exp, log and pow from + - * / only (identical in every IEEE 754 language)
# ---------------------------------------------------------------------------------------------------------------------
LN2_HI = 6.93147180369123816490e-01      # ln 2, split so n * LN2_HI is exact (fdlibm's constants)
LN2_LO = 1.90821492927058770002e-10
INV_LN2 = 1.44269504088896338700e+00
SQRT_HALF = 0.7071067811865476

_EXP_N = 16                                 # Taylor terms for exp on [-ln2/2, ln2/2]: far below a double's precision
_EXP_C = [1.0]
_f = 1
for _k in range(1, _EXP_N + 1):
    _f *= _k                                # exact: 16! < 2^53
    _EXP_C.append(1.0 / _f)
_LOG_C = [1.0 / (2 * i + 1) for i in range(11)]   # 1, 1/3, 1/5, ... 1/21: atanh series


EXP_RANGE = 700.0                           # exp's input is clamped to +-700 (finite in every language)


def xexp(x):
    x = min(max(x, -EXP_RANGE), EXP_RANGE)
    n = math.floor(x * INV_LN2 + 0.5)
    r = (x - n * LN2_HI) - n * LN2_LO
    p = _EXP_C[_EXP_N]
    for k in range(_EXP_N - 1, -1, -1):
        p = p * r + _EXP_C[k]
    return math.ldexp(p, int(n))


def xlog(x):
    """Natural log of x > 0."""
    m, e = math.frexp(x)
    if m < SQRT_HALF:
        m = m * 2.0
        e = e - 1
    s = (m - 1.0) / (m + 1.0)
    z = s * s
    p = _LOG_C[10]
    for k in range(9, -1, -1):
        p = p * z + _LOG_C[k]
    return e * LN2_HI + (e * LN2_LO + (2.0 * s) * p)


def xpow(a, b):
    """a ** b for a >= 0 (0 ** b = 0)."""
    if a <= 0.0:
        return 0.0
    return xexp(b * xlog(a))


def xexp_np(x):
    x = np.clip(np.asarray(x, np.float64), -EXP_RANGE, EXP_RANGE)
    n = np.floor(x * INV_LN2 + 0.5)
    r = (x - n * LN2_HI) - n * LN2_LO
    p = np.full_like(x, _EXP_C[_EXP_N])
    for k in range(_EXP_N - 1, -1, -1):
        p = p * r + _EXP_C[k]
    return np.ldexp(p, n.astype(np.int32))


def xlog_np(x):
    x = np.asarray(x, np.float64)
    m, e = np.frexp(x)
    low = m < SQRT_HALF
    m = np.where(low, m * 2.0, m)
    e = np.where(low, e - 1, e).astype(np.float64)
    s = (m - 1.0) / (m + 1.0)
    z = s * s
    p = np.full_like(x, _LOG_C[10])
    for k in range(9, -1, -1):
        p = p * z + _LOG_C[k]
    return e * LN2_HI + (e * LN2_LO + (2.0 * s) * p)


def xpow_np(a, b):
    a = np.asarray(a, np.float64)
    pos = a > 0.0
    safe = np.where(pos, a, 1.0)
    return np.where(pos, xexp_np(b * xlog_np(safe)), 0.0)


def gauss(h_words):
    """A normal-ish number (mean 0, variance 1) from 12 hashed uniforms (Irwin-Hall): exact arithmetic only."""
    s = 0.0
    for i in range(12):
        s = s + uniform(hash_words(*(h_words + (i,))))
    return s - 6.0


# ---------------------------------------------------------------------------------------------------------------------
#  The swirling air: random Fourier modes (seed only; sizes and speeds are scaled when they're used)
# ---------------------------------------------------------------------------------------------------------------------
def mode_table(seed, which, count):
    """count modes of set `which` as a float32 (count, 8) array: wave vector K (3) for a 1 m eddy, amplitude A (3),
    phase, angular speed for a 1 m eddy. The air uses K / size and speed / size. A is perpendicular to K, so the flow
    is divergence-free (no sources or sinks: the dust neither bunches up nor thins out)."""
    out = np.zeros((count, 8), np.float32)
    amp = math.sqrt(6.0 / count)
    two_pi = 2.0 * math.pi
    for m in range(count):
        base = (seed & M32, MODES, which * 1024 + m)
        # direction: a point picked inside the unit ball (rejection), then normalised
        for attempt in range(64):
            v = [uniform(hash_words(*(base + (attempt * 3 + c,)))) * 2.0 - 1.0 for c in range(3)]
            r2 = (v[0] * v[0] + v[1] * v[1]) + v[2] * v[2]
            if 1e-6 < r2 <= 1.0:
                break
        r = math.sqrt(r2)
        k = [v[0] / r, v[1] / r, v[2] / r]
        kmag = two_pi * xexp(0.35 * gauss(base + (200,)))
        # amplitude: a random vector with its component along k taken out
        for attempt in range(64):
            a = [gauss(base + (300 + attempt * 3 + c,)) for c in range(3)]
            d = (a[0] * k[0] + a[1] * k[1]) + a[2] * k[2]
            a = [a[0] - k[0] * d, a[1] - k[1] * d, a[2] - k[2] * d]
            al = math.sqrt((a[0] * a[0] + a[1] * a[1]) + a[2] * a[2])
            if al > 1e-3:
                break
        phase = uniform(hash_words(*(base + (400,)))) * two_pi
        omega = (0.4 + 0.6 * uniform(hash_words(*(base + (401,))))) * kmag
        out[m] = [k[0] * kmag, k[1] * kmag, k[2] * kmag, a[0] / al * amp, a[1] / al * amp, a[2] / al * amp,
                  phase, omega]
    return out


def mode_tables(seed):
    return [mode_table(seed, which, count) for which, count in MODE_SETS]


# ---------------------------------------------------------------------------------------------------------------------
#  The dust's starting state
# ---------------------------------------------------------------------------------------------------------------------
def mote_count(e):
    """How many motes: density (after Amount) x box volume, capped by Max motes. e = Settings.engine()."""
    su = e['scene_unit']
    size = [max(s * su, 0.02) for s in e['box_size']]
    vol = (size[0] * size[1]) * size[2]
    return int(max(0, min(int(e['max_motes']), math.floor(e['density'] * vol + 0.5)))), size


def dust_start(e, n=None):
    """The dust's starting state, as float32 arrays (what both renderers start from):

        local   (n, 3)  position in the box, metres, box axes, centred on the box
        h       (n, 10) the mote's random attributes, 0..1
        kind    (n,)    GRIT, FLAKE, FIBER or FLUFF
        settle  (n,)    how fast it sinks, as a fraction of Settling
        tau     (n,)    how long it takes to follow the air, seconds
        grit_d  (n,)    grit diameter in micrometres (0 for other kinds)

    e is Settings.engine() (the master dials already applied)."""
    count, size = mote_count(e)
    if n is None:
        n = count
    idx = np.arange(n, dtype=np.uint32)
    hb = hash_np(np.uint32(int(e['seed']) & M32), DUST, idx)
    with np.errstate(over='ignore'):
        u = np.stack([uniform_np(pcg_np(hb + np.uint32(k))) for k in range(H_ATTR + N_ATTR)], 1)
    local = np.empty((n, 3), np.float32)
    for c in range(3):
        local[:, c] = ((u[:, H_POS + c] - 0.5) * size[c]).astype(np.float32)
    h = u[:, H_ATTR:H_ATTR + N_ATTR]
    f_fib, f_flf, f_flk = e['fibers'] / 100.0, e['fluff'] / 100.0, e['flakes'] / 100.0
    h0 = h[:, 0]
    kind = np.where(h0 < f_fib, FIBER, np.where(h0 < f_fib + f_flf, FLUFF,
                    np.where(h0 < (f_fib + f_flf) + f_flk, FLAKE, GRIT))).astype(np.uint8)
    smin = e['size_min']
    ratio = e['size_max'] / max(smin, 1e-3)
    d = smin * xpow_np(ratio, xpow_np(h[:, 1], e['size_bias']))
    q = d / 50.0
    settle = np.where(kind == GRIT, 0.15 + np.minimum(q * q, 4.0),
                      np.where(kind == FLAKE, 0.5, np.where(kind == FIBER, 0.22, 0.12)))
    tau = np.where(kind >= FIBER, 0.22, 0.35)
    return dict(n=n, size=tuple(size), local=local, h=h.astype(np.float32), kind=kind,
                settle=settle.astype(np.float32), tau=tau.astype(np.float32),
                grit_d=np.where(kind == GRIT, d, 0.0).astype(np.float32))
