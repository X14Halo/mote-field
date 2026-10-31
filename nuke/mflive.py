"""
MoteField Live: the fast GPU viewer, for Nuke.

The browser version of MoteField is fast because the dust is simulated and drawn on the graphics card (WebGL), with the
same maths as motefield.py. This module runs that exact viewer (index.html + preview.js + look.js) against *native*
Python instead of Python-in-the-browser:

    * a small local web server (127.0.0.1 only) serves the viewer's files and a binary API
    * the API calls the same functions the browser calls (mfweb.py), with normal CPython + numpy, so reading utility
      passes, building cameras, the light map and the dust's starting state happen at native speed
    * the viewer is shown in a Nuke panel (QtWebEngine) when Nuke has it, otherwise in your normal browser
    * when started from a MoteField node, the viewer reads and writes that node's knobs, so what you set up in the
      viewer is what the node renders on the farm

    In Nuke:        import mflive; mflive.open(nuke.selectedNode())     (or the node's "Live viewer" button)
    Outside Nuke:   python mflive.py [--utility /path/shot_utility.####.exr] [--port 8790]

Nothing leaves the machine: the server only listens on 127.0.0.1.
"""
import io, json, os, sys, struct, threading, traceback, tempfile, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import motefield as mf      # noqa: E402
import mfweb                # noqa: E402

mfweb.ROOT = os.path.join(tempfile.gettempdir(), 'motefield_live')
_LOCK = threading.Lock()          # the renderer's caches are shared: one request at a time
_STATE = {'server': None, 'port': None, 'node': None, 'thread': None}


# ---- binary messages: [uint32 header length][JSON header][blobs]; blobs are referenced as {"__b": [offset, length]}
def _encode(obj):
    blobs = []
    size = [0]

    def walk(o):
        if isinstance(o, (bytes, bytearray, memoryview)):
            b = bytes(o)
            ref = {'__b': [size[0], len(b)]}
            blobs.append(b)
            size[0] += len(b)
            return ref
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(v) for v in o]
        if hasattr(o, 'tolist'):          # numpy scalars / arrays
            return walk(o.tolist())
        return o
    head = json.dumps(walk(obj)).encode()
    return struct.pack('<I', len(head)) + head + b''.join(blobs)


def _decode(data):
    n = struct.unpack_from('<I', data, 0)[0]
    head = json.loads(data[4:4 + n].decode())
    body = data[4 + n:]

    def walk(o):
        if isinstance(o, dict):
            if '__b' in o and len(o) == 1:
                a, l = o['__b']
                return body[a:a + l]
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return walk(head)


# ---- the Nuke node (optional) ----------------------------------------------------------------------------
def _in_main(fn, *a):
    try:
        import nuke
        return nuke.executeInMainThreadWithResult(fn, args=a)
    except ImportError:
        return fn(*a)


def node_settings():
    node = _STATE['node']
    if node is None:
        return None
    return _in_main(mf._nk_settings, node)


def node_set(values):
    """Write knob values from the viewer back to the node (same keys as motefield.DEFAULTS)."""
    node = _STATE['node']
    if node is None:
        return False

    def apply(vals):
        for k, v in vals.items():
            if k in ('fade_xn', 'fade_yn', 'fade_zn', 'fade_xp', 'fade_yp', 'fade_zp'):
                continue
            kn = node.knob(k)
            if kn is None:
                continue
            try:
                kn.setValue(list(v) if isinstance(v, (list, tuple)) else v)
            except Exception:
                pass
        for key, names in (('fade_neg', ('fade_xn', 'fade_yn', 'fade_zn')), ('fade_pos', ('fade_xp', 'fade_yp', 'fade_zp'))):
            if all(n in vals for n in names) and node.knob(key) is not None:
                node[key].setValue([vals[n] for n in names])
        return True
    return _in_main(apply, values)


def write_frame(a):
    """A frame rendered on the GPU by the live viewer -> dust.####.exr in the node's cache folder (the same file the
    node's Python render writes), then the node's Read reloads. Seconds for a shot instead of minutes."""
    import numpy as np
    w, h, f = int(a['w']), int(a['h']), int(a['frame'])
    held = np.frombuffer(bytes(a['held']), np.float32).reshape(h, w, 4)[::-1, :, :3]     # GPU rows run bottom-up
    free = np.frombuffer(bytes(a['free']), np.float32).reshape(h, w, 4)[::-1, :, :3]
    st = node_settings() or {}
    cache = st.get('cache') or os.path.join(mfweb.ROOT, 'cache')
    if not os.path.isdir(cache):
        os.makedirs(cache)
    path = os.path.join(cache, 'dust.%04d.exr' % f)
    mf.write_dust(path, np.ascontiguousarray(held), np.ascontiguousarray(free))
    if a.get('last') and _STATE['node'] is not None:
        mf._nk_reload(_STATE['node'])
    return {'path': path}


# ---- the API: the same jobs as worker.js, plus a few that only make sense with a real file system --------------
def _job(cmd, a):
    p = lambda: json.dumps(a['p'])
    if cmd == 'ping':
        return {'ok': True, 'native': True, 'node': _STATE['node'] is not None, 'version': mf.__version__}
    if cmd == 'init':
        return json.loads(mfweb.schema())
    if cmd == 'standin':
        return json.loads(mfweb.setup_standin(a.get('width', 1280), a.get('view') or 'wide'))
    if cmd == 'fit':
        return json.loads(mfweb.fit(p()))
    if cmd == 'plate':
        return mfweb.plate(p(), a.get('pattern') or '', a['frame'])
    if cmd == 'render':
        return mfweb.render(p(), a['frame'])
    if cmd == 'scene':
        return mfweb.scene(p(), a.get('cloud') is not False)
    if cmd == 'pinit':
        return mfweb.preview_init(p())
    if cmd == 'plight':
        return mfweb.preview_light(p())
    if cmd == 'pbounce':
        return mfweb.preview_bounce(p())
    if cmd == 'ppush':
        return mfweb.preview_pushers(p(), a['f0'], a['f1'])
    if cmd == 'fimages':
        return mfweb.frame_images(p(), a['frame'], a.get('pattern') or '')
    if cmd == 'bokeh':
        return mfweb.set_bokeh_image(a.get('w') or 0, a.get('h') or 0, a.get('data'))
    if cmd == 'exr':
        return mfweb.save_exr(p(), a['frame'])
    if cmd == 'setpath':
        return json.loads(mfweb.use_path(a['kind'], a['path']))
    if cmd == 'write_frame':
        return write_frame(a)
    if cmd == 'cache_info':
        st = node_settings() or {}
        return {'cache': st.get('cache') or os.path.join(mfweb.ROOT, 'cache'), 'node': _STATE['node'] is not None}
    if cmd == 'node_get':
        return node_settings()
    if cmd == 'node_set':
        return node_set(a.get('values') or {})
    raise ValueError('Unknown request: %s' % cmd)


def _handler_class():
    try:
        from http.server import BaseHTTPRequestHandler
    except ImportError:          # Python 2 (old Nuke)
        from BaseHTTPServer import BaseHTTPRequestHandler
    types = {'.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm',
             '.json': 'application/json', '.py': 'text/plain', '.md': 'text/plain', '.png': 'image/png', '.css': 'text/css'}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split('?')[0].lstrip('/') or 'index.html'
            full = os.path.normpath(os.path.join(HERE, path))
            if not full.startswith(HERE) or not os.path.isfile(full):
                self.send_error(404)
                return
            with io.open(full, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header('Content-Type', types.get(os.path.splitext(full)[1], 'application/octet-stream'))
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not self.path.startswith('/api/'):
                self.send_error(404)
                return
            cmd = self.path[5:].split('?')[0]
            n = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(n) if n else b''
            try:
                args = _decode(body) if body else {}
                with _LOCK:
                    res = _job(cmd, args)
                out, code = _encode({'ok': True, 'result': res}), 200
            except Exception as e:
                traceback.print_exc()
                out, code = _encode({'ok': False, 'error': str(e)}), 200
            self.send_response(code)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    return H


def start(port=0, node=None):
    """Starts the server (once) and returns its URL."""
    _STATE['node'] = node
    if _STATE['server'] is None:
        try:
            from http.server import ThreadingHTTPServer as S
        except ImportError:
            try:
                from http.server import HTTPServer as S
            except ImportError:
                from BaseHTTPServer import HTTPServer as S
        srv = S(('127.0.0.1', int(port)), _handler_class())
        _STATE['server'], _STATE['port'] = srv, srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, name='MoteFieldLive')
        t.daemon = True
        t.start()
        _STATE['thread'] = t
    return 'http://127.0.0.1:%d/?live=1' % _STATE['port']


def stop():
    if _STATE['server'] is not None:
        _STATE['server'].shutdown()
        _STATE['server'] = None


def open(node=None, port=0):        # noqa: A001  (named for the Nuke button)
    """Opens the live viewer for a MoteField node: in a Nuke panel if this Nuke has QtWebEngine, else in your browser."""
    url = start(port, node)
    try:
        from PySide2 import QtWebEngineWidgets, QtWidgets, QtCore     # noqa: F401
        return _panel(url)
    except Exception:
        webbrowser.open(url)
        return url


_PANELS = []


def _panel(url):
    from PySide2 import QtWebEngineWidgets, QtWidgets, QtCore
    w = QtWidgets.QWidget()
    w.setWindowTitle('MoteField Live')
    w.setAttribute(QtCore.Qt.WA_DeleteOnClose)
    lay = QtWidgets.QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    view = QtWebEngineWidgets.QWebEngineView(w)
    s = view.settings()
    for attr in ('WebGLEnabled', 'Accelerated2dCanvasEnabled'):
        try:
            s.setAttribute(getattr(QtWebEngineWidgets.QWebEngineSettings, attr), True)
        except Exception:
            pass
    view.load(QtCore.QUrl(url))
    lay.addWidget(view)
    w.resize(1500, 900)
    w.show()
    _PANELS.append(w)
    return w


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='MoteField live viewer (local server).')
    ap.add_argument('--port', type=int, default=8790)
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()
    u = start(a.port)
    print('MoteField Live: %s  (Ctrl+C to stop)' % u)
    if not a.no_browser:
        webbrowser.open(u)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stop()
