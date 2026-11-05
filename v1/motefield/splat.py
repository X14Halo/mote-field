"""Drawing blurred points into an image: the core of the renderer.

Each sample (a point along a mote's motion-blur streak) has a position in pixels, a blur radius and some light in
several channels. It is drawn as the lens would draw it:

  * small blur (under SMALL px): a Gaussian, stamped per sample at its own exact size (no binning);
  * larger blur: the lens aperture (blades, anamorphic squeeze, cat-eye, centre/rim brightness, or a custom shape),
    normalised so a defocused point spreads its light without gaining or losing any. Samples are grouped by radius
    (steps of RATIO) and by screen tile; each group is either stamped sample by sample or FFT-convolved over its
    tile, whichever is cheaper. Cat-eye is taken at each tile's centre, so it varies smoothly across the frame.

Work is proportional to the samples there are, never to empty screen.
"""

import math

import numpy as np

SMALL = 2.5              # px: below this radius, a Gaussian
RATIO = 1.12             # radius steps between aperture kernels
TILE = 256               # px: tile size for FFT groups
CHUNK = 250000           # samples per batch when stamping Gaussians (bounds memory)
FORCE = None             # tests: 'stamp' or 'fft' to use one way for every large-blur group


def gauss_sigma(R):
    """Gaussian sigma (px) for a blur radius R (px): the same formula as the GPU viewer."""
    return np.sqrt(0.25 * R * R + 0.12 + 1.0 / 6.0)


def smooth01(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------------------------------------------------------------
#  The aperture
# ---------------------------------------------------------------------------------------------------------------------
def _sample_bilinear(img, u, v):
    n = img.shape[0]
    x, y = u * n - 0.5, v * n - 0.5
    x0, y0 = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
    tx, ty = x - x0, y - y0
    out = np.zeros(u.shape)
    for dx, dy, w in ((0, 0, (1 - tx) * (1 - ty)), (1, 0, tx * (1 - ty)), (0, 1, (1 - tx) * ty), (1, 1, tx * ty)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < n) & (yi >= 0) & (yi < n)
        out[ok] += w[ok] * img[yi[ok], xi[ok]]
    return out


def aperture(R, blades=0, rot=0.25, aspect=1.0, cat=(0.0, 0.0), falloff=0.0, image=None, img_rot=0.0):
    """The lens aperture as a kernel of radius R px, summing to 1. blades < 3 = round; aspect > 1 = taller (same
    area); cat = cat-eye offset (fraction of R, pointing away from the frame centre): the overlap of two discs that
    far apart, so bokeh toward the frame edges turn into lemons; falloff > 0 brighter centre, < 0 brighter rim;
    image: a custom aperture (square array) spanning the diameter."""
    sa = math.sqrt(max(aspect, 0.05))
    r = int(math.ceil(R * max(sa, 1.0 / sa) + 1.5))
    y, x = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float64)
    Rs = max(R, 1e-6)
    xa, ya = x * sa, y / sa
    rr = np.hypot(xa, ya) / Rs
    if blades >= 3:
        a = np.arctan2(ya, xa) + rot
        sg = 2 * math.pi / blades
        ang = np.mod(a, sg) - sg / 2
        poly = rr * np.cos(ang) / math.cos(sg / 2)
        m = 0.65 * poly + 0.35 * rr
    else:
        m = rr
    aa = min(max(1.5 / Rs, 0.04), 0.5)
    win = lambda q: 1 - smooth01((q - (1 - aa)) / (2 * aa))
    lens = None
    if cat[0] or cat[1]:
        cx, cy = cat[0] * 0.5, cat[1] * 0.5
        lens = np.maximum(np.hypot(x / Rs - cx, y / Rs - cy), np.hypot(x / Rs + cx, y / Rs + cy))
    if image is not None:
        c, s_ = math.cos(-img_rot), math.sin(-img_rot)
        xr, yr = (xa * c - ya * s_) / Rs, (xa * s_ + ya * c) / Rs
        k = _sample_bilinear(image, xr * 0.5 + 0.5, yr * 0.5 + 0.5) * (1 + falloff * (0.5 - rr * rr))
        if lens is not None:
            k = k * win(lens)
    else:
        k = win(m if lens is None else np.maximum(m, lens)) * (1 + falloff * (0.5 - m * m))
    k = np.clip(k, 0, None)
    return k / max(k.sum(), 1e-12)


class Lens(object):
    """What shapes the bokeh, plus caches of kernels and their FFTs."""

    def __init__(self, blades=0, rot=0.25, aspect=1.0, cateye=0.0, falloff=0.0, image=None, img_rot=0.0):
        self.blades, self.rot, self.aspect, self.cateye = int(blades), rot, aspect, cateye
        self.falloff, self.image, self.img_rot = falloff, image, img_rot
        self._k, self._f = {}, {}

    def kernel(self, R, cat=(0.0, 0.0)):
        key = (round(R, 3), round(cat[0], 2), round(cat[1], 2))
        k = self._k.get(key)
        if k is None:
            k = aperture(R, self.blades, self.rot, self.aspect, (key[1], key[2]), self.falloff, self.image,
                         self.img_rot)
            if len(self._k) > 600:
                self._k.clear()
            self._k[key] = k
        return k, key

    def kernel_fft(self, key, k, shape):
        fk = (key, shape)
        F = self._f.get(fk)
        if F is None:
            F = np.fft.rfft2(k, s=shape)
            if len(self._f) > 120:
                self._f.clear()
            self._f[fk] = F
        return F


def _fast_len(n):
    """The next size >= n made of factors 2, 3 and 5 (fast FFT sizes)."""
    best = 1 << int(math.ceil(math.log2(max(n, 1))))
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            p = p35
            while p < n:
                p *= 2
            best = min(best, p)
            p35 *= 3
        p5 *= 5
    return best


# ---------------------------------------------------------------------------------------------------------------------
#  Drawing
# ---------------------------------------------------------------------------------------------------------------------
def _stamp_gauss(img, X, Y, R, E):
    """Adds a normalised Gaussian per sample (R < SMALL) into img (H, W, C)."""
    H, W, C = img.shape
    flat = img.reshape(-1, C)
    sig = gauss_sigma(R)
    k = np.clip(np.ceil(3 * sig).astype(np.intp), 1, None)
    for kk in np.unique(k):
        sel = np.where(k == kk)[0]
        offs = np.arange(-kk, kk + 1)
        oy, ox = np.meshgrid(offs, offs, indexing='ij')
        oy, ox = oy.ravel(), ox.ravel()
        for c0 in range(0, len(sel), CHUNK):
            s = sel[c0:c0 + CHUNK]
            x, y, sg = X[s], Y[s], sig[s]
            ix, iy = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
            px = ix[:, None] + ox[None, :]
            py = iy[:, None] + oy[None, :]
            dx = px + 0.5 - x[:, None]
            dy = py + 0.5 - y[:, None]
            w = np.exp(-(dx * dx + dy * dy) / (2 * sg * sg)[:, None])
            w /= w.sum(1, keepdims=True)
            ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
            idx = (py * W + px)[ok]
            for c in range(C):
                flat[:, c] += np.bincount(idx, weights=(w * E[s, c][:, None])[ok], minlength=H * W)


def _bilinear_into(buf, x, y, E):
    """Bilinear splat of samples into buf (h, w, C) at local pixel coordinates."""
    h, w, C = buf.shape
    fx, fy = x - 0.5, y - 0.5
    x0, y0 = np.floor(fx).astype(np.intp), np.floor(fy).astype(np.intp)
    tx, ty = fx - x0, fy - y0
    xs = np.concatenate([x0, x0 + 1, x0, x0 + 1])
    ys = np.concatenate([y0, y0, y0 + 1, y0 + 1])
    ws = np.concatenate([(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty])
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    idx = (ys * w + xs)[ok]
    flat = buf.reshape(-1, C)
    for c in range(C):
        flat[:, c] += np.bincount(idx, weights=(ws * np.tile(E[:, c], 4))[ok], minlength=h * w)


def _add_clipped(img, patch, x0, y0):
    H, W = img.shape[:2]
    h, w = patch.shape[:2]
    ax0, ay0 = max(x0, 0), max(y0, 0)
    ax1, ay1 = min(x0 + w, W), min(y0 + h, H)
    if ax1 > ax0 and ay1 > ay0:
        img[ay0:ay1, ax0:ax1] += patch[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]


def draw(img, X, Y, R, E, lens, stats=None):
    """Draws samples into img (H, W, C): X, Y pixel positions (pixel centres at .5), R blur radii (px), E (n, C)."""
    if not len(X):
        return
    H, W, C = img.shape
    small = R < SMALL
    if small.any():
        _stamp_gauss(img, X[small], Y[small], R[small], E[small])
    big = np.where(~small)[0]
    if not len(big):
        return
    X, Y, R, E = X[big], Y[big], R[big], E[big]
    b = np.floor(np.log(R / SMALL) / math.log(RATIO)).astype(np.intp)
    tx = np.floor(X / TILE).astype(np.intp)
    ty = np.floor(Y / TILE).astype(np.intp)
    half_diag = math.hypot(W / 2.0, H / 2.0)
    order = np.lexsort((tx, ty, b))
    keys = np.stack([b, ty, tx], 1)[order]
    cuts = np.where(np.any(np.diff(keys, axis=0) != 0, axis=1))[0] + 1
    for grp in np.split(order, cuts):
        bb, gy, gx = b[grp[0]], ty[grp[0]], tx[grp[0]]
        Rb = SMALL * RATIO ** (bb + 0.5)
        cat = (0.0, 0.0)
        if lens.cateye > 0:
            cx = ((gx + 0.5) * TILE - W / 2.0) / half_diag * lens.cateye
            cy = ((gy + 0.5) * TILE - H / 2.0) / half_diag * lens.cateye
            cat = (cx, cy)
        k, key = lens.kernel(Rb, cat)
        r = k.shape[0] // 2
        n = len(grp)
        side = TILE + 2 * r + 2
        shape = (_fast_len(side + 2 * r), _fast_len(side + 2 * r))
        # measured costs: a stamp is ~7 us plus ~2.5 ns per kernel pixel and channel; an FFT tile ~20 ns per pixel and
        # channel of its padded size
        stamp = n * (7e-6 + k.size * C * 2.5e-9) < 20e-9 * shape[0] * shape[1] * C
        if FORCE:
            stamp = FORCE == 'stamp'
        if stamp:
            if stats is not None:
                stats['stamped'] = stats.get('stamped', 0) + n
            xi = np.floor(X[grp]).astype(np.intp)
            yi = np.floor(Y[grp]).astype(np.intp)
            for j in range(n):
                _add_clipped(img, k[:, :, None] * E[grp[j]][None, None, :], xi[j] - r, yi[j] - r)
        else:
            if stats is not None:
                stats['fft'] = stats.get('fft', 0) + 1
            ox, oy = gx * TILE - r - 1, gy * TILE - r - 1
            buf = np.zeros((side, side, C))
            _bilinear_into(buf, X[grp] - ox, Y[grp] - oy, E[grp])
            F = lens.kernel_fft(key, k, shape)
            out = np.empty_like(buf)
            for c in range(C):
                out[:, :, c] = np.fft.irfft2(np.fft.rfft2(buf[:, :, c], s=shape) * F, s=shape)[r:r + side, r:r + side]
            _add_clipped(img, out, ox, oy)
