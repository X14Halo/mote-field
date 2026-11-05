"""OpenEXR reading and writing in plain Python + numpy.

Reads scanline files, single- or multi-part, uncompressed or RLE, ZIPS or ZIP compressed: what utility passes are
usually written as. Tiled files and PIZ, PXR24, B44 and DWA compression go through OpenImageIO or the OpenEXR module
when this Python has one, and otherwise fail with a message saying what to do.

Writes single-part scanline ZIP files with any mix of half and float channels: the dust frames.

    f = ExrFile(path)
    f.parts                    [Part(name, width, height, channels, compression, header, ...)]
    f.read(part_index, names)  {channel name: float32 (H, W) array over the part's data window}
    write(path, {name: (H, W) array}, attrs={...}, half={names...} or True)
"""

import math
import os
import struct
import zlib

import numpy as np

MAGIC = 20000630
COMPRESSION = {0: 'none', 1: 'RLE', 2: 'ZIPS', 3: 'ZIP', 4: 'PIZ', 5: 'PXR24', 6: 'B44', 7: 'B44A', 8: 'DWAA', 9: 'DWAB',
               10: 'HTJ2K'}
LOSSY = (5, 6, 7, 8, 9)
READS_ITSELF = (0, 1, 2, 3)
LINES_PER_CHUNK = {0: 1, 1: 1, 2: 1, 3: 16, 4: 32, 5: 16, 6: 32, 7: 32, 8: 32, 9: 256, 10: 256}
UINT, HALF, FLOAT = 0, 1, 2
PIXEL_DTYPE = {UINT: '<u4', HALF: '<f2', FLOAT: '<f4'}
PIXEL_BYTES = {UINT: 4, HALF: 2, FLOAT: 4}
PIXEL_NAMES = {UINT: 'uint', HALF: 'half', FLOAT: 'float'}


class ExrError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------------
#  Header attributes
# ---------------------------------------------------------------------------------------------------------------------
_FIXED = {'box2i': '<4i', 'box2f': '<4f', 'int': '<i', 'float': '<f', 'double': '<d', 'm33f': '<9f', 'm33d': '<9d',
          'm44f': '<16f', 'm44d': '<16d', 'v2i': '<2i', 'v2f': '<2f', 'v2d': '<2d', 'v3i': '<3i', 'v3f': '<3f',
          'v3d': '<3d', 'rational': '<iI', 'timecode': '<II', 'keycode': '<7i', 'chromaticities': '<8f'}


def _cstr(b, o):
    e = b.find(b'\0', o)
    if e < 0:
        raise ExrError('truncated header')
    return b[o:e].decode('latin-1'), e + 1


def _attr(b, o, typ, size):
    if typ == 'chlist':
        chans, p = [], o
        while b[p] != 0:
            n, q = _cstr(b, p)
            ptype, _plin, xs, ys = struct.unpack_from('<iB3xii', b, q)
            chans.append(Channel(n, ptype, xs, ys))
            p = q + 16
        return chans
    if typ in ('compression', 'lineOrder', 'envmap', 'deepImageState'):
        return b[o]
    if typ == 'string':
        return bytes(b[o:o + size]).decode('utf-8', 'replace')
    if typ == 'stringvector':
        out, p = [], o
        while p < o + size:
            n = struct.unpack_from('<i', b, p)[0]
            out.append(bytes(b[p + 4:p + 4 + n]).decode('utf-8', 'replace'))
            p += 4 + n
        return out
    if typ == 'tiledesc':
        xs, ys, mode = struct.unpack_from('<IIB', b, o)
        return dict(x=xs, y=ys, mode=mode)
    fmt = _FIXED.get(typ)
    if fmt and struct.calcsize(fmt) <= size:
        v = struct.unpack_from(fmt, b, o)
        return v[0] if len(v) == 1 else list(v)
    return bytes(b[o:o + size])                 # an attribute type this reader doesn't know: kept as raw bytes


def _read_header(b, o):
    h = {}
    while True:
        name, a = _cstr(b, o)
        if name == '':
            return h, a
        typ, c = _cstr(b, a)
        size = struct.unpack_from('<i', b, c)[0]
        h[name] = _attr(b, c + 4, typ, size)
        o = c + 4 + size


class Channel(object):
    __slots__ = ('name', 'type', 'xs', 'ys')

    def __init__(self, name, type, xs=1, ys=1):
        self.name, self.type, self.xs, self.ys = name, type, xs, ys

    def __repr__(self):
        return '%s:%s' % (self.name, PIXEL_NAMES.get(self.type, self.type))


class Part(object):
    """One part (image) of an EXR file."""

    def __init__(self, index, header, tiled_file):
        self.index, self.header = index, header
        dw = header.get('dataWindow') or [0, 0, 0, 0]
        self.x0, self.y0 = dw[0], dw[1]
        self.width, self.height = dw[2] - dw[0] + 1, dw[3] - dw[1] + 1
        self.display = header.get('displayWindow') or dw
        self.name = header.get('name', '') or ''
        self.channels = header.get('channels') or []
        self.compression = header.get('compression', 0)
        self.tiled = bool(tiled_file) or 'tiled' in str(header.get('type', ''))
        self.deep = 'deep' in str(header.get('type', ''))
        self.lines = LINES_PER_CHUNK.get(self.compression, 1)
        self.offsets = ()

    @property
    def compression_name(self):
        return COMPRESSION.get(self.compression, str(self.compression))

    def channel_names(self):
        return [c.name for c in self.channels]

    def __repr__(self):
        return 'Part(%d %r %dx%d %s %d channels)' % (self.index, self.name, self.width, self.height,
                                                     self.compression_name, len(self.channels))


# ---------------------------------------------------------------------------------------------------------------------
#  Decoding
# ---------------------------------------------------------------------------------------------------------------------
def _un_rle(src, n):
    out, i, o = bytearray(n), 0, 0
    while i < len(src) and o < n:
        c = src[i] - 256 if src[i] > 127 else src[i]
        i += 1
        if c < 0:
            c = -c
            out[o:o + c] = src[i:i + c]
            o += c
            i += c
        else:
            out[o:o + c + 1] = bytes([src[i]]) * (c + 1)
            o += c + 1
            i += 1
    return bytes(out)


def _un_predict(raw):
    """Undo the ZIP/RLE byte predictor and interleave: the inverse of _predict."""
    t = np.frombuffer(raw, np.uint8)
    if t.size == 0:
        return raw
    d = t.astype(np.int64)
    d[1:] -= 128
    t = (np.cumsum(d) & 255).astype(np.uint8)
    half = (t.size + 1) // 2
    out = np.empty_like(t)
    out[0::2] = t[:half]
    out[1::2] = t[half:]
    return out.tobytes()


def _predict(raw):
    t = np.frombuffer(raw, np.uint8)
    half = (t.size + 1) // 2
    il = np.empty_like(t)
    il[:half] = t[0::2]
    il[half:] = t[1::2]
    d = il.astype(np.int16)
    d[1:] = (d[1:] - d[:-1] + 128) & 255
    return d.astype(np.uint8).tobytes()


class ExrFile(object):
    """An open EXR file. Use as a context manager, or call close()."""

    def __init__(self, path):
        self.path = path
        self._f = open(path, 'rb')
        try:
            import mmap
            b = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:                       # no mmap (the browser): read it all
            self._f.seek(0)
            b = self._f.read()
        self._b = b
        try:
            if len(b) < 8 or struct.unpack_from('<I', b, 0)[0] != MAGIC:
                raise ExrError('%s is not an OpenEXR file' % os.path.basename(path))
            ver = struct.unpack_from('<I', b, 4)[0]
            tiled, self.multipart = bool(ver & 0x200), bool(ver & 0x1000)
            if ver & 0x800 and not self.multipart:
                raise ExrError('%s is a deep EXR; use a flat utility pass' % os.path.basename(path))
            o, headers = 8, []
            if self.multipart:
                while b[o] != 0:
                    h, o = _read_header(b, o)
                    headers.append(h)
                o += 1
            else:
                h, o = _read_header(b, o)
                headers.append(h)
            self.parts = [Part(i, h, tiled and not self.multipart) for i, h in enumerate(headers)]
            for p in self.parts:
                n = p.header.get('chunkCount') or int(math.ceil(p.height / float(p.lines)))
                if not p.tiled:
                    try:
                        p.offsets = struct.unpack_from('<%dQ' % n, b, o)
                    except struct.error:
                        raise ExrError('%s is truncated' % os.path.basename(path))
                o += 8 * n
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for h in (getattr(self, '_b', None), getattr(self, '_f', None)):
            try:
                h.close()
            except Exception:
                pass

    @property
    def headers(self):
        return [p.header for p in self.parts]

    def part(self, name):
        for p in self.parts:
            if p.name == name:
                return p
        return None

    def read(self, pi, names):
        """{name: float32 (H, W)} for channels of part pi, over its data window."""
        p = self.parts[pi]
        names = list(names)
        have = {c.name for c in p.channels}
        missing = [n for n in names if n not in have]
        if missing:
            raise ExrError('%s part %d has no channel %s' % (os.path.basename(self.path), pi, ', '.join(missing)))
        if p.deep:
            raise ExrError('%s part %d is deep; use a flat utility pass' % (os.path.basename(self.path), pi))
        if p.tiled or p.compression not in READS_ITSELF:
            out = read_with_library(self.path, pi, names, p)
            if out is not None:
                return out
            what = 'tiled' if p.tiled else '%s-compressed' % p.compression_name
            raise ExrError('%s is %s, which needs OpenImageIO or the OpenEXR Python module (neither is in this Python). '
                           'Install one, or write utility passes as scanline ZIP, ZIPS, RLE or uncompressed.'
                           % (os.path.basename(self.path), what))
        for c in p.channels:
            if c.name in names and (c.xs != 1 or c.ys != 1):
                raise ExrError('%s: channel %s is subsampled, which is not supported' % (self.path, c.name))
        b, W, H = self._b, p.width, p.height
        offs, o = {}, 0
        for c in p.channels:
            offs[c.name] = (o, c.type)
            o += PIXEL_BYTES[c.type] * W
        line_bytes = o
        out = {n: np.empty((H, W), np.float32) for n in names}
        for q in p.offsets:
            if self.multipart:
                q += 4
            y, size = struct.unpack_from('<ii', b, q)
            q += 8
            ly = y - p.y0
            nl = min(p.lines, H - ly)
            expect = line_bytes * nl
            data = b[q:q + size]
            if size < expect:
                data = _un_predict(_un_rle(data, expect) if p.compression == 1 else zlib.decompress(data))
            a = np.frombuffer(data, np.uint8, count=expect).reshape(nl, line_bytes)
            for n in names:
                off, t = offs[n]
                seg = np.ascontiguousarray(a[:, off:off + PIXEL_BYTES[t] * W]).view(PIXEL_DTYPE[t])
                out[n][ly:ly + nl] = seg
        return out


def read_with_library(path, pi, names, part):
    """Channels from a file this module can't decode itself, through OpenImageIO or the OpenEXR module. Same result as
    ExrFile.read; None when neither is installed."""
    W, H = part.width, part.height
    try:
        import OpenImageIO as oiio
    except Exception:
        oiio = None
    if oiio is not None:
        inp = oiio.ImageInput.open(path)
        if inp:
            try:
                if pi and not inp.seek_subimage(pi, 0):
                    raise ExrError('%s: no part %d' % (os.path.basename(path), pi))
                spec = inp.spec()
                cn = list(spec.channelnames)
                px = inp.read_image(0, spec.nchannels, oiio.FLOAT)
                if px is None:
                    raise ExrError('%s: %s' % (os.path.basename(path), inp.geterror()))
                px = np.asarray(px, np.float32).reshape(spec.height, spec.width, spec.nchannels)
                return {n: np.ascontiguousarray(px[:, :, cn.index(n)]) for n in names}
            finally:
                inp.close()
    try:
        import OpenEXR
    except Exception:
        return None
    if hasattr(OpenEXR, 'File'):
        f = OpenEXR.File(path, separate_channels=True)
        chans = f.parts[pi].channels
        return {n: np.asarray(chans[n].pixels, np.float32).reshape(H, W) for n in names}
    if hasattr(OpenEXR, 'InputFile') and pi == 0:
        import Imath
        f = OpenEXR.InputFile(path)
        ft = Imath.PixelType(Imath.PixelType.FLOAT)
        return {n: np.frombuffer(f.channel(n, ft), np.float32).reshape(H, W).copy() for n in names}
    return None


# ---------------------------------------------------------------------------------------------------------------------
#  Writing
# ---------------------------------------------------------------------------------------------------------------------
def _attr_bytes(name, typ, value):
    if typ == 'chlist':
        body = b''.join(n.encode() + b'\0' + struct.pack('<iB3xii', t, 0, 1, 1) for n, t in value) + b'\0'
    elif typ in ('compression', 'lineOrder'):
        body = struct.pack('<B', value)
    elif typ == 'string':
        body = value.encode('utf-8')
    else:
        fmt = _FIXED[typ]
        body = struct.pack(fmt, *(value if isinstance(value, (list, tuple)) else [value]))
    return name.encode() + b'\0' + typ.encode() + b'\0' + struct.pack('<i', len(body)) + body


def _channel_order(names):
    return sorted(names)                       # EXR requires channels in alphabetical order


def write(path, channels, attrs=None, half=True, level=4):
    """Writes {name: (H, W) array} as a single-part scanline ZIP EXR, atomically.

    half: True (every channel half), False (every channel float), or a set of the names to write as half.
    attrs: {name: value} (strings, ints, floats, or 16 floats for an m44f) or {name: (type, value)}."""
    names = _channel_order(channels)
    if not names:
        raise ExrError('nothing to write')
    H, W = np.asarray(channels[names[0]]).shape
    types = {n: HALF if (half is True or (half and half is not False and n in half)) else FLOAT for n in names}
    hdr = [_attr_bytes('channels', 'chlist', [(n, types[n]) for n in names]),
           _attr_bytes('compression', 'compression', 3),
           _attr_bytes('dataWindow', 'box2i', [0, 0, W - 1, H - 1]),
           _attr_bytes('displayWindow', 'box2i', [0, 0, W - 1, H - 1]),
           _attr_bytes('lineOrder', 'lineOrder', 0),
           _attr_bytes('pixelAspectRatio', 'float', 1.0),
           _attr_bytes('screenWindowCenter', 'v2f', [0.0, 0.0]),
           _attr_bytes('screenWindowWidth', 'float', 1.0)]
    for k, v in sorted((attrs or {}).items()):
        if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) and (v[0] in _FIXED or v[0] == 'string'):
            t, v = v                            # an explicit (type, value)
        elif isinstance(v, str):
            t = 'string'
        elif isinstance(v, bool) or isinstance(v, int):
            t, v = 'int', int(v)
        elif isinstance(v, float):
            t = 'float'
        elif isinstance(v, (list, tuple)) and len(v) == 16:
            t = 'm44f'
        else:
            t, v = 'string', str(v)
        hdr.append(_attr_bytes(k, t, v))
    head = struct.pack('<II', MAGIC, 2) + b''.join(hdr) + b'\0'
    planes = [np.ascontiguousarray(np.asarray(channels[n]).reshape(H, W), dtype=PIXEL_DTYPE[types[n]]) for n in names]
    lines = LINES_PER_CHUNK[3]
    chunks = []
    for y in range(0, H, lines):
        nl = min(lines, H - y)
        raw = b''.join(b''.join(pl[yy].tobytes() for pl in planes) for yy in range(y, y + nl))
        comp = zlib.compress(_predict(raw), level)
        data = comp if len(comp) < len(raw) else raw
        chunks.append(struct.pack('<ii', y, len(data)) + data)
    pos = len(head) + 8 * len(chunks)
    table = []
    for c in chunks:
        table.append(pos)
        pos += len(c)
    path = os.path.abspath(path)
    tmp = '%s.tmp%d.exr' % (path, os.getpid())
    with open(tmp, 'wb') as f:
        f.write(head)
        f.write(struct.pack('<%dQ' % len(table), *table))
        for c in chunks:
            f.write(c)
    os.replace(tmp, path)
    return path
