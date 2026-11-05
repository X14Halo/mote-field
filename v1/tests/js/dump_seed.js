// Writes what viewer/js/mfseed.js makes, as raw bytes, so tests/test_rng_parity.py can compare it with the Python.
//   node dump_seed.js cases.json outdir
// cases.json: {"dust": [{"e": {...engine values...}, "n": 123 or null}, ...], "modes": [seed, ...], "scalar": [x, ...]}
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { dustStart, modeTables, xexp, xlog, xpow, pcg, hashWords } from '../../viewer/js/mfseed.js';

const [cases, out] = [JSON.parse(readFileSync(process.argv[2], 'utf8')), process.argv[3]];
const put = (name, arr) => writeFileSync(join(out, name), Buffer.from(arr.buffer, arr.byteOffset, arr.byteLength));

cases.dust.forEach((c, i) => {
  const s = dustStart(c.e, c.n === null ? undefined : c.n);
  for (const k of ['local', 'h', 'kind', 'settle', 'tau', 'grit_d']) put(`dust${i}_${k}.bin`, s[k]);
  writeFileSync(join(out, `dust${i}_meta.json`), JSON.stringify({ n: s.n, size: s.size }));
});
cases.modes.forEach((seed) => modeTables(seed).forEach((t, j) => put(`modes${seed}_${j}.bin`, t)));
const xs = Float64Array.from(cases.scalar);
put('xexp.bin', xs.map((x) => xexp(x)));
put('xlog.bin', xs.map((x) => (x > 0 ? xlog(x) : 0)));
put('xpow.bin', xs.map((x) => xpow(Math.abs(x), 0.37 + Math.abs(x) % 3)));
put('pcg.bin', Uint32Array.from(cases.ints, (v) => pcg(v)));
put('hash.bin', Uint32Array.from(cases.ints, (v) => hashWords(v, 0x44555354, v ^ 0x5bd1e995, 7)));
