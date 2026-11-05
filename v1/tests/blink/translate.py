"""Turns a Blink kernel into a C++ program (with blink.h), so the tests can run the node's kernels without Nuke.

    python3 translate.py kernel.blink out.cpp

The program reads a job file: lines 'param NAME v1 [v2 v3 v4]', 'input NAME path', 'size W H', 'output path'.
Images are raw float32 RGBA with an int32 width and height first (see blink.h load_raw / save_raw).
"""
import re
import sys

TYPES = {'float': 1, 'int': 1, 'bool': 1, 'float2': 2, 'float3': 3, 'float4': 4, 'int2': 2, 'int4': 4}


def translate(src):
    m = re.search(r'kernel\s+(\w+)\s*:\s*ImageComputationKernel\s*<\s*\w+\s*>', src)
    name = m.group(1)
    body = src[:m.start()] + 'struct ' + name + src[m.end():]
    images = re.findall(r'Image\s*<[^>]*>\s*(\w+)\s*;', body)
    body = re.sub(r'Image\s*<[^>]*>\s*(\w+)\s*;', r'ImgRef \1;', body)
    # params: the declarations between 'param:' and the next section
    pm = re.search(r'param\s*:(.*?)(?:local\s*:|void\s+define)', body, re.S)
    params = []
    if pm:
        for t, n in re.findall(r'(\w+)\s+(\w+)\s*;', pm.group(1)):
            params.append((t, n))
    body = re.sub(r'\bparam\s*:', 'public:', body)
    body = re.sub(r'\blocal\s*:', 'public:', body)
    body = re.sub(r'SampleType\s*\(\s*\w+\s*\)', 'float4', body)
    body = body.replace('struct ' + name, 'struct ' + name + ' : KernelBase { public:', 1)
    # the kernel's opening brace now follows '{ public:' : drop it
    body = re.sub(r'(struct \w+ : KernelBase \{ public:)\s*\{', r'\1', body, count=1)
    out = ['#include "blink.h"', '#include <omp.h>', body, '']
    out.append('static void set_param(%s& k, const std::string& n, const std::vector<double>& v) {' % name)
    for t, n in params:
        c = TYPES[t]
        if c == 1:
            out.append('  if (n == "%s") { k.%s = (%s)v[0]; return; }' % (n, n, t))
        else:
            comps = 'xyzw'[:c]
            base = 'float' if t.startswith('float') else 'int'
            out.append('  if (n == "%s") { %s return; }' % (n, ' '.join('k.%s.%s = (%s)v[%d];' % (n, comps[i], base, i)
                                                                    for i in range(c))))
    out.append('  fprintf(stderr, "unknown param %s\\n", n.c_str()); exit(2);')
    out.append('}')
    dst = images[-1]
    ins = images[:-1]
    out.append('''
int main(int argc, char** argv) {
  %(K)s k;
  k.define();
  FILE* f = fopen(argv[1], "r");
  char line[4096];
  std::vector<Img*> inputs;
  std::string outpath;
  int W = -1, H = -1;
  Img* store[16] = {0};
  const char* names[] = {%(names)s};
  int nin = %(nin)d;
  while (fgets(line, sizeof line, f)) {
    char kind[64], nm[256];
    if (sscanf(line, "%%63s", kind) != 1) continue;
    std::string kd(kind);
    if (kd == "param") {
      char* p = line + 5;
      int off; sscanf(p, "%%255s%%n", nm, &off); p += off;
      std::vector<double> v; double x;
      while (sscanf(p, "%%lf%%n", &x, &off) == 1) { v.push_back(x); p += off; }
      set_param(k, nm, v);
    } else if (kd == "input") {
      char path[2048]; sscanf(line + 5, "%%255s %%2047s", nm, path);
      int i = 0; for (; i < nin; i++) if (std::string(names[i]) == nm) break;
      if (i == nin) { fprintf(stderr, "unknown input %%s\\n", nm); return 2; }
      store[i] = new Img(); if (!load_raw(*store[i], path)) { fprintf(stderr, "cannot read %%s\\n", path); return 2; }
    } else if (kd == "size") { sscanf(line + 4, "%%d %%d", &W, &H); }
    else if (kd == "output") { char path[2048]; sscanf(line + 6, "%%2047s", path); outpath = path; }
  }
  fclose(f);
  for (int i = 0; i < nin; i++) if (!store[i]) { fprintf(stderr, "missing input %%s\\n", names[i]); return 2; }
%(bind)s
  if (W < 0) { W = %(first)s; H = %(firstH)s; }
  Img out; out.alloc(W, H);
  k.%(dst)s.bind(&out);
  k.init();
  #pragma omp parallel for schedule(dynamic, 4)
  for (int y = 0; y < H; y++) {
    %(K)s kk = k;
    for (int x = 0; x < W; x++) {
%(setpos)s
      kk.process(int2(x, y));
    }
  }
  save_raw(out, outpath.c_str());
  return 0;
}
''' % dict(K=name, names=', '.join('"%s"' % n for n in ins) or '""', nin=len(ins), dst=dst,
           bind='\n'.join('  k.%s.bind(store[%d]);' % (n, i) for i, n in enumerate(ins)),
           first='store[0]->W' if ins else '1', firstH='store[0]->H' if ins else '1',
           setpos='\n'.join('      kk.%s.cx = x; kk.%s.cy = y;' % (n, n) for n in images)))
    return '\n'.join(out)


if __name__ == '__main__':
    open(sys.argv[2], 'w').write(translate(open(sys.argv[1]).read()))
