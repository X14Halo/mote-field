// MoteField GPU preview: the same simulation and mote model as motefield.py, run in WebGL2 so playback is real time.
//
//  * The starting state (positions, per-mote random numbers, the eddy modes) comes from Python, so the GPU moves
//    exactly the motes the Python render does; it integrates in 32-bit floats, so after many frames individual motes
//    drift a little from the Python result while the motion stays the same.
//  * Each frame: light visibility pass, sub-steps of the flow, then a "prep" pass that does render_frame()'s per-mote
//    maths (projection, circle of confusion, size, phase function, glints, holdouts, shutter streak), then instanced
//    quads splat each mote's shutter / fibre samples as a lens aperture disc (or a small gaussian) into float buffers.
//  * The stand-in plate and its distance image are ray traced on the GPU from the same boxes as the Python stand-in.
'use strict';

const MF_NS = 16;        // samples per mote along the shutter / along a fibre (Python allows up to 24 x 24)
const MF_TW = 256;       // state textures are MF_TW wide

const GLSL_COMMON = `#version 300 es
precision highp float; precision highp int; precision highp sampler2D;
const float PI = 3.14159265358979;
float smooth01(float x){ x = clamp(x, 0.0, 1.0); return x*x*(3.0-2.0*x); }
vec3 tumbleAxis(float h4, float h5, float h6, float rate, float t){
  float a = 2.0*PI*(h4 + rate*t);
  float b = 2.0*PI*h5 + 4.1*rate*t*(0.5 + h6);
  return vec3(sin(a)*cos(b), cos(a), sin(a)*sin(b));
}
ivec2 texel(int i){ return ivec2(i % ${MF_TW}, i / ${MF_TW}); }
// motefield.cam_fade: 0 nearer than n, 1 from f (both 0 = off)
float camFade(float d, float n, float f){ return f > n ? smooth01((d - n) / (f - n)) : (n > 0.0 ? (d >= n ? 1.0 : 0.0) : 1.0); }
`;

const VS_FULL = `#version 300 es
void main(){ vec2 p = vec2((gl_VertexID & 1) * 4 - 1, (gl_VertexID & 2) * 2 - 1); gl_Position = vec4(p, 0.0, 1.0); }`;

// ---- light visibility of each mote (refresh_light) -------------------------------------------------
const FS_VIS = GLSL_COMMON + `
uniform sampler2D uPos; uniform mat3 uR; uniform vec3 uCenter; uniform float uSu;
uniform int uMode; uniform vec3 uL;
uniform sampler2D uBoxes; uniform int uNB;
uniform sampler2D uLM; uniform vec3 uLa, uLb; uniform vec2 uLlo; uniform float uLcell, uLbias; uniform int uLres;
uniform int uInterior; uniform vec3 uBlo, uBhi;
uniform sampler2D uSky0, uSky1, uSky2; uniform int uNV; uniform vec3 uVC[12]; uniform float uVT[6]; uniform vec2 uVS[3];
uniform vec3 uTaps[6]; uniform int uNT;
out vec4 o;
bool skyAt(int k, vec3 Q){
  vec3 pos = uVC[k*4], r = uVC[k*4+1], u = uVC[k*4+2], b = uVC[k*4+3];
  vec3 d = Q - pos; float z = -dot(d, b);
  if (z <= 0.0) return false;
  float zs = max(z, 1e-6);
  vec2 S = uVS[k];
  float px = (dot(d, r)/zs/uVT[k*2]*0.5 + 0.5)*S.x, py = (0.5 - dot(d, u)/zs/uVT[k*2+1]*0.5)*S.y;
  if (px < 0.0 || px >= S.x || py < 0.0 || py >= S.y) return false;
  ivec2 ip = ivec2(clamp(int(px), 0, int(S.x)-1), clamp(int(py), 0, int(S.y)-1));
  float v = k == 0 ? texelFetch(uSky0, ip, 0).r : k == 1 ? texelFetch(uSky1, ip, 0).r : texelFetch(uSky2, ip, 0).r;
  return v > 0.001;      // R8 texture: a stored 1 reads back as 1/255
}
float visAt(vec3 P){
  float vis = 1.0;
  if (uMode == 1) {
    vec3 Ls = vec3(abs(uL.x) < 1e-9 ? 1e-9 : uL.x, abs(uL.y) < 1e-9 ? 1e-9 : uL.y, abs(uL.z) < 1e-9 ? 1e-9 : uL.z);
    vec3 inv = 1.0 / Ls;
    for (int k = 0; k < uNB; k++) {
      vec3 mn = texelFetch(uBoxes, ivec2(k, 0), 0).xyz, mx = texelFetch(uBoxes, ivec2(k, 1), 0).xyz;
      vec3 t1 = (mn - P) * inv, t2 = (mx - P) * inv;
      vec3 tn = min(t1, t2), tf = max(t1, t2);
      float a = max(max(tn.x, tn.y), tn.z), b = min(min(tf.x, tf.y), tf.z);
      if (b >= max(a, 1e-3)) { vis = 0.0; break; }
    }
  } else if (uMode == 2) {
    float u = dot(P, uLa), v = dot(P, uLb), d = dot(P, uL);
    int iu = int((u - uLlo.x) / uLcell), iv = int((v - uLlo.y) / uLcell);     // truncates like numpy's astype
    bool ok = iu >= 0 && iu < uLres && iv >= 0 && iv < uLres;
    float occ = texelFetch(uLM, ivec2(clamp(iu, 0, uLres-1), clamp(iv, 0, uLres-1)), 0).r;
    if (ok && occ > d + uLbias) vis = 0.0;
    if (uInterior == 1) {
      vec3 Ls = vec3(abs(uL.x) < 1e-9 ? 1e-9 : uL.x, abs(uL.y) < 1e-9 ? 1e-9 : uL.y, abs(uL.z) < 1e-9 ? 1e-9 : uL.z);
      vec3 tt = vec3(Ls.x > 0.0 ? (uBhi.x - P.x)/Ls.x : (uBlo.x - P.x)/Ls.x,
                     Ls.y > 0.0 ? (uBhi.y - P.y)/Ls.y : (uBlo.y - P.y)/Ls.y,
                     Ls.z > 0.0 ? (uBhi.z - P.z)/Ls.z : (uBlo.z - P.z)/Ls.z);
      float t = min(min(tt.x, tt.y), tt.z);
      vec3 Q = P + max(t, 0.0) * uL + uL * (length(uBhi - uBlo) * 0.01);
      bool open_ = false;
      for (int k = 0; k < 3; k++) if (k < uNV && skyAt(k, Q)) open_ = true;
      if (!open_) vis = 0.0;
    }
  }
  return vis;
}
void main(){
  vec3 local = texelFetch(uPos, ivec2(gl_FragCoord.xy), 0).xyz;
  vec3 P = (uR * local + uCenter) / uSu;
  float v = 0.0;
  for (int k = 0; k < 6; k++) { if (k >= uNT) break; v += visAt(P + uTaps[k]); }      // soft shadow edges: several taps across the sun
  o = vec4(v / float(max(uNT, 1)), 0.0, 0.0, 1.0);
}`;

// ---- one sub-step of DustSim.step() ------------------------------------------------------------------
const FS_STEP = GLSL_COMMON + `
uniform sampler2D uPos, uVel, uVis, uSt;
uniform mat3 uR; uniform vec3 uCenter, uSize, uWind;
uniform float uT, uDt, uAmp0, uAmp1, uThermal, uSettle;
uniform vec4 uM0[80]; uniform int uN0; uniform vec4 uM1[40]; uniform int uN1;
uniform sampler2D uPush; uniform int uNP; uniform float uPAmt, uPRs;     // pushers: row 0 centre + radius (m), row 1 velocity + weight
layout(location=0) out vec4 oPos; layout(location=1) out vec4 oVel;
void main(){
  ivec2 ip = ivec2(gl_FragCoord.xy);
  vec3 local = texelFetch(uPos, ip, 0).xyz, V = texelFetch(uVel, ip, 0).xyz;
  vec4 st = texelFetch(uSt, ip, 0);          // type, settle factor, tau
  float vis = texelFetch(uVis, ip, 0).r;
  vec3 Pw = uR * local + uCenter;
  vec3 tgt = uWind;
  vec3 q = Pw - uWind * uT;
  if (uAmp0 > 0.0) {
    vec3 f = vec3(0.0);
    for (int m = 0; m < 40; m++) { if (m >= uN0) break; vec4 a = uM0[m*2], b = uM0[m*2+1];
      f += sin(dot(q, a.xyz) + a.w + b.w * (uT * 0.5 * uAmp0)) * b.xyz; }
    tgt += uAmp0 * 0.6 * f;
  }
  if (uAmp1 > 0.0) {
    vec3 f = vec3(0.0);
    for (int m = 0; m < 20; m++) { if (m >= uN1) break; vec4 a = uM1[m*2], b = uM1[m*2+1];
      f += sin(dot(q, a.xyz) + a.w + b.w * (uT * 0.5 * uAmp1)) * b.xyz; }
    tgt += uAmp1 * 0.6 * f * (st.x >= 2.0 ? 1.6 : 1.0);
  }
  tgt.y += uThermal * 0.01 * vis - uSettle * 0.01 * st.y;
  float churn = 0.0;
  for (int i = 0; i < 144; i++) { if (i >= uNP) break;         // DustSim.step: flow around the mover now, a fading wake behind
    vec4 pc = texelFetch(uPush, ivec2(i, 0), 0), pv = texelFetch(uPush, ivec2(i, 1), 0);
    vec3 d = Pw - pc.xyz, U = pv.xyz; float dist = length(d) + 1e-6, a = pc.w * uPRs;
    if (pv.w >= 0.999) { vec3 n = d / dist; float k3 = 0.5 * pow(a / max(dist, a), 3.0) * uPAmt; tgt += k3 * (3.0 * dot(n, U) * n - U); }
    else { float qw = dist / a, g = exp(-qw * qw) * pv.w * uPAmt; tgt += U * (g * 0.35); churn += g * length(U); } }   // WAKE_DRAG
  if (churn > 0.0) {                                            // the wake stirs the air: the micro field at strength 1, x WAKE_TURB
    vec3 f = vec3(0.0);
    for (int m = 0; m < 20; m++) { if (m >= uN1) break; vec4 a = uM1[m*2], b = uM1[m*2+1];
      f += sin(dot(q, a.xyz) + a.w + b.w * (uT * 0.5)) * b.xyz; }
    tgt += 0.6 * f * churn * 0.6;
  }
  float k = 1.0 - exp(-uDt / st.z);
  V += (tgt - V) * k;
  local += (transpose(uR) * V) * uDt;
  local = mod(local + uSize * 0.5, uSize) - uSize * 0.5;
  oPos = vec4(local, 1.0); oVel = vec4(V, 1.0);
}`;

// ---- render_frame()'s per-mote maths ---------------------------------------------------------------
const FS_PREP = GLSL_COMMON + `
uniform sampler2D uPos, uVel, uVis, uH0, uH1, uH2, uDist;
uniform int uN;
uniform mat3 uR; uniform vec3 uCenter, uSize; uniform float uSu;
uniform float uFade; uniform vec3 uFadeN, uFadeP;
uniform vec3 uC[12]; uniform float uCT[6];        // cameras: now, shutter open, shutter close (pos, right, up, back) + tan(h/2), tan(v/2)
uniform vec2 uWH, uWH0;
uniform float uFocal, uFilm, uFstop, uFocusM, uMaxR, uFocalPx; uniform int uDof;
uniform float uTOpen, uTClose, uSpf, uTsec, uShut; uniform int uShape;
uniform float uFC, uFront, uBack;
float shutterW(float u){ return uShape == 1 ? 1.0 / (1.0 + exp(-9.0 * (u - 0.55))) : 1.0; }
uniform vec3 uL, uSun, uTint; uniform float uBright, uForward, uGlint, uTumble;
uniform float uSizeMin, uSizeMax, uSizeBias, uFlakeScale, uFiberScale, uFluffScale;
uniform int uHold, uFg; uniform float uFgNear, uFgFar;
uniform vec3 uBP[24], uBN[24], uBF[24], uBAmb; uniform int uNB2; uniform float uFillK; uniform float uCFN, uCFF;
uniform vec4 uShA[8], uShB[8], uShC[8]; uniform int uNSh;      // hide shapes: centre + kind, size + Y turn, falloff + mode + target
// motefield.shape_mask: how much of a point (scene units) the hide shapes leave; tgt 1 = dust, 2 = embers
float shapeMask(vec3 P, float tgt){
  float w = 1.0;
  for (int k = 0; k < 8; k++) {
    if (k >= uNSh) break;
    vec4 A = uShA[k], B = uShB[k], C = uShC[k];
    if (C.z != 3.0 && C.z != tgt) continue;
    float ca = cos(B.w), sa = sin(B.w); vec3 d = P - A.xyz;
    vec3 q = vec3(ca * d.x - sa * d.z, d.y, sa * d.x + ca * d.z), h = B.xyz * 0.5;
    float sd;
    if (A.w > 0.5) { vec3 e = abs(q) - h; sd = length(max(e, 0.0)) + min(max(max(e.x, e.y), e.z), 0.0); }
    else sd = (length(q / h) - 1.0) * min(min(h.x, h.y), h.z);
    float ins = 1.0 - (C.x > 0.0 ? smooth01(sd / C.x + 0.5) : (sd >= 0.0 ? 1.0 : 0.0));
    w *= C.y > 0.5 ? ins : 1.0 - ins;
  }
  return w;
}

uniform float uSteady;
float holdAt(vec2 xy, float dist){
  ivec2 sp = ivec2(clamp(int(xy.x / uWH.x * uWH0.x), 0, int(uWH0.x) - 1), clamp(int(xy.y / uWH.y * uWH0.y), 0, int(uWH0.y) - 1));
  float surf = texelFetch(uDist, sp, 0).r;
  float occl = surf > 0.0 ? smooth01(((dist - surf) / max(surf, 1e-6) + 0.01) / 0.04) : 0.0;
  float fgw = 1.0;
  if (uFg == 1 && uFgFar > uFgNear) fgw = smooth01((surf - uFgNear) / (uFgFar - uFgNear));
  else if (uFg == 1) fgw = surf >= uFgNear ? 1.0 : 0.0;
  return 1.0 - occl * fgw;
}
// steady: averaged over the mote's blur, so it slides behind an edge instead of blinking off
float holdout(vec2 xy, float dist, float Reff){
  if (uSteady <= 0.0) return holdAt(xy, dist);
  float r = uSteady * Reff * 0.7071;
  return (holdAt(xy, dist) + holdAt(xy + vec2(r, r), dist) + holdAt(xy + vec2(-r, r), dist) + holdAt(xy + vec2(r, -r), dist) + holdAt(xy + vec2(-r, -r), dist)) / 5.0;
}

layout(location=0) out vec4 o0; layout(location=1) out vec4 o1; layout(location=2) out vec4 o2; layout(location=3) out vec4 o3;
vec3 proj(int k, vec3 P){
  vec3 d = P - uC[k*4];
  float z = -dot(d, uC[k*4+3]), zs = max(z, 1e-6);
  return vec3((dot(d, uC[k*4+1])/zs/uCT[k*2]*0.5 + 0.5)*uWH.x, (0.5 - dot(d, uC[k*4+2])/zs/uCT[k*2+1]*0.5)*uWH.y, z);
}
float edgeW(vec3 local){
  vec3 half_ = uSize * 0.5; float w = 1.0; float base = max(uFade, 0.0) * uSu;
  for (int ax = 0; ax < 3; ax++) {
    float fn = base * max(uFadeN[ax], 0.0), fp = base * max(uFadeP[ax], 0.0);
    if (fn > 1e-9) w *= smooth01((local[ax] + half_[ax]) / fn);
    if (fp > 1e-9) w *= smooth01((half_[ax] - local[ax]) / fp);
  }
  return w;
}
void cull(){ o0 = vec4(0.0); o1 = vec4(0.0, 0.0, 0.0, -1.0); o2 = vec4(0.0); o3 = vec4(0.0); }
void main(){
  ivec2 ip = ivec2(gl_FragCoord.xy);
  int i = ip.y * ${MF_TW} + ip.x;
  if (i >= uN) { cull(); return; }
  vec3 local = texelFetch(uPos, ip, 0).xyz;
  vec3 V = texelFetch(uVel, ip, 0).xyz / uSu;
  float vis = texelFetch(uVis, ip, 0).r;
  vec4 h0 = texelFetch(uH0, ip, 0), h1 = texelFetch(uH1, ip, 0), h2 = texelFetch(uH2, ip, 0);
  float hh[10] = float[10](h0.x, h0.y, h0.z, h0.w, h1.x, h1.y, h1.z, h1.w, h2.x, h2.y);
  int typ = int(h2.z + 0.5);
  vec3 P = (uR * local + uCenter) / uSu;
  float weight = edgeW(local);
  vec3 cpos = uC[0], cback = uC[3];
  vec3 d = P - cpos;
  float zc = -dot(d, cback);
  weight *= smooth01((zc * uSu - 0.06) / 0.24);          // motes fade out as they come right up to the lens
  weight *= camFade(length(d), uCFN, uCFF) * shapeMask(P, 1.0);
  if (weight <= 1e-4 || zc <= 0.05 / uSu) { cull(); return; }
  vec3 pp = proj(0, P);
  float margin = uMaxR + 40.0;
  if (pp.x <= -margin || pp.x >= uWH.x + margin || pp.y <= -margin || pp.y >= uWH.y + margin) { cull(); return; }
  float z_m = zc * uSu;
  float dist = length(P - cpos), dist_m = dist * uSu;
  vec3 Vd = (cpos - P) / dist;
  float coc = 0.0;
  if (uDof == 1) {
    vec2 dc = pp.xy - uWH * 0.5; float rn2 = dot(dc, dc) / dot(uWH * 0.5, uWH * 0.5);
    float s1 = uFocusM * 1000.0 * max(1.0 + uFC * rn2, 0.05), s2 = max(z_m, 0.02) * 1000.0;
    coc = (uFocal / uFstop) * uFocal * (s2 - s1) / (s2 * max(s1 - uFocal, 1.0)) / uFilm * uWH.x;
    coc *= coc < 0.0 ? uFront : uBack; }
  float R = clamp(abs(coc) * 0.5, 0.25 + 0.5 * uSteady, uMaxR);
  float ppm = uFocalPx / max(z_m, 1e-3);
  float u = hh[1];
  float rate = uTumble * (typ == 1 ? 0.12 + 0.78*hh[3] : typ == 0 ? 0.1 + 0.6*hh[3] : 0.03 + 0.27*hh[3]);
  vec3 ax = tumbleAxis(hh[4], hh[5], hh[6], rate, uTsec);
  float sigma, rs, gph = uForward; vec3 tcol = vec3(1.0);
  float ln = 0.0;
  if (typ == 0) {
    float dg = uSizeMin * pow(uSizeMax / max(uSizeMin, 1e-3), pow(u, uSizeBias));
    sigma = (dg / 40.0) * (dg / 40.0) * (1.0 + 0.12 * (1.0 - uSteady) * sin(2.0*PI*(rate * uTsec * 1.3 + hh[7])));
    rs = 0.5 * dg * 1e-6 * ppm;
  } else if (typ == 1) {
    float df = 30.0 * pow(8.0, pow(u, 2.2)) * uFlakeScale;
    float nl = abs(dot(ax, uL)), nv = abs(dot(ax, Vd));
    nl += (0.5 - nl) * uSteady; nv += (0.5 - nv) * uSteady;          // a tumbling flake's flash, toward its average
    sigma = (df*df / 1600.0) * (0.4 * nl * (0.25 + 0.75*nv) + 0.03);
    rs = 0.5 * df * 1e-6 * ppm; gph = uForward * 0.85;
    tcol = hh[7] > 0.5 ? vec3(1.04, 1.0, 0.94) : vec3(0.95, 0.95, 0.97);
  } else if (typ == 2) {
    ln = 300.0 * pow(8.0, pow(u, 1.8)) * uFiberScale;
    float th = 8.0 + 12.0 * hh[8], tl = dot(ax, uL);
    sigma = (ln * th / 1600.0) * sqrt(max(1.0 - tl*tl, 0.03)) * 0.5;
    rs = th * 1e-6 * ppm; gph = uForward * 0.5;
    float h9 = hh[9];
    tcol = h9 < 0.4 ? vec3(0.95, 0.95, 0.93) : h9 < 0.58 ? vec3(0.5, 0.58, 0.82) : h9 < 0.7 ? vec3(0.85, 0.45, 0.42) : h9 < 0.84 ? vec3(0.36, 0.34, 0.32) : vec3(0.82, 0.74, 0.55);
  } else {
    float dl = 200.0 * pow(6.0, u*u) * uFluffScale;
    sigma = (dl*dl / 1600.0) * 0.07; rs = 0.5 * dl * 1e-6 * ppm; gph = uForward * 0.45; tcol = vec3(0.9, 0.9, 0.93);
  }
  float c = dot(-uL, Vd);
  float hg = (1.0 - gph*gph) / pow(max(1.0 + gph*gph - 2.0*gph*c, 1e-4), 1.5);
  vec3 rad = uSun * (vis * hg);
  if (typ == 1 && vis > 0.0 && uGlint * (1.0 - uSteady) > 0.0) {
    vec3 Hh = normalize(uL + Vd); float g = 0.0, gs = 0.0;
    for (int k = 0; k < 4; k++) gs += shutterW(float(k) / 3.0);
    for (int k = 0; k < 4; k++) { float tt = uTsec + (uTOpen + (uTClose - uTOpen) * float(k) / 3.0) * uSpf;
      g += shutterW(float(k) / 3.0) * 4.0 / gs * pow(abs(dot(tumbleAxis(hh[4], hh[5], hh[6], rate, tt), Hh)), 90.0); }
    rad += uSun * (vis * g * 0.25 * 8.0 * uGlint * (1.0 - uSteady) * (0.4 + 0.6*hh[2]));
  }
  vec3 Ei = uBAmb; vec3 Pm = P * uSu;
  for (int k = 0; k < 24; k++) { if (k >= uNB2) break; vec3 dv = uBP[k] - Pm; float r2 = dot(dv, dv) + 0.12;
    Ei += uBF[k] * (max(-dot(dv * inversesqrt(r2), uBN[k]), 0.0) / (PI * r2)); }
  rad += Ei * uFillK;
  vec3 col = rad * uTint * tcol * (uBright * 0.8 * sigma / pow(max(dist_m, 0.05), 2.0) * weight) * pow(uFocalPx / 1100.0, 2.0);
  vec3 po = proj(1, P + V * (uTOpen * uSpf)), pc = proj(2, P + V * (uTClose * uSpf));
  float streak = length(pc.xy - po.xy);
  float Reff = typ == 3 ? sqrt(R*R + 0.6*rs*rs) : typ == 2 ? R : sqrt(R*R + 0.5*rs*rs);
  float S = clamp(ceil(streak / max(Reff * 0.6, 0.6)), 1.0, 24.0);
  float K = 1.0, bend = 0.0; vec2 fd = vec2(0.0);
  if (typ == 2) {
    float hl = ln * 0.5e-6 / uSu;
    vec3 a0 = proj(0, P - ax * hl), a1 = proj(0, P + ax * hl);
    fd = a1.xy - a0.xy;
    K = clamp(ceil(length(fd) / max(R * 0.7, 0.7)), 1.0, 24.0);
    bend = (hh[2] - 0.5) * 0.6;
  }
  float hold = uHold == 1 ? holdout(pp.xy, dist, Reff) : 1.0;
  o0 = vec4(po.xy, pc.xy);
  o1 = vec4(col, Reff);
  o2 = vec4(hold, S, K, bend);
  o3 = vec4(fd, 0.0, 0.0);
}`;


// ---- embers (motefield.EmberSim / render_embers) ------------------------------------------------------------
const GLSL_EMBER = `
uniform uint uSeedOff; uniform int uNS, uNM, uNG;
uint pcg(uint v){ uint st = v * 747796405u + 2891336453u; uint w = ((st >> ((st >> 28u) + 4u)) ^ st) * 277803737u; return (w >> 22u) ^ w; }
float ernd(uint slot, int cyc, uint k){ return float(pcg(pcg(pcg(slot + uSeedOff) + uint(cyc)) + k) >> 8u) * (1.0 / 16777216.0); }
uniform float uELife;
float elife(uint sl, int cy){ float h = ernd(sl, cy, 6u); return (0.45 + 2.75 * h * h) * max(uELife, 0.05); }
float esize(uint sl, int cy){ return 0.25 + 1.25 * pow(ernd(sl, cy, 7u), 2.5); }
`;
const FS_ESTEP = GLSL_COMMON + GLSL_EMBER + `
uniform sampler2D uEP, uEV; uniform int uN;
uniform float uT, uDt, uRad, uHgt, uRise, uSpread, uTurb, uFlutter, uPops, uPopSize; uniform vec3 uEmit, uWind;
layout(location=0) out vec4 oP; layout(location=1) out vec4 oV;
// the MoteField mockup's ember air, exactly: curl of 3D simplex noise and value-noise gusts (motefield._curl / _vnoise);
// its whole-number maths (mod289, permute, the gradient index) is kept exact in 32 bits so the farm and the viewer agree
vec3 mod289(vec3 x){return x-floor((x+0.5)*(1./289.))*289.;} vec4 mod289(vec4 x){return x-floor((x+0.5)*(1./289.))*289.;}
vec4 permute(vec4 x){return mod289(mod289(x*34.+1.)*x);} vec4 tis(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){ const vec2 C=vec2(1./6.,1./3.); const vec4 D=vec4(0.,.5,1.,2.);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx); vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy; i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
  // the gradient (x, y, h) * 14 in whole numbers, so the sign of h (often exactly 0) matches the farm's
  vec4 j=p-49.*floor((p+0.5)/49.); vec4 x_=floor((j+0.5)/7.); vec4 y_=j-7.*x_;
  vec4 xi=4.*x_-13., yi=4.*y_-13., hi=14.-abs(xi)-abs(yi); vec4 x=xi/14., y=yi/14., h=hi/14.; vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.+1.; vec4 s1=floor(b1)*2.+1.; vec4 sh=-(vec4(1.)-step(vec4(0.5),hi)); vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 nm=tis(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3))); p0*=nm.x; p1*=nm.y; p2*=nm.z; p3*=nm.w;
  vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.); m=m*m;
  return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3))); }
vec3 pot(vec3 p){ return vec3(snoise(p), snoise(p+vec3(31.4,17.1,-9.7)), snoise(p+vec3(-12.3,45.6,7.8))); }
vec3 curl(vec3 p){ const float e=0.1; vec3 dx=vec3(e,0.,0.), dy=vec3(0.,e,0.), dz=vec3(0.,0.,e);
  vec3 a=pot(p+dx), b=pot(p-dx), c=pot(p+dy), d=pot(p-dy), f=pot(p+dz), g=pot(p-dz);
  return vec3((c.z-d.z)-(f.y-g.y), (f.x-g.x)-(a.z-b.z), (a.y-b.y)-(c.x-d.x))/(2.*e); }
float hash13(vec3 p3){ p3=fract(p3*0.1031); p3+=dot(p3,p3.zyx+31.32); return fract((p3.x+p3.y)*p3.z); }
float vnoise(vec3 x){ vec3 i=floor(x), f=fract(x); f=f*f*(3.-2.*f);
  return mix(mix(mix(hash13(i),hash13(i+vec3(1,0,0)),f.x),mix(hash13(i+vec3(0,1,0)),hash13(i+vec3(1,1,0)),f.x),f.y),
             mix(mix(hash13(i+vec3(0,0,1)),hash13(i+vec3(1,0,1)),f.x),mix(hash13(i+vec3(0,1,1)),hash13(i+vec3(1,1,1)),f.x),f.y),f.z); }
void spawn(uint sl, int cy, bool burst, inout vec3 P, inout vec3 V){
  float hh = pow(ernd(sl, cy, 0u), 0.7), a = ernd(sl, cy, 1u) * 2.0 * PI, r = sqrt(ernd(sl, cy, 2u)) * uRad * (1.0 - 0.6 * hh);
  P = uEmit + vec3(cos(a) * r, hh * uHgt, sin(a) * r);
  if (burst) { float ca = ernd(sl, cy, 17u) * 2.0 * PI, el = 0.45 + 0.95 * ernd(sl, cy, 18u), sp = (2.5 + 3.0 * uPopSize) * (0.45 + 0.85 * ernd(sl, cy, 19u));
    V = vec3(cos(ca) * cos(el), sin(el), sin(ca) * cos(el)) * sp; }
  else { float ca = ernd(sl, cy, 3u) * 2.0 * PI, cone = uSpread * 0.6 * sqrt(ernd(sl, cy, 4u)), sp = uRise * (0.5 + 0.85 * ernd(sl, cy, 5u));
    V = vec3(sin(cone) * cos(ca), cos(cone), sin(cone) * sin(ca)) * sp; }
}
// where the ember would be in smooth air (EmberSim._guide): turbulence is read along this path, not at the ember,
// so it can't feed on rounding differences and the viewer keeps the farm's ember paths
vec3 guide(uint sl, int cy, bool burst, float age, float sz){
  vec3 S, V0; spawn(sl, cy, burst, S, V0);
  float tau = burst ? 0.35 + 0.6 * sz : 0.12 + 0.35 * sz;
  vec3 A = uWind * 0.8; A.y += (burst ? 0.0 : 0.45 * uRise) - (burst ? 0.8 + 2.5 * sz : 0.22 * sz);
  return S + A * age + (V0 - A) * (tau * (1.0 - exp(-age / tau)));
}
void main(){
  ivec2 ip = ivec2(gl_FragCoord.xy); int i = ip.y * ${MF_TW} + ip.x;
  vec4 A = texelFetch(uEP, ip, 0), B = texelFetch(uEV, ip, 0);
  if (i >= uN) { oP = A; oV = B; return; }
  bool burst = i >= uNS;
  uint sl = burst ? 1000000u + uint(i - uNS) : uint(i);
  int grp = burst ? (i - uNS) / max(uNM, 1) : 0;
  vec3 P = A.xyz, V = B.xyz; int cyc = int(floor(A.w + 0.5)); float age = B.w, dt = uDt;
  bool alive = age >= 0.0;
  if (alive) {                                   // ride the air (EmberSim._air)
    int cy = burst ? cyc - 1 : cyc;
    float sz = esize(sl, cy);
    vec3 G = guide(sl, cy, burst, age, sz);
    vec3 rel = P - uEmit; float h = max(rel.y, 0.0), r = length(rel.xz), hg = max(G.y - uEmit.y, 0.0);
    float sig = uRad * 0.9 + 0.08 + h * 0.18;
    float gust = 0.7 + 0.6 * vnoise(vec3(uT * 1.3, hg * 0.8 - uT * 1.1, 3.0));
    vec3 air = uWind * min(h * 0.8 + 0.25, 1.0);
    air.y += 0.75 * uRise * exp(-r * r / (2.0 * sig * sig)) * gust / (1.0 + h * 0.25);
    air.xz -= rel.xz * (0.06 * exp(-h * 2.0) * uRise / (r + 0.15));
    if (uTurb > 0.0) {                         // swirling turbulence at two scales, drifting up with the plume
      air += curl(G * 2.2 + vec3(0.0, -uT * 0.9, uT * 0.2)) * uTurb * (0.25 + min(hg, 2.0) * 0.5);
      air += curl(G * 7.0 + vec3(0.0, -uT * 2.0, uT)) * uTurb * 0.16 * min(0.3 + hg, 1.5);
    }
    float fa = uFlutter * (0.08 + 0.34 * ernd(sl, cy, 13u)), fw = 2.0 * PI * (1.5 + 4.5 * ernd(sl, cy, 14u)), fph = ernd(sl, cy, 15u) * 2.0 * PI;
    float sg = ernd(sl, cy, 16u) < 0.5 ? -1.0 : 1.0, tt = age * fw + fph;
    air += vec3(cos(tt), 0.35 * sin(tt * 1.7 + fph), sg * sin(tt)) * fa;
    float tau = burst ? 0.35 + 0.6 * sz : 0.12 + 0.35 * sz;
    air.y -= burst ? 0.8 + 2.5 * sz : 0.22 * sz;
    V += (air - V) * (1.0 - exp(-dt / tau));
    P += V * dt; age += dt;
  } else if (!burst) {                           // steady: born when the age passes 0
    age += dt;
    if (age >= 0.0) spawn(sl, cyc, false, P, V);
  }
  if (alive) {
    float lf = elife(sl, burst ? cyc - 1 : cyc);
    if (!burst && age >= lf) { age -= lf; cyc += 1; spawn(sl, cyc, false, P, V); }   // respawned when it passes its life
    else if (burst && age >= lf) age = -1.0;                                        // a burned-out spark waits for its group
  } else if (burst) {                            // pops: waiting groups fire when their time comes
    float tf = (float(cyc * uNG + grp) + 0.8 * ernd(1000000u + uint(grp), cyc, 20u)) / max(uPops, 1e-6);
    if (uT >= tf) { if (uT - tf <= 0.25) { spawn(sl, cyc, true, P, V); age = 0.0; } cyc += 1; }
  }
  oP = vec4(P, float(cyc)); oV = vec4(V, age);
}`;
const FS_EPREP = GLSL_COMMON + GLSL_EMBER + `
uniform sampler2D uEP, uEV, uDist; uniform int uN; uniform float uSu;
uniform vec3 uC[12]; uniform float uCT[6]; uniform vec2 uWH, uWH0;
uniform float uFocal, uFilm, uFstop, uFocusM, uMaxR, uFocalPx; uniform int uDof;
uniform float uTOpen, uTClose, uSpf, uFC, uFront, uBack, uExpT;
uniform float uTemp, uESize, uEGain, uTwinkle;
uniform int uHold, uFg; uniform float uFgNear, uFgFar; uniform float uEFN, uEFF;
uniform vec4 uShA[8], uShB[8], uShC[8]; uniform int uNSh;      // hide shapes: centre + kind, size + Y turn, falloff + mode + target
// motefield.shape_mask: how much of a point (scene units) the hide shapes leave; tgt 1 = dust, 2 = embers
float shapeMask(vec3 P, float tgt){
  float w = 1.0;
  for (int k = 0; k < 8; k++) {
    if (k >= uNSh) break;
    vec4 A = uShA[k], B = uShB[k], C = uShC[k];
    if (C.z != 3.0 && C.z != tgt) continue;
    float ca = cos(B.w), sa = sin(B.w); vec3 d = P - A.xyz;
    vec3 q = vec3(ca * d.x - sa * d.z, d.y, sa * d.x + ca * d.z), h = B.xyz * 0.5;
    float sd;
    if (A.w > 0.5) { vec3 e = abs(q) - h; sd = length(max(e, 0.0)) + min(max(max(e.x, e.y), e.z), 0.0); }
    else sd = (length(q / h) - 1.0) * min(min(h.x, h.y), h.z);
    float ins = 1.0 - (C.x > 0.0 ? smooth01(sd / C.x + 0.5) : (sd >= 0.0 ? 1.0 : 0.0));
    w *= C.y > 0.5 ? ins : 1.0 - ins;
  }
  return w;
}

uniform float uSteady;
float holdAt(vec2 xy, float dist){
  ivec2 sp = ivec2(clamp(int(xy.x / uWH.x * uWH0.x), 0, int(uWH0.x) - 1), clamp(int(xy.y / uWH.y * uWH0.y), 0, int(uWH0.y) - 1));
  float surf = texelFetch(uDist, sp, 0).r;
  float occl = surf > 0.0 ? smooth01(((dist - surf) / max(surf, 1e-6) + 0.01) / 0.04) : 0.0;
  float fgw = 1.0;
  if (uFg == 1 && uFgFar > uFgNear) fgw = smooth01((surf - uFgNear) / (uFgFar - uFgNear));
  else if (uFg == 1) fgw = surf >= uFgNear ? 1.0 : 0.0;
  return 1.0 - occl * fgw;
}
// steady: averaged over the mote's blur, so it slides behind an edge instead of blinking off
float holdout(vec2 xy, float dist, float Reff){
  if (uSteady <= 0.0) return holdAt(xy, dist);
  float r = uSteady * Reff * 0.7071;
  return (holdAt(xy, dist) + holdAt(xy + vec2(r, r), dist) + holdAt(xy + vec2(-r, r), dist) + holdAt(xy + vec2(r, -r), dist) + holdAt(xy + vec2(-r, -r), dist)) / 5.0;
}
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1; layout(location=2) out vec4 o2; layout(location=3) out vec4 o3;
vec3 proj(int k, vec3 P){
  vec3 d = P - uC[k*4];
  float z = -dot(d, uC[k*4+3]), zs = max(z, 1e-6);
  return vec3((dot(d, uC[k*4+1])/zs/uCT[k*2]*0.5 + 0.5)*uWH.x, (0.5 - dot(d, uC[k*4+2])/zs/uCT[k*2+1]*0.5)*uWH.y, z);
}
vec3 planck(float T){ vec3 lam = vec3(0.61, 0.55, 0.465); return 1.0 / (lam*lam*lam*lam*lam * (exp(min(14388.0 / (lam * T), vec3(80.0))) - 1.0)); }
void cull(){ o0 = vec4(0.0); o1 = vec4(0.0, 0.0, 0.0, -1.0); o2 = vec4(0.0); o3 = vec4(0.0); }
void main(){
  ivec2 ip = ivec2(gl_FragCoord.xy); int i = ip.y * ${MF_TW} + ip.x;
  if (i >= uN) { cull(); return; }
  vec4 A = texelFetch(uEP, ip, 0), B = texelFetch(uEV, ip, 0);
  float age = B.w; if (age < 0.0) { cull(); return; }
  bool burst = i >= uNS; uint sl = burst ? 1000000u + uint(i - uNS) : uint(i);
  int cy = int(floor(A.w + 0.5)) - (burst ? 1 : 0);
  vec3 P = A.xyz / uSu, V = B.xyz / uSu;
  vec3 cpos = uC[0];
  vec3 pp = proj(0, P);
  float margin = uMaxR + 40.0;
  if (pp.z * uSu <= 0.05 || pp.x <= -margin || pp.x >= uWH.x + margin || pp.y <= -margin || pp.y >= uWH.y + margin) { cull(); return; }
  float life = elife(sl, cy);
  float T0 = uTemp * (0.9 + 0.2 * ernd(sl, cy, 8u)), T = 650.0 + (T0 - 650.0) * exp(-age / (life * 0.95));
  float env = smooth01(age / 0.03) * (1.0 - smooth01((age / life - 0.82) / 0.18));
  env *= camFade(length(P - uC[0]), uEFN, uEFF) * shapeMask(P, 2.0);
  float f1 = 3.0 + 11.0 * ernd(sl, cy, 9u), p1 = ernd(sl, cy, 10u), f2 = 0.8 + 3.2 * ernd(sl, cy, 11u), p2 = ernd(sl, cy, 12u);
  float tw = 0.0;
  for (int j = -1; j <= 1; j++) { float a = age + float(j) * uExpT * 0.5; tw += 1.0 + uTwinkle * 0.6 * sin(2.0*PI*(a * f1 + p1)) * sin(2.0*PI*(a * f2 + p2)); }
  tw /= 3.0;
  float dmm = uESize * esize(sl, cy), dist = length(P - cpos), dist_m = dist * uSu, z_m = pp.z * uSu;
  vec3 col = planck(T) * (2e10 * uEGain * tw * env * dmm * dmm / pow(max(dist_m, 0.05), 2.0)) * pow(uFocalPx / 1100.0, 2.0);
  float rs = 0.5 * dmm * 1e-3 * uFocalPx / max(z_m, 1e-3);
  float coc = 0.0;
  if (uDof == 1) {
    vec2 dc = pp.xy - uWH * 0.5; float rn2 = dot(dc, dc) / dot(uWH * 0.5, uWH * 0.5);
    float s1 = uFocusM * 1000.0 * max(1.0 + uFC * rn2, 0.05), s2 = max(z_m, 0.02) * 1000.0;
    coc = (uFocal / uFstop) * uFocal * (s2 - s1) / (s2 * max(s1 - uFocal, 1.0)) / uFilm * uWH.x;
    coc *= coc < 0.0 ? uFront : uBack; }
  float R = clamp(abs(coc) * 0.5, 0.25 + 0.5 * uSteady, uMaxR), Reff = sqrt(R * R + 0.5 * rs * rs);
  vec3 po = proj(1, P + V * (uTOpen * uSpf)), pc = proj(2, P + V * (uTClose * uSpf));
  float S = clamp(ceil(length(pc.xy - po.xy) / max(Reff * 0.6, 0.6)), 1.0, 48.0);
  float hold = uHold == 1 ? holdout(pp.xy, dist, Reff) : 1.0;
  o0 = vec4(po.xy, pc.xy); o1 = vec4(col, Reff); o2 = vec4(hold, S, 1.0, 0.0); o3 = vec4(0.0);
}`;

// ---- splat: instanced quads, one per mote sample ----------------------------------------------------
const vsSplat = (NS) => GLSL_COMMON + `
uniform sampler2D uT0, uT1, uT2, uT3; uniform vec2 uWH; uniform float uMaxR, uCat, uAspect; uniform int uShape;
flat out vec3 vE; flat out float vHold, vR, vSig; flat out vec2 vCat; out vec2 vOff;
float shutterW(float u){ return uShape == 1 ? 1.0 / (1.0 + exp(-9.0 * (u - 0.55))) : 1.0; }
void main(){
  int m = gl_InstanceID / ${NS}, s = gl_InstanceID % ${NS};
  ivec2 tp = texel(m);
  vec4 t1 = texelFetch(uT1, tp, 0);
  gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
  if (t1.w < 0.0) return;
  vec4 t0 = texelFetch(uT0, tp, 0), t2 = texelFetch(uT2, tp, 0), t3 = texelFetch(uT3, tp, 0);
  int S = int(t2.y + 0.5), K = int(t2.z + 0.5);
  int Kq = min(K, ${NS}), Sq = min(S, max(1, ${NS} / Kq));
  if (s >= Sq * Kq) return;
  int si = s / Kq, ki = s % Kq;
  float ts = (float(si) + 0.5) / float(Sq);
  float kf = Kq > 1 ? float(ki) / float(Kq - 1) - 0.5 : 0.0;
  float bend = t2.w; vec2 fd = t3.xy;
  float X = t0.x + (t0.z - t0.x) * ts + fd.x * kf - fd.y * bend * (0.25 - kf*kf);
  float Y = t0.y + (t0.w - t0.y) * ts + fd.y * kf + fd.x * bend * (0.25 - kf*kf);
  float Rr = t1.w, half_;
  float sa = sqrt(uAspect);
  if (Rr < 1.8) { vSig = sqrt(0.25*Rr*Rr + 0.12 + 1.0/6.0); half_ = 3.0*vSig + 1.0; vR = 0.0; }
  else { vR = min(Rr, uMaxR); vSig = 0.0; half_ = vR * max(sa, 1.0 / sa) + 2.0; }
  float ws = 0.0; for (int q = 0; q < ${NS}; q++) { if (q >= Sq) break; ws += shutterW((float(q) + 0.5) / float(Sq)); }
  vE = t1.rgb * (shutterW(ts) / (ws * float(Kq))); vHold = t2.x;
  vCat = (vec2(X, Y) - uWH * 0.5) / length(uWH * 0.5) * uCat;     // cat-eye offset, image axes (y down)
  int c = gl_VertexID;
  vec2 corner = vec2((c == 1 || c == 2 || c == 4) ? 1.0 : -1.0, (c == 2 || c == 4 || c == 5) ? 1.0 : -1.0);
  vOff = corner * half_;
  vec2 pix = vec2(X, Y) + vec2(vOff.x, -vOff.y);            // vOff.y points up the screen
  gl_Position = vec4(pix.x / uWH.x * 2.0 - 1.0, 1.0 - pix.y / uWH.y * 2.0, 0.0, 1.0);
}`;
const VS_SPLAT = vsSplat(MF_NS);
const FS_SPLAT = GLSL_COMMON + `
flat in vec3 vE; flat in float vHold, vR, vSig; flat in vec2 vCat; in vec2 vOff;
uniform int uBlades, uImg; uniform float uApN[32], uRot, uFall, uAspect, uImgRot; uniform sampler2D uShapeTex;
layout(location=0) out vec4 oHeld; layout(location=1) out vec4 oFree;
float apNorm(float R){
  float x = clamp(log(R / 1.8) / log(300.0 / 1.8) * 31.0, 0.0, 31.0);
  int i = int(floor(x)); int j = min(i + 1, 31);
  return mix(uApN[i], uApN[j], x - float(i)) * R * R;
}
void main(){
  float w;
  if (vR <= 0.0) { float r2 = dot(vOff, vOff); w = exp(-r2 / (2.0*vSig*vSig)) / (2.0*PI*vSig*vSig); }
  else {
    vec2 q = vec2(vOff.x, -vOff.y);                       // image axes, y down (like the Python kernel)
    float sa = sqrt(uAspect);
    vec2 qa = vec2(q.x * sa, q.y / sa);
    float rr = length(qa) / vR, m = rr;
    float aa = clamp(1.5 / vR, 0.04, 0.5);
    float k, cl = length(vCat), lens = 0.0;
    if (cl > 0.0) { vec2 qn = q / vR, ch = vCat * 0.5; lens = max(length(qn - ch), length(qn + ch)); }   // cat-eye outline
    if (uImg == 1) {
      float c = cos(-uImgRot), s = sin(-uImgRot);
      vec2 r2 = vec2(qa.x * c - qa.y * s, qa.x * s + qa.y * c) / vR;
      vec2 uv = r2 * 0.5 + 0.5;
      k = (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) ? 0.0 : texture(uShapeTex, uv).r;
      k *= 1.0 + uFall * (0.5 - rr*rr);
      if (cl > 0.0) k *= 1.0 - smooth01((lens - (1.0 - aa)) / (2.0*aa));
    } else {
      if (uBlades >= 3) {
        float a = atan(qa.y, qa.x) + uRot, sg = 2.0*PI/float(uBlades);
        float ang = mod(a, sg) - sg*0.5;
        float poly = rr * cos(ang) / cos(sg*0.5);
        m = 0.65*poly + 0.35*rr;
      }
      k = (1.0 - smooth01((max(m, lens) - (1.0 - aa)) / (2.0*aa))) * (1.0 + uFall * (0.5 - m*m));
    }
    if (cl > 0.0) {
      // the overlap of two unit discs cl apart, as a fraction of one disc
      float d = min(cl, 1.999);
      k /= max((2.0 * acos(d * 0.5) - d * 0.5 * sqrt(4.0 - d*d)) / PI, 0.02);
    }
    w = max(k, 0.0) / apNorm(vR);
  }
  oFree = vec4(vE * w, 0.0);
  oHeld = vec4(vE * w * vHold, 0.0);
}`;

// ---- stand-in plate and distance, ray traced (render_standin_frame) ----------------------------------
const FS_PLATE = GLSL_COMMON + `
uniform sampler2D uBoxes; uniform int uNB;
uniform vec3 uC[4]; uniform float uTh, uTv; uniform vec2 uWH;
uniform vec3 uLs, uSunP, uSky; uniform float uInt;
uniform vec3 uFireP, uFireC;      // the campfire stand-in's fire: a point light (scene units; rgb irradiance at 1 m); 0 = none
layout(location=0) out vec4 oPlate; layout(location=1) out vec4 oDist;
float gSkip;                          // the box hit last never holds out (setSkip)
float trace(vec3 O, vec3 D, out vec3 n, out vec3 alb){
  vec3 Ds = vec3(abs(D.x) < 1e-9 ? 1e-9 : D.x, abs(D.y) < 1e-9 ? 1e-9 : D.y, abs(D.z) < 1e-9 ? 1e-9 : D.z);
  vec3 inv = 1.0 / Ds; float best = 1e30; n = vec3(0.0); alb = vec3(0.0); gSkip = 0.0;
  for (int k = 0; k < uNB; k++) {
    vec3 mn = texelFetch(uBoxes, ivec2(k, 0), 0).xyz, mx = texelFetch(uBoxes, ivec2(k, 1), 0).xyz;
    vec3 t1 = (mn - O) * inv, t2 = (mx - O) * inv;
    vec3 tn = min(t1, t2), tf = max(t1, t2);
    float a = max(max(tn.x, tn.y), tn.z), b = min(min(tf.x, tf.y), tf.z);
    if (b >= max(a, 1e-4) && a > 1e-4 && a < best) {
      best = a;
      int axi = (tn.x >= tn.y && tn.x >= tn.z) ? 0 : (tn.y >= tn.z ? 1 : 2);
      n = vec3(0.0); n[axi] = -sign(Ds[axi]);
      alb = texelFetch(uBoxes, ivec2(k, 2), 0).rgb;
      gSkip = texelFetch(uBoxes, ivec2(k, 0), 0).w;
    }
  }
  return best;
}
bool blocked(vec3 P, vec3 L){
  vec3 Ls = vec3(abs(L.x) < 1e-9 ? 1e-9 : L.x, abs(L.y) < 1e-9 ? 1e-9 : L.y, abs(L.z) < 1e-9 ? 1e-9 : L.z);
  vec3 inv = 1.0 / Ls;
  for (int k = 0; k < uNB; k++) {
    vec3 mn = texelFetch(uBoxes, ivec2(k, 0), 0).xyz, mx = texelFetch(uBoxes, ivec2(k, 1), 0).xyz;
    vec3 t1 = (mn - P) * inv, t2 = (mx - P) * inv;
    vec3 tn = min(t1, t2), tf = max(t1, t2);
    if (min(min(tf.x, tf.y), tf.z) >= max(max(max(tn.x, tn.y), tn.z), 1e-3)) return true;
  }
  return false;
}
void main(){
  vec2 px = gl_FragCoord.xy;          // texture row r holds image row r counted from the top, like uploaded images
  float sx = (px.x / uWH.x * 2.0 - 1.0) * uTh, sy = (1.0 - px.y / uWH.y * 2.0) * uTv;
  vec3 D = normalize(-uC[3] + sx * uC[1] + sy * uC[2]);
  vec3 n, alb;
  float t = trace(uC[0], D, n, alb);
  bool hit = t < 1e29, see = gSkip > 0.5;
  vec3 rgb = uSky;
  if (hit) {
    vec3 P = uC[0] + D * t;
    float nl = max(dot(n, uLs), 0.0);
    float lit = (nl > 0.0 && !blocked(P + n * 0.3, uLs)) ? 1.0 : 0.0;
    float fill = uInt * (0.035 + 0.02 * clamp(n.y, 0.0, 1.0));
    vec3 light = uSunP * (nl * lit) + fill * vec3(0.95, 0.97, 1.0);
    if (dot(uFireC, uFireC) > 0.0) {             // motefield.render_standin_frame: the fire, with shadows
      vec3 Lf = uFireP - P; float d = length(Lf); Lf /= max(d, 1e-6);
      float nf = max(dot(n, Lf), 0.0);
      if (nf > 0.0) { vec3 hn, ha; float th = trace(P + n * 0.3, Lf, hn, ha); float dm = d * 0.01;
        if (th >= d - 1.0) light += uFireC * nf / (dm * dm + 0.09); }
    }
    rgb = alb * light;
  }
  oPlate = vec4(rgb, 1.0);
  oDist = vec4(hit && !see ? t : 0.0, 0.0, 0.0, 1.0);
}`;

const FS_COPY = GLSL_COMMON + `
uniform sampler2D uSrc; out vec4 o;
void main(){ o = texelFetch(uSrc, ivec2(gl_FragCoord.xy), 0); }`;

// ---- viewer: plate + dust, gain, sRGB ------------------------------------------------------------------
const VS_QUAD = `#version 300 es
uniform vec4 uRect;   // x0, y0, x1, y1 in clip space
out vec2 vUV;
void main(){ vec2 c = vec2((gl_VertexID == 1 || gl_VertexID == 2 || gl_VertexID == 4) ? 1.0 : 0.0, (gl_VertexID == 2 || gl_VertexID == 4 || gl_VertexID == 5) ? 1.0 : 0.0);
  vUV = vec2(c.x, 1.0 - c.y); gl_Position = vec4(mix(uRect.xy, uRect.zw, c), 0.0, 1.0); }`;
const FS_COMP = `#version 300 es
precision highp float;
uniform sampler2D uPlate, uHeld, uFree; uniform int uShowPlate, uShowDust, uLayer, uHasPlate; uniform float uGain;
in vec2 vUV; out vec4 o;
vec3 srgb(vec3 x){ x = clamp(x, 0.0, 1.0); return mix(12.92*x, 1.055*pow(x, vec3(1.0/2.4)) - 0.055, step(vec3(0.0031308), x)); }
void main(){
  vec3 c = vec3(0.0);
  if (uShowPlate == 1 && uHasPlate == 1) { ivec2 s = textureSize(uPlate, 0); c += texelFetch(uPlate, ivec2(vUV * vec2(s)), 0).rgb; }
  if (uShowDust == 1) { ivec2 s = textureSize(uHeld, 0); ivec2 p = ivec2(vUV * vec2(s));
    p.y = s.y - 1 - p.y;   // dust buffers are rendered with row 0 at the top of clip space
    c += uLayer == 1 ? texelFetch(uFree, p, 0).rgb : texelFetch(uHeld, p, 0).rgb; }
  o = vec4(srgb(c * uGain), 1.0);
}`;

// ---- 3D view: the motes straight from the simulation textures --------------------------------------------
const VS_MOTES3D = GLSL_COMMON + `
uniform sampler2D uPos, uVis; uniform mat3 uR; uniform vec3 uCenter, uSize; uniform float uSu, uFade, uPt; uniform vec3 uFadeN, uFadeP;
uniform mat4 uM; out vec3 vC;
float edgeW(vec3 local){
  vec3 half_ = uSize * 0.5; float w = 1.0; float base = max(uFade, 0.0) * uSu;
  for (int ax = 0; ax < 3; ax++) {
    float fn = base * max(uFadeN[ax], 0.0), fp = base * max(uFadeP[ax], 0.0);
    if (fn > 1e-9) w *= smooth01((local[ax] + half_[ax]) / fn);
    if (fp > 1e-9) w *= smooth01((half_[ax] - local[ax]) / fp);
  }
  return w;
}
void main(){
  ivec2 tp = texel(gl_VertexID);
  vec3 local = texelFetch(uPos, tp, 0).xyz;
  float vis = texelFetch(uVis, tp, 0).r, w = edgeW(local);
  vec3 P = (uR * local + uCenter) / uSu;
  gl_Position = w > 1e-4 ? uM * vec4(P, 1.0) : vec4(2.0, 2.0, 2.0, 1.0);
  gl_PointSize = uPt;
  float a = clamp(vis * 0.75 + 0.25, 0.0, 1.0) * clamp(w, 0.0, 1.0);
  vC = mix(vec3(0.16, 0.17, 0.2), vec3(1.0, 0.9, 0.72), a) * (0.25 + 0.75 * a);
}`;
const FS_MOTES3D = `#version 300 es
precision mediump float; in vec3 vC; out vec4 o;
void main(){ vec2 d = gl_PointCoord*2.0-1.0; float r = dot(d,d); if (r > 1.0) discard; o = vec4(vC, smoothstep(1.0, 0.0, r)); }`;

// the campfire stand-in's fire light on a frame (motefield.standin_light): position and colour, flickering
// ---- shapes (motefield.parse_shapes / shape_at): spheres and boxes that hide dust / embers or push the dust ----------
const MF_SHAPE_MODES = ['hide inside', 'hide outside', 'push'];
function mfShapes(p, all) {       // all: keep the ones that are off / have no keys too (the editor)
  let L; try { L = typeof p.shapes === 'string' ? JSON.parse(p.shapes || '[]') : (p.shapes || []); } catch (e) { return []; }
  if (!Array.isArray(L)) return [];
  const out = [];
  L.forEach((sh, i) => {
    if (!sh || typeof sh !== 'object') return;
    const keys = (sh.keys || []).filter(k => k && Array.isArray(k.c) && Array.isArray(k.s)).map(k => ({ f: +k.f, c: k.c.slice(0, 3).map(Number), s: k.s.slice(0, 3).map(v => Math.max(+v, 1e-3)), r: +(k.r || 0) }))
      .sort((a, b) => a.f - b.f);
    if (!all && (sh.on === false || !keys.length)) return;
    out.push({ name: String(sh.name || 'Shape ' + (i + 1)), kind: sh.kind === 'box' ? 'box' : 'sphere', mode: MF_SHAPE_MODES.includes(sh.mode) ? sh.mode : 'hide inside',
               target: sh.target === 'dust' || sh.target === 'embers' ? sh.target : 'both', falloff: Math.max(+(sh.falloff || 0), 0), on: sh.on !== false, keys });
  });
  return out;
}
function mfHermite(keys, f, get) {        // motefield._hermite: smooth through the keys, held outside them -> [value, slope per frame]
  const n = keys.length, z = (v) => v.map(() => 0);
  if (n === 1 || f <= keys[0].f) return [get(keys[0]), z(get(keys[0]))];
  if (f >= keys[n - 1].f) return [get(keys[n - 1]), z(get(keys[n - 1]))];
  let i = 0; while (keys[i + 1].f < f) i++;
  const P = keys.map(get), F = keys.map(k => k.f);
  const slope = (j) => { const a = Math.max(j - 1, 0), b = Math.min(j + 1, n - 1), d = Math.max(F[b] - F[a], 1e-6); return P[b].map((v, c) => (v - P[a][c]) / d); };
  const dt = Math.max(F[i + 1] - F[i], 1e-6), t = (f - F[i]) / dt, m0 = slope(i).map(v => v * dt), m1 = slope(i + 1).map(v => v * dt);
  const h00 = 2 * t ** 3 - 3 * t * t + 1, h10 = t ** 3 - 2 * t * t + t, h01 = -2 * t ** 3 + 3 * t * t, h11 = t ** 3 - t * t;
  const d00 = 6 * t * t - 6 * t, d10 = 3 * t * t - 4 * t + 1, d01 = -6 * t * t + 6 * t, d11 = 3 * t * t - 2 * t;
  return [P[i].map((v, c) => h00 * v + h10 * m0[c] + h01 * P[i + 1][c] + h11 * m1[c]),
          P[i].map((v, c) => (d00 * v + d10 * m0[c] + d01 * P[i + 1][c] + d11 * m1[c]) / dt)];
}
function mfShapeAt(sh, f) {               // {c, s, r (degrees), dc (scene units per frame)}
  const [c, dc] = mfHermite(sh.keys, f, k => k.c), [s] = mfHermite(sh.keys, f, k => k.s), [r] = mfHermite(sh.keys, f, k => [k.r]);
  return { c, s: s.map(v => Math.max(v, 1e-3)), r: r[0], dc };
}
function mfShapePushers(p, su, f0, f1) {  // motefield.shape_pushers, for frames f0..f1: {frame: [[cx, cy, cz, r, vx, vy, vz] m, m/s]}
  const L = mfShapes(p).filter(s => s.mode === 'push'); if (!L.length) return null;
  const tab = {};
  for (let f = f0; f <= f1; f++) tab[f] = L.map(sh => { const a = mfShapeAt(sh, f), r = (a.s[0] + a.s[1] + a.s[2]) / 3 * 0.5;
    return [...a.c.map(v => v * su), r * su, ...a.dc.map(v => v * su * p.fps)]; });
  return tab;
}
function mfPushShapesKey(p) { return JSON.stringify(mfShapes(p).filter(s => s.mode === 'push')); }

function fireAt(L, frame) {
  const F = L && L.fire; if (!F) return null;
  const t = typeof frame === 'number' ? (frame - (F.first || 1001)) / 24 : 0;
  const fl = Math.max(0.35, 1 + F.flick * (0.12 * Math.sin(t * 7.3) + 0.08 * Math.sin(t * 13.7 + 1.3) + 0.06 * Math.sin(t * 23.1 + 0.4)));
  return { pos: F.pos, col: F.col.map(v => v * fl), fl, tongues: F.tongues, t };
}
class MFPreview {
  constructor(gl) {
    this.gl = gl;
    this.ok = !!gl.getExtension('EXT_color_buffer_float');
    this.floatBlend = !!gl.getExtension('EXT_float_blend');
    if (!this.ok) return;
    const P = (vs, fs) => this._prog(vs, fs);
    this.pVis = P(VS_FULL, FS_VIS); this.pStep = P(VS_FULL, FS_STEP); this.pPrep = P(VS_FULL, FS_PREP);
    this.pSplat = P(VS_SPLAT, FS_SPLAT); this.pSplatE = P(vsSplat(48), FS_SPLAT); this.pSplatF = null; this.pEStep = P(VS_FULL, FS_ESTEP); this.pEPrep = P(VS_FULL, FS_EPREP); this.pPlate = P(VS_FULL, FS_PLATE); this.pComp = P(VS_QUAD, FS_COMP);
    this.p3d = P(VS_MOTES3D, FS_MOTES3D); this.pCopy = P(VS_FULL, FS_COPY);
    this.vao = gl.createVertexArray();
    this.fb = gl.createFramebuffer();
    this.init = null; this.sim = null; this.boxes = null; this.light = null; this.cams = null;
    this.frameTex = new Map();         // frame -> {dist, plate} for picked utility passes
    this.apCache = {};
    this.dummy = this._tex(1, 1, gl.R32F, gl.RED, gl.FLOAT, new Float32Array([0]));
    this.dummy8 = this._tex(1, 1, gl.R8, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array([0]));
  }
  _prog(vs, fs) {
    const gl = this.gl;
    const sh = (t, src) => { const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('shader: ' + gl.getShaderInfoLog(s)); return s; };
    const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('link: ' + gl.getProgramInfoLog(p));
    p.u = {}; const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) { const a = gl.getActiveUniform(p, i); const nm = a.name.replace(/\[0\]$/, ''); p.u[nm] = gl.getUniformLocation(p, a.name); }
    return p;
  }
  _tex(w, h, ifmt, fmt, type, data) {
    const gl = this.gl, t = gl.createTexture();
    const unit = gl.getParameter(gl.ACTIVE_TEXTURE);
    gl.activeTexture(gl.TEXTURE15);          // a spare unit: making a texture mid-pass mustn't unbind a sampler in use
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, w, h, 0, fmt, type, data || null);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    gl.activeTexture(unit);
    t.w = w; t.h = h;
    return t;
  }
  _rgba32(w, h, data) { const gl = this.gl; return this._tex(w, h, gl.RGBA32F, gl.RGBA, gl.FLOAT, data); }
  _free(...ts) { for (const t of ts) if (t) this.gl.deleteTexture(t); }
  _bind(prog, unit, name, tex) { const gl = this.gl; gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); if (prog.u[name]) gl.uniform1i(prog.u[name], unit); }
  _target(texs, w, h) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fb);
    const bufs = [];
    for (let i = 0; i < 4; i++) {
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + i, gl.TEXTURE_2D, texs[i] || null, 0);
      if (texs[i]) bufs.push(gl.COLOR_ATTACHMENT0 + i);
    }
    gl.drawBuffers(bufs);
    gl.viewport(0, 0, w, h);
  }

  // ---- data from Python ----------------------------------------------------------------------------
  setInit(d) {
    const gl = this.gl, n = d.n, TH = Math.max(1, Math.ceil(n / MF_TW)), N = MF_TW * TH;
    const pack = (src, stride, comps, off) => { const a = new Float32Array(N * 4); for (let i = 0; i < n; i++) for (let c = 0; c < comps; c++) a[i * 4 + c] = src[i * stride + off + c]; return a; };
    const local = new Float32Array(d.local.buffer, d.local.byteOffset, n * 3), h = new Float32Array(d.h.buffer, d.h.byteOffset, n * 10);
    const st = new Float32Array(d.st.buffer, d.st.byteOffset, n * 4);
    if (this.init) this._free(this.init.pos0, this.init.h0, this.init.h1, this.init.h2, this.init.st);
    if (this.sim) this._free(...this.sim.pos, ...this.sim.vel, this.sim.vis, this.sim.visSoft, ...this.sim.prep);
    const h2 = new Float32Array(N * 4);
    for (let i = 0; i < n; i++) { h2[i * 4] = h[i * 10 + 8]; h2[i * 4 + 1] = h[i * 10 + 9]; h2[i * 4 + 2] = st[i * 4]; h2[i * 4 + 3] = st[i * 4 + 1]; }
    this.init = { n, TH, pos0: this._rgba32(MF_TW, TH, pack(local, 3, 3, 0)), h0: this._rgba32(MF_TW, TH, pack(h, 10, 4, 0)),
                  h1: this._rgba32(MF_TW, TH, pack(h, 10, 4, 4)), h2: this._rgba32(MF_TW, TH, h2), st: this._rgba32(MF_TW, TH, pack(st, 4, 4, 0)),
                  modes0: this._modes(d.modes0, d.M0), modes1: this._modes(d.modes1, d.M1), M0: d.M0, M1: d.M1,
                  center: d.center, size: d.size, R: d.R, wind: d.wind, su: d.su };
    const z = () => this._rgba32(MF_TW, TH, null);
    this.sim = { pos: [z(), z()], vel: [z(), z()], cur: 0, vis: this._tex(MF_TW, TH, gl.R32F, gl.RED, gl.FLOAT, null), visSoft: this._tex(MF_TW, TH, gl.R32F, gl.RED, gl.FLOAT, null), prep: [z(), z(), z(), z()], frame: null, key: null };
  }
  _modes(bytes, M) {   // K(3) A(3) ph om  ->  vec4(K, ph), vec4(A, om)
    const a = new Float32Array(bytes.buffer, bytes.byteOffset, M * 8), o = new Float32Array(M * 8);
    for (let m = 0; m < M; m++) { const s = m * 8;
      o.set([a[s], a[s + 1], a[s + 2], a[s + 6], a[s + 3], a[s + 4], a[s + 5], a[s + 7]], m * 8); }
    return o;
  }
  setBoxes(boxes, walker, names, wnames) {   // [[mnx, mny, mnz, mxx, mxy, mxz, r, g, b], ...]; walker: motefield.STANDIN_WALKER + frame range
    // the walker is one more box at the end, moved every frame. It is in the plate and its depth, but (as in motefield.py,
    // whose light map is built from the still room) not in the shadows the dust is lit with: nb counts the still boxes
    const nb = boxes.length, na = nb + (walker ? 2 : 0), a = new Float32Array(na * 3 * 4);
    boxes.forEach((b, k) => { a.set([b[0], b[1], b[2], 0], k * 4); a.set([b[3], b[4], b[5], 0], (na + k) * 4); a.set([b[6], b[7], b[8], 0], (2 * na + k) * 4); });
    if (this.boxes) this._free(this.boxes.tex);
    this.boxes = { nb, na, walker: walker || null, wf: null, tex: this._rgba32(na, 3, a), row0: a.slice(0, na * 4),
                   names: names || [], wnames: wnames || [], skip: new Set(), skipKey: '' };
    if (walker) this.walkerTo(walker.first);
  }
  // objects that never hold out (motefield holdout_skip): flagged in the box texture, so the holdout distance image
  // (standinPlate's) sees through them; the plate, the lit set and the shadows are unchanged
  setSkip(names) {
    const B = this.boxes; if (!B) return;
    const key = [...new Set(names)].sort().join(',');
    if (key === B.skipKey) return;
    B.skipKey = key; B.skip = new Set(names);
    const r = B.row0.slice();
    for (let k = 0; k < B.nb; k++) r[k * 4 + 3] = B.skip.has(B.names[k]) ? 1 : 0;
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE15); gl.bindTexture(gl.TEXTURE_2D, B.tex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, B.nb, 1, gl.RGBA, gl.FLOAT, r.subarray(0, B.nb * 4));
    const wf = B.wf; B.wf = null; if (B.walker && wf !== null) this.walkerTo(wf);
    if (this.pl) this.pl.frame = null;
  }
  walkerBoxes(frame) {      // motefield.standin_walker_parts: the body and its swinging arm
    const W = this.boxes && this.boxes.walker; if (!W || typeof frame !== 'number') return null;
    const u = (frame - W.first) / Math.max(W.last - W.first, 1), c = W.start.map((v, i) => v + (W.end[i] - v) * u), sz = W.size;
    const body = { mn: [c[0] - sz[0] / 2, c[1], c[2] - sz[2] / 2], mx: [c[0] + sz[0] / 2, c[1] + sz[1], c[2] + sz[2] / 2], color: W.color };
    const A = W.arm; if (!A) return [body];
    const cx = c[0] + A.swing * Math.sin(2 * Math.PI * (frame - W.first) / A.period), z0 = body.mx[2] + 1;
    return [body, { mn: [cx - A.size[0] / 2, A.top - A.size[1], z0], mx: [cx + A.size[0] / 2, A.top, z0 + A.size[2]], color: W.color }];
  }
  walkerTo(frame) {
    const B = this.boxes; if (!B || !B.walker || B.wf === frame) return;
    const ws = this.walkerBoxes(frame); if (!ws) return;
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE15); gl.bindTexture(gl.TEXTURE_2D, B.tex);     // a spare unit, so no other binding changes
    ws.forEach((w, i) => { const k = B.nb + i;
      gl.texSubImage2D(gl.TEXTURE_2D, 0, k, 0, 1, 1, gl.RGBA, gl.FLOAT, new Float32Array([...w.mn, B.skip.has(B.wnames[i]) ? 1 : 0]));
      gl.texSubImage2D(gl.TEXTURE_2D, 0, k, 1, 1, 1, gl.RGBA, gl.FLOAT, new Float32Array([...w.mx, 0]));
      gl.texSubImage2D(gl.TEXTURE_2D, 0, k, 2, 1, 1, gl.RGBA, gl.FLOAT, new Float32Array([...w.color, 0])); });
    B.wf = frame;
  }
  setLightMap(d) {
    const gl = this.gl;
    if (this.light) this._free(this.light.g, ...this.light.sky);
    if (!d) { this.light = null; return; }
    const g = new Float32Array(d.g.buffer, d.g.byteOffset, d.res * d.res);
    const views = d.views.slice(0, 3);
    this.light = { ...d, g: this._tex(d.res, d.res, gl.R32F, gl.RED, gl.FLOAT, g),
      sky: views.map(v => this._tex(v.w, v.h, gl.R8, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array(v.sky.buffer, v.sky.byteOffset, v.w * v.h))),
      views: views.map(v => ({ cam: v.cam, w: v.w, h: v.h })) };
  }
  setCams(sc) { this.cams = sc; }
  setBounce(d) {           // motefield.bounce_lights
    if (!d) { this.bounce = null; return; }
    const f = (b, n) => { const a = new Float32Array(72); a.set(new Float32Array(b.buffer, b.byteOffset, n * 3).subarray(0, 72)); return a; };
    this.bounce = { n: d.n, pos: f(d.pos, d.n), nrm: f(d.nrm, d.n), flux: f(d.flux, d.n), amb: d.amb };
  }
  setFrameImages(f, d) {
    const gl = this.gl, old = this.frameTex.get(f);
    if (old) this._free(old.dist, old.plate, old.dof, old.hdist);
    const e = { dist: this._tex(d.w, d.h, gl.R32F, gl.RED, gl.FLOAT, new Float32Array(d.dist.buffer, d.dist.byteOffset, d.w * d.h)) };
    if (d.hdist) e.hdist = this._tex(d.w, d.h, gl.R32F, gl.RED, gl.FLOAT, new Float32Array(d.hdist.buffer, d.hdist.byteOffset, d.w * d.h));   // objects that never hold out: see-through
    if (d.rgb) {
      const u16 = new Uint16Array(d.rgb.buffer, d.rgb.byteOffset, d.pw * d.ph * 3);
      e.plate = this._tex(d.pw, d.ph, gl.RGB16F, gl.RGB, gl.HALF_FLOAT, u16);
      e.synthetic = !!d.synthetic;        // shaded from the utility pass: no lens blur of its own yet
    }
    this.frameTex.set(f, e);
  }
  clearFrameImages() { for (const e of this.frameTex.values()) this._free(e.dist, e.plate, e.dof, e.hdist); this.frameTex.clear(); }

  // ---- cameras (Shot.camera with fractional frames) --------------------------------------------------
  camAt(f) {
    if (this.camOverride) return this.camOverride;        // the viewer's free camera
    const sc = this.cams, n = sc.last - sc.first + 1;
    const get = (g) => { const i = (g - sc.first) * 13, d = sc.cams; return { pos: [d[i], d[i + 1], d[i + 2]], right: [d[i + 3], d[i + 4], d[i + 5]], up: [d[i + 6], d[i + 7], d[i + 8]], back: [d[i + 9], d[i + 10], d[i + 11]], hfov: d[i + 12] }; };
    const has = (g) => g >= sc.first && g <= sc.last;
    const blend = (a, b, t) => {
      const L = (x, y) => x.map((v, k) => v * (1 - t) + y[k] * t);
      let bk = L(a.back, b.back); const bl = Math.hypot(...bk) + 1e-12; bk = bk.map(v => v / bl);
      let r = L(a.right, b.right); const rb = r[0] * bk[0] + r[1] * bk[1] + r[2] * bk[2]; r = r.map((v, k) => v - bk[k] * rb);
      const rl = Math.hypot(...r) + 1e-12; r = r.map(v => v / rl);
      const u = [bk[1] * r[2] - bk[2] * r[1], bk[2] * r[0] - bk[0] * r[2], bk[0] * r[1] - bk[1] * r[0]];
      return { pos: L(a.pos, b.pos), right: r, up: u, back: bk, hfov: a.hfov * (1 - t) + b.hfov * t };
    };
    const f0 = Math.floor(f);
    if (Math.abs(f - f0) < 1e-6 && has(f0)) return get(f0);
    if (has(f0) && has(f0 + 1)) return blend(get(f0), get(f0 + 1), f - f0);
    if (has(f0 + 1) && has(f0 + 2)) return blend(get(f0 + 1), get(f0 + 2), f - (f0 + 1));
    if (has(f0) && has(f0 - 1)) return blend(get(f0 - 1), get(f0), f - (f0 - 1));
    for (const g of [f0, f0 + 1, f0 - 1]) if (has(g)) return get(g);
    return get(Math.max(sc.first, Math.min(sc.last, Math.round(f))));
  }

  // ---- the simulation ---------------------------------------------------------------------------------
  _passCommon(prog) {
    const gl = this.gl, I = this.init;
    gl.uniformMatrix3fv(prog.u.uR, false, new Float32Array([I.R[0], I.R[3], I.R[6], I.R[1], I.R[4], I.R[7], I.R[2], I.R[5], I.R[8]]));
    gl.uniform3fv(prog.u.uCenter, I.center); if (prog.u.uSize) gl.uniform3fv(prog.u.uSize, I.size);
    if (prog.u.uSu) gl.uniform1f(prog.u.uSu, I.su);
  }
  _visPass(p, soft) {
    const gl = this.gl, I = this.init, S = this.sim, prog = this.pVis;
    gl.useProgram(prog); gl.bindVertexArray(this.vao);
    this._target([soft ? S.visSoft : S.vis], MF_TW, I.TH);
    const r = soft ? ((p.shadow_soft || 0) + 12 * (p.steady || 0)) * 0.01 / I.su : 0;      // steady softens shadow edges too
    if (r > 0) {          // motefield.soft_taps
      const L = p.L, l = Math.hypot(...L), Ln = L.map(v => v / l), up = Math.abs(Ln[1]) < 0.95 ? [0, 1, 0] : [1, 0, 0];
      let a = [Ln[1] * up[2] - Ln[2] * up[1], Ln[2] * up[0] - Ln[0] * up[2], Ln[0] * up[1] - Ln[1] * up[0]]; const al = Math.hypot(...a); a = a.map(v => v / al);
      const b = [Ln[1] * a[2] - Ln[2] * a[1], Ln[2] * a[0] - Ln[0] * a[2], Ln[0] * a[1] - Ln[1] * a[0]];
      const taps = [0, 0, 0];
      for (let k = 0; k < 5; k++) { const t = 2 * Math.PI * k / 5 + 0.3; for (let i = 0; i < 3; i++) taps.push((a[i] * Math.cos(t) + b[i] * Math.sin(t)) * r); }
      gl.uniform3fv(prog.u.uTaps, taps); gl.uniform1i(prog.u.uNT, 6);
    } else { gl.uniform3fv(prog.u.uTaps, new Float32Array(18)); gl.uniform1i(prog.u.uNT, 1); }
    this._passCommon(prog);
    this._bind(prog, 0, 'uPos', S.pos[S.cur]);
    gl.uniform3fv(prog.u.uL, p.L);
    gl.uniform1i(prog.u.uMode, p.lightMode);
    this._bind(prog, 1, 'uBoxes', this.boxes ? this.boxes.tex : this.dummy);
    gl.uniform1i(prog.u.uNB, this.boxes ? this.boxes.nb : 0);
    const Lm = this.light;
    this._bind(prog, 2, 'uLM', Lm ? Lm.g : this.dummy);
    for (let k = 0; k < 3; k++) this._bind(prog, 3 + k, 'uSky' + k, Lm && Lm.sky[k] ? Lm.sky[k] : this.dummy8);
    if (Lm && p.lightMode === 2) {
      gl.uniform3fv(prog.u.uLa, Lm.a); gl.uniform3fv(prog.u.uLb, Lm.b); gl.uniform2fv(prog.u.uLlo, Lm.lo);
      gl.uniform1f(prog.u.uLcell, Lm.cell); gl.uniform1f(prog.u.uLbias, Lm.bias); gl.uniform1i(prog.u.uLres, Lm.res);
      gl.uniform1i(prog.u.uInterior, p.interior ? 1 : 0); gl.uniform3fv(prog.u.uBlo, Lm.blo); gl.uniform3fv(prog.u.uBhi, Lm.bhi);
      gl.uniform1i(prog.u.uNV, Lm.views.length);
      const VC = new Float32Array(36), VT = new Float32Array(6), VS = new Float32Array(6);
      Lm.views.forEach((v, k) => { const c = v.cam; VC.set(c.slice(0, 12), k * 12); const th = Math.tan(c[12] / 2); VT[k * 2] = th; VT[k * 2 + 1] = th * v.h / v.w; VS[k * 2] = v.w; VS[k * 2 + 1] = v.h; });
      gl.uniform3fv(prog.u.uVC, VC); gl.uniform1fv(prog.u.uVT, VT); gl.uniform2fv(prog.u.uVS, VS);
    }
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  _stepPass(p, t, dt) {
    const gl = this.gl, I = this.init, S = this.sim, prog = this.pStep, nx = 1 - S.cur;
    // made before anything is bound: creating a texture binds it to the active unit, which would knock out a sampler below
    if (this._push && this._push.n && !this.pushTex) this.pushTex = this._rgba32(144, 2, null);
    gl.useProgram(prog); gl.bindVertexArray(this.vao);
    this._target([S.pos[nx], S.vel[nx]], MF_TW, I.TH);
    this._passCommon(prog);
    this._bind(prog, 0, 'uPos', S.pos[S.cur]); this._bind(prog, 1, 'uVel', S.vel[S.cur]);
    this._bind(prog, 2, 'uVis', S.vis); this._bind(prog, 3, 'uSt', I.st);
    gl.uniform3fv(prog.u.uWind, I.wind);
    gl.uniform1f(prog.u.uT, t); gl.uniform1f(prog.u.uDt, dt);
    gl.uniform1f(prog.u.uAmp0, p.eddies * 0.01); gl.uniform1f(prog.u.uAmp1, p.micro * 0.01);
    gl.uniform1f(prog.u.uThermal, p.thermal); gl.uniform1f(prog.u.uSettle, p.settle);
    gl.uniform4fv(prog.u.uM0, I.modes0); gl.uniform1i(prog.u.uN0, I.M0);
    gl.uniform4fv(prog.u.uM1, I.modes1); gl.uniform1i(prog.u.uN1, I.M1);
    const P = this._push;
    gl.uniform1i(prog.u.uNP, P ? P.n : 0);
    if (P && P.n) {
      gl.activeTexture(gl.TEXTURE4); gl.bindTexture(gl.TEXTURE_2D, this.pushTex);      // its own unit: don't disturb the others
      if (this.pushTexFor !== P) { gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 144, 2, gl.RGBA, gl.FLOAT, P.data); this.pushTexFor = P; }
      this._bind(prog, 4, 'uPush', this.pushTex);
      gl.uniform1f(prog.u.uPAmt, p.push_amount); gl.uniform1f(prog.u.uPRs, Math.max(p.push_radius, 0.05));
    } else this._bind(prog, 4, 'uPush', this.dummy);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    S.cur = nx;
  }
  _restart() {
    const gl = this.gl, I = this.init, S = this.sim;
    // copy the starting positions into the current state, zero the velocities
    gl.useProgram(this.pCopy); gl.bindVertexArray(this.vao);
    this._target([S.pos[0]], MF_TW, I.TH);
    this._bind(this.pCopy, 0, 'uSrc', I.pos0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this._target([S.vel[0]], MF_TW, I.TH);
    gl.clearBufferfv(gl.COLOR, 0, [0, 0, 0, 0]);
    S.cur = 0;
  }
  // sim_at(): state at `frame`, simulated from first - preroll
  // ---- embers: motefield.EmberSim on the GPU, from the same starting state (mfweb.ember_init) ----------------
  setEmbers(d) {
    const gl = this.gl;
    if (this.emb) this._free(...this.emb.pos, ...this.emb.vel, ...this.emb.prep);
    this.emb = null;
    if (!d || !d.n) return;
    const TH = Math.max(1, Math.ceil(d.n / MF_TW)), N = MF_TW * TH;
    const grow = (b) => { const a = new Float32Array(N * 4); a.set(new Float32Array(b.buffer, b.byteOffset, d.n * 4)); return a; };
    const z = () => this._rgba32(MF_TW, TH, null);
    this.emb = { d, TH, init: [grow(d.pos), grow(d.vel)], pos: [z(), z()], vel: [z(), z()], prep: [z(), z(), z(), z()], cur: 0, frame: null, t: 0, key: null };
  }
  _emberTo(frame, p) {
    const E = this.emb; if (!E || !p.embers) return;
    const gl = this.gl, d = E.d, key = JSON.stringify(['ember_life', 'ember_rise', 'ember_spread', 'ember_turb', 'ember_flutter', 'ember_pops', 'ember_pop_size', 'speed', 'fps'].map(k => p[k]));
    if (E.key !== key || E.frame === null || frame < E.frame) {
      for (const [tex, data] of [[E.pos[0], E.init[0]], [E.vel[0], E.init[1]]]) { gl.activeTexture(gl.TEXTURE15); gl.bindTexture(gl.TEXTURE_2D, tex); gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, MF_TW, E.TH, gl.RGBA, gl.FLOAT, data); }
      E.cur = 0; E.frame = d.f0; E.t = d.t0; E.key = key;
    }
    const sub = d.sub, dt = p.speed / p.fps / sub, prog = this.pEStep;
    gl.disable(gl.BLEND); gl.useProgram(prog); gl.bindVertexArray(this.vao);
    const u = prog.u;
    gl.uniform1ui(u.uSeedOff, d.seedoff); gl.uniform1i(u.uNS, d.ns); gl.uniform1i(u.uNM, d.nm); gl.uniform1i(u.uNG, d.ng); gl.uniform1i(u.uN, d.n);
    gl.uniform1f(u.uELife, p.ember_life); gl.uniform1f(u.uDt, dt); gl.uniform1f(u.uRad, d.rad); gl.uniform1f(u.uHgt, d.hgt);
    gl.uniform1f(u.uRise, p.ember_rise); gl.uniform1f(u.uSpread, p.ember_spread); gl.uniform1f(u.uTurb, p.ember_turb); gl.uniform1f(u.uFlutter, p.ember_flutter);
    gl.uniform1f(u.uPops, p.ember_pops); gl.uniform1f(u.uPopSize, p.ember_pop_size); gl.uniform3fv(u.uEmit, d.emit); gl.uniform3fv(u.uWind, d.wind);
    while (E.frame < frame) {
      for (let k = 0; k < sub; k++) {
        const nx = 1 - E.cur;
        this._target([E.pos[nx], E.vel[nx]], MF_TW, E.TH);
        this._bind(prog, 0, 'uEP', E.pos[E.cur]); this._bind(prog, 1, 'uEV', E.vel[E.cur]);
        gl.uniform1f(u.uT, E.t);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        E.cur = nx; E.t += dt;
      }
      E.frame++;
    }
  }
  // character pushers: {frame: [[cx, cy, cz, r, vx, vy, vz], ...]} (metres, m/s), the chosen movers from motefield.find_movers
  setPushers(d) { this.pushers = d || null; }
  _pushersFor(F, p) {          // motefield.pushers_for_step: this frame's, plus a fading wake from the 5 before; at most 144
    const data = new Float32Array(144 * 2 * 4); let n = 0;
    for (let k = 0; k <= 5; k++) {
      const L = this.pushers[F - k]; if (!L) continue;
      const w = Math.pow(0.6, k);
      for (const q of L) { if (n >= 144) break; data.set([q[0], q[1], q[2], q[3] * (1 + 0.25 * k)], n * 4); data.set([q[4], q[5], q[6], w], (144 + n) * 4); n++; }
    }
    return { n, data };
  }
  simTo(frame, p, simKey) {
    if (!this.init) return false;
    const S = this.sim, fps = p.fps, sub = Math.max(1, Math.round(p.substeps)), f0 = Math.round(p.first) - Math.round(p.preroll);
    const gl = this.gl;
    gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST); gl.disable(gl.SCISSOR_TEST);
    if (S.key !== simKey || S.frame === null || frame < S.frame) {
      this._restart(); S.key = simKey; S.frame = f0; S.t = (f0 - p.first) / fps * p.speed; S.steps = 0;
    }
    const dt = p.speed / fps / sub;
    let n = 0;
    while (S.frame < frame) {
      this._push = this.pushers ? this._pushersFor(S.frame + 1, p) : null;     // motefield.pushers_for_step (movers and / or push shapes)
      this._visPass(p);
      for (let k = 0; k < sub; k++) { this._stepPass(p, S.t, dt); S.t += dt; }
      S.frame++; n++;
    }
    this._visPass(p);
    this._visPass(p, true);           // what the render uses: soft edges
    this._emberTo(frame, p);
    return n;
  }

  // ---- one frame of dust -----------------------------------------------------------------------------------
  setShapeImage(img) {        // {w, h, data: Float32Array (0..1, row 0 at the top)} or null
    const gl = this.gl;
    if (this.shapeTex) this._free(this.shapeTex);
    this.shapeTex = null; this.shapeMean = 0;
    if (!img) return;
    const n = Math.max(img.w, img.h), sq = new Float32Array(n * n);
    let mx = 0; for (const v of img.data) mx = Math.max(mx, v);
    const ox = (n - img.w) >> 1, oy = (n - img.h) >> 1;
    for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) sq[(y + oy) * n + x + ox] = img.data[y * img.w + x] / Math.max(mx, 1e-9);
    this.shapeTex = this._tex(n, n, gl.R16F, gl.RED, gl.FLOAT, sq);        // R16F filters linearly everywhere
    gl.activeTexture(gl.TEXTURE15); gl.bindTexture(gl.TEXTURE_2D, this.shapeTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    this.shapeImg = { n, sq }; this.apCache = {};
  }
  _apNormImage(fall) {
    const key = 'img' + fall; if (this.apCache[key]) return this.apCache[key];
    // sum of the shape over the bokeh's square, in units of R^2 (the image spans 2R x 2R)
    const { n, sq } = this.shapeImg; let s = 0;
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) { const u = (x + 0.5) / n * 2 - 1, v = (y + 0.5) / n * 2 - 1; s += sq[y * n + x] * (1 + fall * (0.5 - u * u - v * v)); }
    const c = Math.max(s / (n * n) * 4, 1e-6);
    return (this.apCache[key] = new Float32Array(32).fill(c));
  }
  _apNorm(blades, fall = 0, rot = 0.25) {
    const ck = blades + '|' + fall + '|' + rot;
    if (this.apCache[ck]) return this.apCache[ck];
    const out = new Float32Array(32), sm = (x) => { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); };
    for (let i = 0; i < 32; i++) {
      const R = 1.8 * Math.pow(300 / 1.8, i / 31), r = Math.ceil(R + 1.5), aa = Math.min(Math.max(1.5 / R, 0.04), 0.5);
      let s = 0;
      for (let y = -r; y <= r; y++) for (let x = -r; x <= r; x++) {
        const rr = Math.hypot(x, y) / R; let m = rr;
        if (blades >= 3) { const a = Math.atan2(y, x) + rot, sg = 2 * Math.PI / blades; const ang = ((a % sg) + sg) % sg - sg / 2; m = 0.65 * (rr * Math.cos(ang) / Math.cos(sg / 2)) + 0.35 * rr; }
        s += Math.max(0, (1 - sm((m - (1 - aa)) / (2 * aa))) * (1 + fall * (0.5 - m * m)));
      }
      out[i] = s / (R * R);
    }
    return (this.apCache[ck] = out);
  }
  _ensureTargets(W, H) {
    const gl = this.gl;
    if (this.acc && this.acc.w === W && this.acc.h === H) return;
    if (this.acc) this._free(this.acc.held, this.acc.free, this.acc.plate, this.acc.dist);
    const fmt = this.floatBlend ? gl.RGBA32F : gl.RGBA16F, type = this.floatBlend ? gl.FLOAT : gl.HALF_FLOAT;
    this.acc = { w: W, h: H, held: this._tex(W, H, fmt, gl.RGBA, type, null), free: this._tex(W, H, fmt, gl.RGBA, type, null) };
  }
  _ensurePlate(W0, H0) {
    const gl = this.gl;
    if (this.pl && this.pl.w === W0 && this.pl.h === H0) return;
    if (this.pl) this._free(this.pl.plate, this.pl.dist);
    this.pl = { w: W0, h: H0, plate: this._tex(W0, H0, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT, null), dist: this._tex(W0, H0, gl.R32F, gl.RED, gl.FLOAT, null), frame: null };
  }
  // the stand-in's plate and distance image for this frame (GPU version of render_standin_frame)
  standinPlate(frame, W0, H0, plateLight, walkerFrame) {      // walkerFrame: where the walker is (the current frame when the camera is held)
    this._ensurePlate(W0, H0);
    const wf = walkerFrame === undefined ? frame : walkerFrame;
    const c = this.camAt(frame), key = W0 + 'x' + H0 + '|' + wf + '|' + c.pos.join(',') + c.back.join(',') + c.hfov;
    if (this.pl.frame === frame && this.pl.key === key) return;
    this.walkerTo(wf);
    const gl = this.gl, prog = this.pPlate;
    gl.useProgram(prog); gl.bindVertexArray(this.vao);
    gl.disable(gl.BLEND);
    this._target([this.pl.plate, this.pl.dist], W0, H0);
    this._bind(prog, 0, 'uBoxes', this.boxes.tex); gl.uniform1i(prog.u.uNB, this.boxes.na);
    gl.uniform3fv(prog.u.uC, [...c.pos, ...c.right, ...c.up, ...c.back]);
    const th = Math.tan(c.hfov / 2);
    gl.uniform1f(prog.u.uTh, th); gl.uniform1f(prog.u.uTv, th * H0 / W0); gl.uniform2f(prog.u.uWH, W0, H0);
    const az = plateLight.az * Math.PI / 180, el = plateLight.el * Math.PI / 180, I = plateLight.intensity;
    gl.uniform3fv(prog.u.uLs, [-Math.cos(az) * Math.cos(el), Math.sin(el), -Math.sin(az) * Math.cos(el)]);
    gl.uniform3fv(prog.u.uSunP, plateLight.kelvin.map(v => v * I));
    gl.uniform3fv(prog.u.uSky, [0.66 * I * 0.45, 0.76 * I * 0.45, 0.95 * I * 0.45]);
    gl.uniform1f(prog.u.uInt, I);
    const F = fireAt(plateLight, wf); gl.uniform3fv(prog.u.uFireP, F ? F.pos : [0, 0, 0]); gl.uniform3fv(prog.u.uFireC, F ? F.col : [0, 0, 0]);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this.pl.frame = frame; this.pl.key = key;
  }
  _shapeUniforms(prog, p, frame) {     // the hide shapes on this frame (motefield.shape_mask), at most 8
    const gl = this.gl, A = new Float32Array(32), B = new Float32Array(32), C = new Float32Array(32);
    const L = mfShapes(p).filter(s => s.mode !== 'push').slice(0, 8);
    L.forEach((sh, k) => { const a = mfShapeAt(sh, frame);
      A.set([...a.c, sh.kind === 'box' ? 1 : 0], k * 4); B.set([...a.s, a.r * Math.PI / 180], k * 4);
      C.set([sh.falloff, sh.mode === 'hide outside' ? 1 : 0, sh.target === 'dust' ? 1 : sh.target === 'embers' ? 2 : 3, 0], k * 4); });
    gl.uniform4fv(prog.u.uShA, A); gl.uniform4fv(prog.u.uShB, B); gl.uniform4fv(prog.u.uShC, C); gl.uniform1i(prog.u.uNSh, L.length);
  }
  // render_frame(): the dust for `frame` into the accumulation buffers. distTex: the shot's distance image
  lens(p, cam, W) {        // motefield.render_frame + lens_settings
    const I = this.init, lens = this.cams.lens || {};
    const focal = p.focal || lens.focal || 35, film = p.film || lens.film || 2 * focal * Math.tan(cam.hfov / 2);
    const fstop = p.fstop || lens.fstop || 4, focusM = (p.focus || lens.focus || 500) * I.su;
    const ex = lens.extra || {};
    const blades = Math.round(p.blades) > 0 ? Math.round(p.blades) : Math.round(ex.blades || 0);
    const full = p.fstop_full || ex.fstop_full || fstop, vign = Math.min(1, full / Math.max(fstop, 1e-3));
    return { focal, film, fstop, focusM, su: I.su, blades, catAmt: Math.max((ex.cateye || 0) + p.cateye, 0) * vign,
             aspect: Math.max((ex.anamorphic || 1) + p.anamorphic, 0.05), fc: (ex.fieldcurv || 0) + p.field_curv,
             front: p.front_mult, back: p.back_mult, dof: !!p.dof, maxR: p.max_bokeh };
  }
  // final: every sample along each streak, as the farm render does (up to 24 x the fibre spread; the viewer draws 16)
  renderDust(frame, p, W0, H0, distTex, hold, final) {
    const gl = this.gl, I = this.init, S = this.sim;
    const sc = Math.max(Math.min(p.proxy, 1), 0.1), W = Math.max(Math.round(W0 * sc), 8), H = Math.max(Math.round(H0 * sc), 8);
    this._ensureTargets(W, H);
    gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST); gl.disable(gl.SCISSOR_TEST);
    this._target([this.acc.held, this.acc.free], W, H);
    gl.clearBufferfv(gl.COLOR, 0, [0, 0, 0, 0]); gl.clearBufferfv(gl.COLOR, 1, [0, 0, 0, 0]);
    // (no motes: the passes below draw nothing, but the embers still do)
    const cam = this.camAt(hold ? hold : frame);       // hold: the camera stays on that frame while the dust moves
    const { focal, film, fstop, focusM, blades, catAmt, aspect, fc } = this.lens(p, cam, W);
    const shape = String(p.shutter_shape).toLowerCase().startsWith('back') ? 1 : 0;
    const focalPx = W / (2 * Math.tan(cam.hfov / 2));
    const s = p.mb ? p.shutter / 360 : 0, mode = String(p.shutter_mode).toLowerCase();
    const [tO, tC] = mode.startsWith('back') ? [-s, 0] : mode.startsWith('for') ? [0, s] : [-s / 2, s / 2];
    const cams = hold ? [cam, cam, cam] : [cam, this.camAt(frame + tO), this.camAt(frame + tC)];
    const C = [], CT = [];
    for (const c of cams) { C.push(...c.pos, ...c.right, ...c.up, ...c.back); const th = Math.tan(c.hfov / 2); CT.push(th, th * H / W); }
    // per-mote maths
    let prog = this.pPrep;
    gl.useProgram(prog); gl.bindVertexArray(this.vao);
    this._target(S.prep, MF_TW, I.TH);
    this._passCommon(prog);
    this._bind(prog, 0, 'uPos', S.pos[S.cur]); this._bind(prog, 1, 'uVel', S.vel[S.cur]); this._bind(prog, 2, 'uVis', S.visSoft);
    this._bind(prog, 3, 'uH0', I.h0); this._bind(prog, 4, 'uH1', I.h1); this._bind(prog, 5, 'uH2', I.h2); this._bind(prog, 6, 'uDist', distTex || this.dummy);
    const u = prog.u;
    gl.uniform1i(u.uN, I.n);
    gl.uniform1f(u.uFade, p.fade); gl.uniform3fv(u.uFadeN, [p.fade_xn, p.fade_yn, p.fade_zn]); gl.uniform3fv(u.uFadeP, [p.fade_xp, p.fade_yp, p.fade_zp]);
    gl.uniform3fv(u.uC, C); gl.uniform1fv(u.uCT, CT);
    gl.uniform2f(u.uWH, W, H); gl.uniform2f(u.uWH0, distTex ? distTex.w : 1, distTex ? distTex.h : 1);
    gl.uniform1f(u.uFocal, focal); gl.uniform1f(u.uFilm, film); gl.uniform1f(u.uFstop, fstop); gl.uniform1f(u.uFocusM, focusM);
    gl.uniform1f(u.uMaxR, p.max_bokeh * sc); gl.uniform1f(u.uFocalPx, focalPx); gl.uniform1i(u.uDof, p.dof ? 1 : 0);
    gl.uniform1f(u.uTOpen, tO); gl.uniform1f(u.uTClose, tC); gl.uniform1f(u.uSpf, p.speed / p.fps); gl.uniform1f(u.uShut, s);
    gl.uniform1i(u.uShape, shape); gl.uniform1f(u.uFC, fc); gl.uniform1f(u.uFront, p.front_mult); gl.uniform1f(u.uBack, p.back_mult);
    gl.uniform1f(u.uTsec, (frame - p.first) / p.fps * p.speed);
    gl.uniform3fv(u.uL, p.L); gl.uniform3fv(u.uSun, p.sun); gl.uniform3fv(u.uTint, p.tint);
    const B = this.bounce;
    gl.uniform1f(u.uFillK, p.bounce * p.shadow_fill * 1.5);
    if (B && B.n) { gl.uniform3fv(u.uBP, B.pos); gl.uniform3fv(u.uBN, B.nrm); gl.uniform3fv(u.uBF, B.flux); gl.uniform1i(u.uNB2, Math.min(B.n, 24)); gl.uniform3fv(u.uBAmb, B.amb); }
    else { gl.uniform1i(u.uNB2, 0); gl.uniform3fv(u.uBAmb, p.sun.map(v => v * 0.004)); }
    gl.uniform1f(u.uBright, p.brightness); gl.uniform1f(u.uForward, p.forward); gl.uniform1f(u.uGlint, p.glint); gl.uniform1f(u.uTumble, p.tumble);
    gl.uniform1f(u.uSizeMin, p.size_min); gl.uniform1f(u.uSizeMax, p.size_max); gl.uniform1f(u.uSizeBias, p.size_bias);
    gl.uniform1f(u.uFlakeScale, p.flake_scale); gl.uniform1f(u.uFiberScale, p.fiber_scale); gl.uniform1f(u.uFluffScale, p.fluff_scale);
    gl.uniform1f(u.uSteady, p.steady || 0); gl.uniform1f(u.uCFN, p.cam_fade_near || 0); gl.uniform1f(u.uCFF, p.cam_fade_far || 0);
    this._shapeUniforms(prog, p, frame);
    gl.uniform1i(u.uHold, p.holdout && distTex ? 1 : 0); gl.uniform1i(u.uFg, p.fg_far > p.fg_near || p.fg_near > 0 ? 1 : 0); gl.uniform1f(u.uFgNear, p.fg_near); gl.uniform1f(u.uFgFar, p.fg_far);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    // splat
    if (final && !this.pSplatF) this.pSplatF = this._prog(vsSplat(48), FS_SPLAT);
    const NSd = final ? 48 : MF_NS;
    prog = final ? this.pSplatF : this.pSplat;
    gl.useProgram(prog); gl.bindVertexArray(this.vao);
    this._target([this.acc.held, this.acc.free], W, H);
    gl.enable(gl.BLEND); gl.blendEquation(gl.FUNC_ADD); gl.blendFunc(gl.ONE, gl.ONE);
    for (let k = 0; k < 4; k++) this._bind(prog, k, 'uT' + k, S.prep[k]);
    gl.uniform2f(prog.u.uWH, W, H); gl.uniform1f(prog.u.uMaxR, p.max_bokeh * sc);
    const useImg = !!(this.shapeTex && p.bokeh_image), rot = p.bokeh_rot * Math.PI / 180;
    gl.uniform1i(prog.u.uBlades, blades); gl.uniform1i(prog.u.uImg, useImg ? 1 : 0);
    gl.uniform1f(prog.u.uRot, 0.25 + rot); gl.uniform1f(prog.u.uImgRot, rot); gl.uniform1f(prog.u.uFall, p.bokeh_falloff);
    gl.uniform1f(prog.u.uAspect, aspect); gl.uniform1f(prog.u.uCat, catAmt); gl.uniform1i(prog.u.uShape, shape);
    this._bind(prog, 4, 'uShapeTex', this.shapeTex || this.dummy);
    gl.uniform1fv(prog.u.uApN, useImg ? this._apNormImage(p.bokeh_falloff) : this._apNorm(blades >= 3 ? blades : 0, p.bokeh_falloff, blades >= 3 ? 0.25 + rot : 0.25));
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, I.n * NSd);
    gl.disable(gl.BLEND);
    // embers: their own per-point maths (render_embers), then the same splat with more samples along their streaks
    const E = this.emb;
    if (p.embers && E && E.frame === frame) {
      const ep = this.pEPrep, eu = ep.u, d = E.d;
      gl.useProgram(ep); gl.bindVertexArray(this.vao);
      this._target(E.prep, MF_TW, E.TH);
      this._bind(ep, 0, 'uEP', E.pos[E.cur]); this._bind(ep, 1, 'uEV', E.vel[E.cur]); this._bind(ep, 2, 'uDist', distTex || this.dummy);
      gl.uniform1ui(eu.uSeedOff, d.seedoff); gl.uniform1i(eu.uNS, d.ns); gl.uniform1i(eu.uNM, d.nm); gl.uniform1i(eu.uNG, d.ng); gl.uniform1i(eu.uN, d.n);
      gl.uniform1f(eu.uELife, p.ember_life); gl.uniform1f(eu.uSu, I.su);
      gl.uniform3fv(eu.uC, C); gl.uniform1fv(eu.uCT, CT);
      gl.uniform2f(eu.uWH, W, H); gl.uniform2f(eu.uWH0, distTex ? distTex.w : 1, distTex ? distTex.h : 1);
      gl.uniform1f(eu.uFocal, focal); gl.uniform1f(eu.uFilm, film); gl.uniform1f(eu.uFstop, fstop); gl.uniform1f(eu.uFocusM, focusM);
      gl.uniform1f(eu.uMaxR, p.max_bokeh * sc); gl.uniform1f(eu.uFocalPx, focalPx); gl.uniform1i(eu.uDof, p.dof ? 1 : 0);
      gl.uniform1f(eu.uTOpen, tO); gl.uniform1f(eu.uTClose, tC); gl.uniform1f(eu.uSpf, p.speed / p.fps); gl.uniform1f(eu.uExpT, (tC - tO) * p.speed / p.fps);
      gl.uniform1f(eu.uFC, fc); gl.uniform1f(eu.uFront, p.front_mult); gl.uniform1f(eu.uBack, p.back_mult);
      gl.uniform1f(eu.uTemp, p.ember_temp); gl.uniform1f(eu.uESize, p.ember_size); gl.uniform1f(eu.uEGain, p.ember_gain); gl.uniform1f(eu.uTwinkle, p.ember_twinkle);
      gl.uniform1f(eu.uSteady, p.steady || 0); gl.uniform1f(eu.uEFN, p.ember_fade_near || 0); gl.uniform1f(eu.uEFF, p.ember_fade_far || 0);
      this._shapeUniforms(ep, p, frame);
      gl.uniform1i(eu.uHold, p.holdout && distTex ? 1 : 0); gl.uniform1i(eu.uFg, p.fg_far > p.fg_near || p.fg_near > 0 ? 1 : 0); gl.uniform1f(eu.uFgNear, p.fg_near); gl.uniform1f(eu.uFgFar, p.fg_far);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      const sp = this.pSplatE;
      gl.useProgram(sp); gl.bindVertexArray(this.vao);
      this._target([this.acc.held, this.acc.free], W, H);
      gl.enable(gl.BLEND); gl.blendEquation(gl.FUNC_ADD); gl.blendFunc(gl.ONE, gl.ONE);
      for (let k = 0; k < 4; k++) this._bind(sp, k, 'uT' + k, E.prep[k]);
      gl.uniform2f(sp.u.uWH, W, H); gl.uniform1f(sp.u.uMaxR, p.max_bokeh * sc);
      const useImg = !!(this.shapeTex && p.bokeh_image), rot = p.bokeh_rot * Math.PI / 180;
      gl.uniform1i(sp.u.uBlades, blades); gl.uniform1i(sp.u.uImg, useImg ? 1 : 0);
      gl.uniform1f(sp.u.uRot, 0.25 + rot); gl.uniform1f(sp.u.uImgRot, rot); gl.uniform1f(sp.u.uFall, p.bokeh_falloff);
      gl.uniform1f(sp.u.uAspect, aspect); gl.uniform1f(sp.u.uCat, catAmt); gl.uniform1i(sp.u.uShape, shape);
      this._bind(sp, 4, 'uShapeTex', this.shapeTex || this.dummy);
      gl.uniform1fv(sp.u.uApN, useImg ? this._apNormImage(p.bokeh_falloff) : this._apNorm(blades >= 3 ? blades : 0, p.bokeh_falloff, blades >= 3 ? 0.25 + rot : 0.25));
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, d.n * 48);
      gl.disable(gl.BLEND);
    }
    return { W, H, n: I.n };
  }
  // a half-float copy of a W x H float texture (Final output keeps one per frame)
  copyHalf(src, W, H) {
    const gl = this.gl, t = this._tex(W, H, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT, null);
    gl.disable(gl.BLEND); gl.useProgram(this.pCopy); gl.bindVertexArray(this.vao);
    this._target([t], W, H); this._bind(this.pCopy, 0, 'uSrc', src);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    return t;
  }
  // viewer: draw into the default framebuffer, image placed at rect (clip space)
  composite(rect, opts, plateTex) {
    const gl = this.gl, prog = this.pComp;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.useProgram(prog); gl.bindVertexArray(this.vao);
    gl.uniform4fv(prog.u.uRect, rect);
    this._bind(prog, 0, 'uPlate', plateTex || this.dummy);
    this._bind(prog, 1, 'uHeld', this.acc ? this.acc.held : this.dummy); this._bind(prog, 2, 'uFree', this.acc ? this.acc.free : this.dummy);
    gl.uniform1i(prog.u.uShowPlate, opts.view !== 'dust' ? 1 : 0); gl.uniform1i(prog.u.uShowDust, opts.view !== 'plate' && this.acc ? 1 : 0);
    gl.uniform1i(prog.u.uLayer, opts.layer === 'noholdout' ? 1 : 0); gl.uniform1i(prog.u.uHasPlate, plateTex ? 1 : 0);
    gl.uniform1f(prog.u.uGain, opts.gain);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }
  draw3d(M, p, ptSize) {
    if (!this.init || !this.init.n) return;
    const gl = this.gl, prog = this.p3d, S = this.sim;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.useProgram(prog); gl.bindVertexArray(this.vao);
    this._passCommon(prog);
    this._bind(prog, 0, 'uPos', S.pos[S.cur]); this._bind(prog, 1, 'uVis', S.vis);
    gl.uniformMatrix4fv(prog.u.uM, false, M);
    gl.uniform1f(prog.u.uFade, p.fade); gl.uniform3fv(prog.u.uFadeN, [p.fade_xn, p.fade_yn, p.fade_zn]); gl.uniform3fv(prog.u.uFadeP, [p.fade_xp, p.fade_yp, p.fade_zp]);
    gl.uniform1f(prog.u.uPt, ptSize);
    gl.drawArrays(gl.POINTS, 0, this.init.n);
  }
}
window.MFPreview = MFPreview;
