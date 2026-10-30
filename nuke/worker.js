// Runs motefield.py (the Nuke tool, unchanged) in Pyodide, off the page's main thread.
importScripts('pyodide/pyodide.js');

let py = null, mf = null;
const conv = { dict_converter: Object.fromEntries };

function say(type, data) { postMessage(Object.assign({ type }, data)); }

async function boot() {
  say('boot', { msg: 'Loading the Python runtime…', x: 0.05 });
  py = await loadPyodide({ indexURL: new URL('pyodide/', self.location.href).href, stdout: () => {}, stderr: () => {} });
  say('boot', { msg: 'Loading numpy…', x: 0.55 });
  await py.loadPackage('numpy', { messageCallback: () => {} });
  say('boot', { msg: 'Loading MoteField…', x: 0.85 });
  for (const f of ['motefield.py', 'mfweb.py']) {
    const r = await fetch(f + '?v=' + Date.now());
    if (!r.ok) throw new Error('Could not load ' + f);
    py.FS.writeFile('/home/pyodide/' + f, await r.text());
  }
  mf = py.pyimport('mfweb');
  return JSON.parse(mf.schema());
}

function lastLine(err) {
  const s = String(err && err.message || err);
  const lines = s.trim().split('\n').filter(Boolean);
  return lines[lines.length - 1].replace(/^\w*Error: /, '');
}

function out(res) {
  if (res == null) return null;
  const o = res.toJs ? res.toJs(conv) : res;
  if (res.destroy) res.destroy();
  return o;
}

const jobs = {
  init: () => boot(),
  standin: ({ width }) => JSON.parse(mf.setup_standin(width)),
  files: ({ kind, files }) => {
    const names = py.toPy(files.map(f => f.name));
    const blobs = py.toPy(files.map(f => new Uint8Array(f.data)));
    try { return JSON.parse(mf.add_files(kind, names, blobs)); }
    finally { names.destroy(); blobs.destroy(); }
  },
  fit: ({ p }) => JSON.parse(mf.fit(JSON.stringify(p))),
  plate: ({ p, pattern, frame }) => out(mf.plate(JSON.stringify(p), pattern || '', frame)),
  render: ({ p, frame, id }) => {
    const cb = (x, msg) => { say('progress', { id, x, msg }); return true; };
    return out(mf.render(JSON.stringify(p), frame, cb));
  },
  exr: ({ p, frame }) => out(mf.save_exr(JSON.stringify(p), frame)),
};

self.onmessage = async (e) => {
  const { id, cmd, args } = e.data;
  try {
    const result = await jobs[cmd](args || {});
    const tr = [];
    if (result && typeof result === 'object')
      for (const v of Object.values(result)) if (v && v.buffer instanceof ArrayBuffer) tr.push(v.buffer);
    if (result instanceof Uint8Array) tr.push(result.buffer);
    postMessage({ type: 'done', id, ok: true, result }, tr);
  } catch (err) {
    postMessage({ type: 'done', id, ok: false, error: lastLine(err), detail: String(err && err.message || err) });
  }
};
