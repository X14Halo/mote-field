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
uniform vec4 uPC[24], uPV[24]; uniform int uNP; uniform float uPAmt, uPRs;     // pushers: centre + radius (m), velocity + weight
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
  for (int i = 0; i < 24; i++) { if (i >= uNP) break;          // DustSim.step: flow around the mover now, a fading wake behind
    vec3 d = Pw - uPC[i].xyz, U = uPV[i].xyz; float dist = length(d) + 1e-6, a = uPC[i].w * uPRs;
    if (uPV[i].w >= 0.999) { vec3 n = d / dist; float k3 = 0.5 * pow(a / max(dist, a), 3.0) * uPAmt; tgt += k3 * (3.0 * dot(n, U) * n - U); }
    else { float q = dist / a, g = exp(-q * q) * uPV[i].w * uPAmt; tgt += U * (g * 0.35); churn += g * length(U); } }   // WAKE_DRAG
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
uniform vec3 uBP[24], uBN[24], uBF[24], uBAmb; uniform int uNB2; uniform float uFillK;
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
  float R = clamp(abs(coc) * 0.5, 0.25, uMaxR);
  float ppm = uFocalPx / max(z_m, 1e-3);
  float u = hh[1];
  float rate = uTumble * (typ == 1 ? 0.12 + 0.78*hh[3] : typ == 0 ? 0.1 + 0.6*hh[3] : 0.03 + 0.27*hh[3]);
  vec3 ax = tumbleAxis(hh[4], hh[5], hh[6], rate, uTsec);
  float sigma, rs, gph = uForward; vec3 tcol = vec3(1.0);
  float ln = 0.0;
  if (typ == 0) {
    float dg = uSizeMin * pow(uSizeMax / max(uSizeMin, 1e-3), pow(u, uSizeBias));
    sigma = (dg / 40.0) * (dg / 40.0) * (1.0 + 0.12 * sin(2.0*PI*(rate * uTsec * 1.3 + hh[7])));
    rs = 0.5 * dg * 1e-6 * ppm;
  } else if (typ == 1) {
    float df = 30.0 * pow(8.0, pow(u, 2.2)) * uFlakeScale;
    float nl = abs(dot(ax, uL)), nv = abs(dot(ax, Vd));
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
  if (typ == 1 && vis > 0.0 && uGlint > 0.0) {
    vec3 Hh = normalize(uL + Vd); float g = 0.0, gs = 0.0;
    for (int k = 0; k < 4; k++) gs += shutterW(float(k) / 3.0);
    for (int k = 0; k < 4; k++) { float tt = uTsec + (uTOpen + (uTClose - uTOpen) * float(k) / 3.0) * uSpf;
      g += shutterW(float(k) / 3.0) * 4.0 / gs * pow(abs(dot(tumbleAxis(hh[4], hh[5], hh[6], rate, tt), Hh)), 90.0); }
    rad += uSun * (vis * g * 0.25 * 8.0 * uGlint * (0.4 + 0.6*hh[2]));
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
  float hold = 1.0;
  if (uHold == 1) {
    ivec2 sp = ivec2(clamp(int(pp.x / uWH.x * uWH0.x), 0, int(uWH0.x) - 1), clamp(int(pp.y / uWH.y * uWH0.y), 0, int(uWH0.y) - 1));
    float surf = texelFetch(uDist, sp, 0).r;
    float occl = surf > 0.0 ? smooth01(((dist - surf) / max(surf, 1e-6) + 0.01) / 0.04) : 0.0;
    float fgw = 1.0;
    if (uFg == 1 && uFgFar > uFgNear) fgw = smooth01((surf - uFgNear) / (uFgFar - uFgNear));
    else if (uFg == 1) fgw = surf >= uFgNear ? 1.0 : 0.0;
    hold = 1.0 - occl * fgw;
  }
  o0 = vec4(po.xy, pc.xy);
  o1 = vec4(col, Reff);
  o2 = vec4(hold, S, K, bend);
  o3 = vec4(fd, 0.0, 0.0);
}`;

// ---- splat: instanced quads, one per mote sample ----------------------------------------------------
const VS_SPLAT = GLSL_COMMON + `
uniform sampler2D uT0, uT1, uT2, uT3; uniform vec2 uWH; uniform float uMaxR, uCat, uAspect; uniform int uShape;
flat out vec3 vE; flat out float vHold, vR, vSig; flat out vec2 vCat; out vec2 vOff;
float shutterW(float u){ return uShape == 1 ? 1.0 / (1.0 + exp(-9.0 * (u - 0.55))) : 1.0; }
void main(){
  int m = gl_InstanceID / ${MF_NS}, s = gl_InstanceID % ${MF_NS};
  ivec2 tp = texel(m);
  vec4 t1 = texelFetch(uT1, tp, 0);
  gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
  if (t1.w < 0.0) return;
  vec4 t0 = texelFetch(uT0, tp, 0), t2 = texelFetch(uT2, tp, 0), t3 = texelFetch(uT3, tp, 0);
  int S = int(t2.y + 0.5), K = int(t2.z + 0.5);
  int Kq = min(K, ${MF_NS}), Sq = min(S, max(1, ${MF_NS} / Kq));
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
  float ws = 0.0; for (int q = 0; q < ${MF_NS}; q++) { if (q >= Sq) break; ws += shutterW((float(q) + 0.5) / float(Sq)); }
  vE = t1.rgb * (shutterW(ts) / (ws * float(Kq))); vHold = t2.x;
  vCat = (vec2(X, Y) - uWH * 0.5) / length(uWH * 0.5) * uCat;     // cat-eye offset, image axes (y down)
  int c = gl_VertexID;
  vec2 corner = vec2((c == 1 || c == 2 || c == 4) ? 1.0 : -1.0, (c == 2 || c == 4 || c == 5) ? 1.0 : -1.0);
  vOff = corner * half_;
  vec2 pix = vec2(X, Y) + vec2(vOff.x, -vOff.y);            // vOff.y points up the screen
  gl_Position = vec4(pix.x / uWH.x * 2.0 - 1.0, 1.0 - pix.y / uWH.y * 2.0, 0.0, 1.0);
}`;
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
layout(location=0) out vec4 oPlate; layout(location=1) out vec4 oDist;
float trace(vec3 O, vec3 D, out vec3 n, out vec3 alb){
  vec3 Ds = vec3(abs(D.x) < 1e-9 ? 1e-9 : D.x, abs(D.y) < 1e-9 ? 1e-9 : D.y, abs(D.z) < 1e-9 ? 1e-9 : D.z);
  vec3 inv = 1.0 / Ds; float best = 1e30; n = vec3(0.0); alb = vec3(0.0);
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
  bool hit = t < 1e29;
  vec3 rgb = uSky;
  if (hit) {
    vec3 P = uC[0] + D * t;
    float nl = max(dot(n, uLs), 0.0);
    float lit = (nl > 0.0 && !blocked(P + n * 0.3, uLs)) ? 1.0 : 0.0;
    float fill = uInt * (0.035 + 0.02 * clamp(n.y, 0.0, 1.0));
    rgb = alb * (uSunP * (nl * lit) + fill * vec3(0.95, 0.97, 1.0));
  }
  oPlate = vec4(rgb, 1.0);
  oDist = vec4(hit ? t : 0.0, 0.0, 0.0, 1.0);
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

class MFPreview {
  constructor(gl) {
    this.gl = gl;
    this.ok = !!gl.getExtension('EXT_color_buffer_float');
    this.floatBlend = !!gl.getExtension('EXT_float_blend');
    if (!this.ok) return;
    const P = (vs, fs) => this._prog(vs, fs);
    this.pVis = P(VS_FULL, FS_VIS); this.pStep = P(VS_FULL, FS_STEP); this.pPrep = P(VS_FULL, FS_PREP);
    this.pSplat = P(VS_SPLAT, FS_SPLAT); this.pPlate = P(VS_FULL, FS_PLATE); this.pComp = P(VS_QUAD, FS_COMP);
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
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, w, h, 0, fmt, type, data || null);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
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
  setBoxes(boxes, walker) {         // [[mnx, mny, mnz, mxx, mxy, mxz, r, g, b], ...]; walker: motefield.STANDIN_WALKER + frame range
    // the walker is one more box at the end, moved every frame. It is in the plate and its depth, but (as in motefield.py,
    // whose light map is built from the still room) not in the shadows the dust is lit with: nb counts the still boxes
    const nb = boxes.length, na = nb + (walker ? 1 : 0), a = new Float32Array(na * 3 * 4);
    boxes.forEach((b, k) => { a.set([b[0], b[1], b[2], 0], k * 4); a.set([b[3], b[4], b[5], 0], (na + k) * 4); a.set([b[6], b[7], b[8], 0], (2 * na + k) * 4); });
    if (this.boxes) this._free(this.boxes.tex);
    this.boxes = { nb, na, walker: walker || null, wf: null, tex: this._rgba32(na, 3, a) };
    if (walker) this.walkerTo(walker.first);
  }
  walkerBox(frame) {        // motefield.standin_walker
    const W = this.boxes && this.boxes.walker; if (!W) return null;
    const u = (frame - W.first) / Math.max(W.last - W.first, 1), c = W.start.map((v, i) => v + (W.end[i] - v) * u), sz = W.size;
    return { mn: [c[0] - sz[0] / 2, c[1], c[2] - sz[2] / 2], mx: [c[0] + sz[0] / 2, c[1] + sz[1], c[2] + sz[2] / 2], color: W.color };
  }
  walkerTo(frame) {
    const B = this.boxes; if (!B || !B.walker || B.wf === frame) return;
    const w = this.walkerBox(frame), gl = this.gl, k = B.nb;
    gl.bindTexture(gl.TEXTURE_2D, B.tex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, k, 0, 1, 1, gl.RGBA, gl.FLOAT, new Float32Array([...w.mn, 0]));
    gl.texSubImage2D(gl.TEXTURE_2D, 0, k, 1, 1, 1, gl.RGBA, gl.FLOAT, new Float32Array([...w.mx, 0]));
    gl.texSubImage2D(gl.TEXTURE_2D, 0, k, 2, 1, 1, gl.RGBA, gl.FLOAT, new Float32Array([...w.color, 0]));
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
    if (old) this._free(old.dist, old.plate, old.dof);
    const e = { dist: this._tex(d.w, d.h, gl.R32F, gl.RED, gl.FLOAT, new Float32Array(d.dist.buffer, d.dist.byteOffset, d.w * d.h)) };
    if (d.rgb) {
      const u16 = new Uint16Array(d.rgb.buffer, d.rgb.byteOffset, d.pw * d.ph * 3);
      e.plate = this._tex(d.pw, d.ph, gl.RGB16F, gl.RGB, gl.HALF_FLOAT, u16);
      e.synthetic = !!d.synthetic;        // shaded from the utility pass: no lens blur of its own yet
    }
    this.frameTex.set(f, e);
  }
  clearFrameImages() { for (const e of this.frameTex.values()) this._free(e.dist, e.plate, e.dof); this.frameTex.clear(); }

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
    const r = soft ? (p.shadow_soft || 0) * 0.01 / I.su : 0;
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
    if (P && P.n) { gl.uniform4fv(prog.u.uPC, P.c); gl.uniform4fv(prog.u.uPV, P.v); gl.uniform1f(prog.u.uPAmt, p.push_amount); gl.uniform1f(prog.u.uPRs, Math.max(p.push_radius, 0.05)); }
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
  // character pushers: {frame: [[cx, cy, cz, r, vx, vy, vz], ...]} (metres, m/s), from motefield.utility_pushers
  setPushers(d) { this.pushers = d || null; }
  _pushersFor(F, p) {
    const c = new Float32Array(96), v = new Float32Array(96); let n = 0;
    for (let k = 0; k <= 5; k++) {             // PUSH_WAKE: this frame's, plus a fading wake from the 5 before
      const L = this.pushers[F - k]; if (!L) continue;
      const w = Math.pow(0.6, k);
      for (const q of L) { if (n >= 24) break; c.set([q[0], q[1], q[2], q[3] * (1 + 0.25 * k)], n * 4); v.set([q[4], q[5], q[6], w], n * 4); n++; }
    }
    return { n, c, v };
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
      this._push = p.push && this.pushers ? this._pushersFor(S.frame + 1, p) : null;     // motefield.pushers_for_step
      this._visPass(p);
      for (let k = 0; k < sub; k++) { this._stepPass(p, S.t, dt); S.t += dt; }
      S.frame++; n++;
    }
    this._visPass(p);
    this._visPass(p, true);           // what the render uses: soft edges
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
  standinPlate(frame, W0, H0, plateLight) {
    this._ensurePlate(W0, H0);
    if (this.pl.frame === frame && this.pl.key === W0 + 'x' + H0) return;
    this.walkerTo(frame);
    const gl = this.gl, prog = this.pPlate, c = this.camAt(frame);
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
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this.pl.frame = frame; this.pl.key = W0 + 'x' + H0;
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
  renderDust(frame, p, W0, H0, distTex, hold) {
    const gl = this.gl, I = this.init, S = this.sim;
    const sc = Math.max(Math.min(p.proxy, 1), 0.1), W = Math.max(Math.round(W0 * sc), 8), H = Math.max(Math.round(H0 * sc), 8);
    this._ensureTargets(W, H);
    gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST); gl.disable(gl.SCISSOR_TEST);
    this._target([this.acc.held, this.acc.free], W, H);
    gl.clearBufferfv(gl.COLOR, 0, [0, 0, 0, 0]); gl.clearBufferfv(gl.COLOR, 1, [0, 0, 0, 0]);
    if (!I.n) return { W, H, n: 0 };
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
    gl.uniform1i(u.uHold, p.holdout && distTex ? 1 : 0); gl.uniform1i(u.uFg, p.fg_fade ? 1 : 0); gl.uniform1f(u.uFgNear, p.fg_near); gl.uniform1f(u.uFgFar, p.fg_far);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    // splat
    prog = this.pSplat;
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
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, I.n * MF_NS);
    gl.disable(gl.BLEND);
    return { W, H, n: I.n };
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
