"""Where every mote is at any moment, worked out directly from the time: no simulation state, no pre-roll.

The air is what MoteField 0.8 simulated, written so each mote's path has a closed form:

  * drift: a steady breeze (and a vertical drift) carries everything along;
  * eddies: large slow swirls, and micro-turbulence: small ones. Both are divergence-free random Fourier modes
    (rng.mode_tables), frozen into the breeze as it carries them, and slowly changing. A mote moves through them only
    by settling, so along its path each mode is a sine of time: its velocity is a sum of sines and its displacement a
    sum of their exact integrals;
  * settling: heavier grit sinks faster (each mote's own rate);
  * rising warm air where the sun is: integrated once per shot along each mote's drift path through the light,
    then read off for any time.

Motes wrap around inside the box (the edge fade hides the jump). Lag behind the air is left out: at these speeds
the air changes so slowly that it would change positions by well under a millimetre.

Because nothing is stepped forward, any frame comes out the same alone or in a sequence, on any machine; the farm can
render frames in any order; and the GPU, working in 32-bit floats, can't drift away from this over a long shot.

    m = DustMotion(e, vis)      e = Settings.engine(frame); vis = light.visibility(...) or None
    s = m.at(frame)             s.P (scene units, world), s.V (scene units per second), s.local (metres, box axes)
"""

import math

import numpy as np

from . import rng

THERMAL_DT = 0.25            # seconds between samples of the light along each mote's path
MICRO_SCALE = 0.22           # micro-turbulence eddies are this fraction of the eddy size
MICRO_FIBRE = 1.6            # fibres and fluff catch micro-turbulence this much more


def rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


class State(object):
    __slots__ = ('t', 'P', 'V', 'local')

    def __init__(self, t, P, V, local):
        self.t, self.P, self.V, self.local = t, P, V, local


class DustMotion(object):
    def __init__(self, e, vis=None):
        self.e = e
        self.su = su = e['scene_unit']
        st = rng.dust_start(e)
        self.start = st
        self.n = st['n']
        self.kind = st['kind']
        self.x0 = st['local'].astype(np.float64)                     # metres, box axes, centred
        self.size = np.array(st['size'], np.float64)
        self.center = np.asarray(e['box_center'], np.float64) * su
        self.R = rot_y(e['box_rot'])                                 # world = local @ R.T + centre
        a = math.radians(e['drift_dir'])
        self.wind = np.array([math.cos(a) * e['drift'], e['lift'], math.sin(a) * e['drift']]) * 0.01
        self.settle = e['settle'] * 0.01 * st['settle'].astype(np.float64)       # each mote's sinking speed, m/s
        self.w0 = self.x0 @ self.R.T + self.center                   # where each mote starts, world metres
        tabs = rng.mode_tables(e['seed'])
        ae, am = e['eddies'] * 0.01, e['micro'] * 0.01
        size_e = max(e['eddy_size'], 0.05)
        size_m = size_e * MICRO_SCALE
        self.sets = []
        for tab, size, amp, sweep in ((tabs[0], size_e, ae, ae), (tabs[1], size_m, am, am + ae)):
            if amp <= 0:
                continue
            t = tab.astype(np.float64)
            K = t[:, :3] / size
            A = t[:, 3:6]
            phase0 = self.w0 @ K.T + t[:, 6]                         # (N, M)
            omega = t[:, 7] / size * 0.5 * sweep                     # how fast each mode changes (rad/s)
            beta = omega[None, :] - self.settle[:, None] * K[None, :, 1]     # ... as seen by each sinking mote
            gain = np.full(self.n, amp * 0.6)
            if tab is tabs[1]:
                gain = gain * np.where(self.kind >= rng.FIBER, MICRO_FIBRE, 1.0)
            self.sets.append((A, phase0, beta, gain))
        self.vis = vis
        self.thermal = e['thermal'] * 0.01
        self.fps, self.first, self.speed = float(e['fps']), e['first'], e['speed']
        self._th = None

    def time(self, frame):
        """Seconds of air time at a frame (0 at the shot's first frame; Speed scales it)."""
        return (frame - self.first) / self.fps * self.speed

    # ---- where the motes are ------------------------------------------------------------------------------------------
    def _wrap(self, local):
        return np.mod(local + self.size / 2, self.size) - self.size / 2

    def _guide(self, t):
        """Where each mote's drift alone takes it by time t: wrapped in the box, world metres."""
        d = (self.wind[None, :] + np.outer(-self.settle, [0.0, 1.0, 0.0])) * t
        return self._wrap(self.x0 + d @ self.R) @ self.R.T + self.center

    def _thermal_table(self, t_lo, t_hi):
        """The light along each mote's drift path, integrated from t = 0: (times, cumulative (N, K))."""
        k0 = int(math.floor(min(t_lo, 0.0) / THERMAL_DT)) - 1
        k1 = int(math.ceil(max(t_hi, 0.0) / THERMAL_DT)) + 1
        ts = np.arange(k0, k1 + 1) * THERMAL_DT
        v = np.stack([self.vis(self._guide(t) / self.su) for t in ts], 1)            # (N, K)
        steps = (v[:, 1:] + v[:, :-1]) * 0.5 * THERMAL_DT
        cum = np.concatenate([np.zeros((self.n, 1)), np.cumsum(steps, 1)], 1)
        cum -= cum[:, [-k0]]                                                          # 0 at t = 0
        return ts, cum, v

    def _lift(self, t):
        """Rise from warm air by time t (m) and the warm air's speed now (m/s), per mote."""
        if self.vis is None or self.thermal == 0 or self.n == 0:
            return np.zeros(self.n), np.zeros(self.n)
        if self._th is None or not (self._th[0][0] <= t <= self._th[0][-1]):
            # the whole shot (and a little either side) at once; grown if a time outside it is asked for
            lo = min(t, self.time(self.e['first'] - 2), self._th[0][0] if self._th else t)
            hi = max(t, self.time(self.e['last'] + 2), self._th[0][-1] if self._th else t)
            self._th = self._thermal_table(lo, hi)
        ts, cum, v = self._th
        x = (t - ts[0]) / THERMAL_DT
        i = min(max(int(math.floor(x)), 0), len(ts) - 2)
        f = x - i
        rise = cum[:, i] + (cum[:, i + 1] - cum[:, i]) * f
        now = v[:, i] + (v[:, i + 1] - v[:, i]) * f
        return rise * self.thermal, now * self.thermal

    def at(self, frame, t=None):
        """The motes at a frame (or at air time t): positions, velocities, box-local positions."""
        if t is None:
            t = self.time(frame)
        n = self.n
        if n == 0:
            z = np.zeros((0, 3))
            return State(t, z, z, z)
        disp = (self.wind[None, :] + np.outer(-self.settle, [0.0, 1.0, 0.0])) * t
        vel = np.broadcast_to(self.wind, (n, 3)) + np.outer(-self.settle, [0.0, 1.0, 0.0])
        for A, phase0, beta, gain in self.sets:
            half = beta * (t * 0.5)
            # exact integral of sin(phase0 + beta s) from 0 to t, written so it stays accurate as beta -> 0
            d = (t * np.sin(phase0 + half) * np.sinc(half / math.pi)) * gain[:, None]
            disp = disp + d @ A
            vel = vel + (np.sin(phase0 + beta * t) * gain[:, None]) @ A
        rise, rise_v = self._lift(t)
        disp[:, 1] += rise
        vel = vel + np.outer(rise_v, [0.0, 1.0, 0.0])
        local = self._wrap(self.x0 + disp @ self.R)
        P = (local @ self.R.T + self.center) / self.su
        return State(t, P, vel / self.su, local)

    def edge_weight(self, local):
        """0..1 per mote: density fading out toward each face of the box (Edge softness)."""
        e, su = self.e, self.su
        half = self.size / 2
        w = np.ones(len(local))
        base = max(e['fade'], 0.0) * su
        neg, pos = e['fade_neg'], e['fade_pos']
        for ax in range(3):
            for side, m in ((0, neg[ax]), (1, pos[ax])):
                f = base * max(m, 0.0)
                if f <= 1e-9:
                    continue
                d = (local[:, ax] + half[ax]) if side == 0 else (half[ax] - local[:, ax])
                x = np.clip(d / f, 0.0, 1.0)
                w *= x * x * (3 - 2 * x)
        return w
