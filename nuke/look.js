// MoteField viewer look: what turns "dust over a clay box" back into a picture you can judge dust in.
//  * the stand-in set, lit like a render: sun with shadows, one bounce of light (ray traced, denoised), a light shaft
//    through the window (haze), and the same lens defocus the dust gets
//  * glow (bloom and a little halation) and a filmic tone curve (AgX), like the MoteField mockup
// Viewer only: none of this goes into the dust the tools render.
'use strict';

const LK_COMMON = GLSL_COMMON + `
uniform sampler2D uBoxes; uniform int uNB;
vec3 safeDir(vec3 d){ return vec3(abs(d.x) < 1e-9 ? 1e-9 : d.x, abs(d.y) < 1e-9 ? 1e-9 : d.y, abs(d.z) < 1e-9 ? 1e-9 : d.z); }
float traceB(vec3 O, vec3 D, float tmin, out vec3 n, out vec3 alb){
  vec3 Ds = safeDir(D), inv = 1.0 / Ds; float best = 1e30; n = vec3(0.0); alb = vec3(0.0);
  for (int k = 0; k < uNB; k++) {
    vec3 mn = texelFetch(uBoxes, ivec2(k, 0), 0).xyz, mx = texelFetch(uBoxes, ivec2(k, 1), 0).xyz;
    vec3 t1 = (mn - O) * inv, t2 = (mx - O) * inv;
    vec3 tn = min(t1, t2), tf = max(t1, t2);
    float a = max(max(tn.x, tn.y), tn.z), b = min(min(tf.x, tf.y), tf.z);
    if (b >= max(a, tmin) && a > tmin && a < best) {
      best = a;
      int axi = (tn.x >= tn.y && tn.x >= tn.z) ? 0 : (tn.y >= tn.z ? 1 : 2);
      n = vec3(0.0); n[axi] = -sign(Ds[axi]);
      alb = texelFetch(uBoxes, ivec2(k, 2), 0).rgb;
    }
  }
  return best;
}
bool blockedB(vec3 P, vec3 L){
  vec3 inv = 1.0 / safeDir(L);
  for (int k = 0; k < uNB; k++) {
    vec3 mn = texelFetch(uBoxes, ivec2(k, 0), 0).xyz, mx = texelFetch(uBoxes, ivec2(k, 1), 0).xyz;
    vec3 t1 = (mn - P) * inv, t2 = (mx - P) * inv;
    vec3 tn = min(t1, t2), tf = max(t1, t2);
    if (min(min(tf.x, tf.y), tf.z) >= max(max(max(tn.x, tn.y), tn.z), 1e-3)) return true;
  }
  return false;
}
float hash13(vec3 p){ p = fract(p * 0.1031); p += dot(p, p.zyx + 31.32); return fract((p.x + p.y) * p.z); }
uniform vec3 uC[4]; uniform float uTh, uTv; uniform vec2 uWH;
uniform vec3 uLs, uSunP, uSky; uniform float uInt;
vec3 rayDir(vec2 px){ float sx = (px.x / uWH.x * 2.0 - 1.0) * uTh, sy = (1.0 - px.y / uWH.y * 2.0) * uTv; return normalize(-uC[3] + sx * uC[1] + sy * uC[2]); }
// what a surface sends back: the stand-in's own shading (sun, with shadows, plus a little fill)
vec3 shade(vec3 P, vec3 n, vec3 alb, float fillK){
  float nl = max(dot(n, uLs), 0.0);
  float lit = (nl > 0.0 && !blockedB(P + n * 0.3, uLs)) ? 1.0 : 0.0;
  float fill = uInt * (0.035 + 0.02 * clamp(n.y, 0.0, 1.0)) * fillK;
  return alb * (uSunP * (nl * lit) + fill * vec3(0.95, 0.97, 1.0));
}
`;

// 1. primary hit (full res): direct light, normal + distance, albedo
//    cutaway: from outside the room, rays start where they enter it, so the near walls don't block the view
const LK_GBUF = LK_COMMON + `
uniform vec3 uRoomLo, uRoomHi; uniform int uCut;
layout(location=0) out vec4 oDirect; layout(location=1) out vec4 oNrm; layout(location=2) out vec4 oAlb; layout(location=3) out vec4 oDist;
void main(){
  vec3 D = rayDir(gl_FragCoord.xy), n, alb;
  float t0 = 0.0;
  vec3 O = uC[0];
  if (uCut == 1 && any(lessThan(O, uRoomLo)) || uCut == 1 && any(greaterThan(O, uRoomHi))) {
    vec3 inv = 1.0 / safeDir(D), a1 = (uRoomLo - O) * inv, a2 = (uRoomHi - O) * inv;
    vec3 tn = min(a1, a2), tf = max(a1, a2);
    float ta = max(max(tn.x, tn.y), tn.z), tb = min(min(tf.x, tf.y), tf.z);
    if (tb < max(ta, 0.0)) { oDirect = vec4(0.0); oNrm = vec4(0.0); oAlb = vec4(0.0); oDist = vec4(0.0); return; }
    t0 = max(ta, 0.0) + 0.5;
  }
  float t = traceB(O + D * t0, D, 1e-4, n, alb);
  if (t < 1e29) t += t0;
  if (t > 1e29) { oDirect = vec4(uSky, 0.0); oNrm = vec4(0.0); oAlb = vec4(0.0); oDist = vec4(0.0); return; }
  vec3 P = uC[0] + D * t;
  oDirect = vec4(shade(P, n, alb, 0.35), 1.0);
  oNrm = vec4(n, t); oAlb = vec4(alb, 1.0); oDist = vec4(t, 0.0, 0.0, 1.0);
}`;

// 2. one bounce (half res): cosine-weighted rays from each surface, what they hit, lit by the sun
const LK_GI = LK_COMMON + `
uniform sampler2D uNrm; uniform int uSpp; uniform vec2 uFull;
out vec4 o;
void main(){
  ivec2 fp = min(ivec2(gl_FragCoord.xy) * 2, ivec2(uFull) - 1);
  vec4 nd = texelFetch(uNrm, fp, 0);
  if (nd.w <= 0.0) { o = vec4(0.0); return; }
  vec3 n = nd.xyz, P = uC[0] + rayDir(vec2(fp) + 0.5) * nd.w + n * 0.2;
  vec3 t1 = normalize(abs(n.y) < 0.9 ? cross(n, vec3(0.0, 1.0, 0.0)) : cross(n, vec3(1.0, 0.0, 0.0))), t2 = cross(n, t1);
  vec3 acc = vec3(0.0);
  for (int i = 0; i < 64; i++) {
    if (i >= uSpp) break;
    float u1 = hash13(vec3(gl_FragCoord.xy, float(i) * 7.13)), u2 = hash13(vec3(gl_FragCoord.yx * 1.37, float(i) * 3.71 + 11.0));
    float r = sqrt(u1), ph = 6.2831853 * u2;
    vec3 d = normalize(t1 * (r * cos(ph)) + t2 * (r * sin(ph)) + n * sqrt(max(1.0 - u1, 0.0)));
    vec3 hn, ha; float th = traceB(P, d, 0.05, hn, ha);
    acc += th > 1e29 ? uSky : shade(P + d * th, hn, ha, 0.35);
  }
  o = vec4(acc / float(max(uSpp, 1)), 1.0);
}`;

// 3. edge-aware smoothing of the bounce light (half res)
const LK_ATROUS = GLSL_COMMON + `
uniform sampler2D uTex, uNrm; uniform vec2 uFull; uniform int uStep; out vec4 o;
void main(){
  ivec2 g = ivec2(gl_FragCoord.xy), dim = textureSize(uTex, 0), fm = ivec2(uFull) - 1;
  vec4 n0 = texelFetch(uNrm, min(g * 2, fm), 0);
  if (n0.w <= 0.0) { o = texelFetch(uTex, g, 0); return; }
  vec3 s = vec3(0.0); float ws = 0.0;
  for (int j = -2; j <= 2; j++) for (int i = -2; i <= 2; i++) {
    ivec2 q = clamp(g + ivec2(i, j) * uStep, ivec2(0), dim - 1);
    vec4 nq = texelFetch(uNrm, min(q * 2, fm), 0);
    float k = (3.0 - abs(float(i))) * (3.0 - abs(float(j)));
    float w = k * exp(-abs(nq.w - n0.w) / (0.02 * n0.w + 1.0)) * pow(max(dot(nq.xyz, n0.xyz), 0.0), 8.0);
    s += texelFetch(uTex, q, 0).rgb * w; ws += w;
  }
  o = vec4(s / max(ws, 1e-5), 1.0);
}`;

// 4. the light shaft: single scattering along each view ray (half res)
const LK_HAZE = LK_COMMON + `
uniform sampler2D uNrm; uniform vec2 uFull; uniform float uHaze, uG, uSu; out vec4 o;
float hg(float c, float g){ return (1.0 - g*g) / pow(max(1.0 + g*g - 2.0*g*c, 1e-4), 1.5); }
void main(){
  ivec2 fp = min(ivec2(gl_FragCoord.xy) * 2, ivec2(uFull) - 1);
  vec3 rd = rayDir(vec2(fp) + 0.5);
  float d = texelFetch(uNrm, fp, 0).w; float tmax = d > 0.0 ? d : 2000.0;
  const int N = 40; float dt = tmax / float(N);
  float j = hash13(vec3(gl_FragCoord.xy, 5.0));
  float acc = 0.0;
  for (int i = 0; i < N; i++) { vec3 p = uC[0] + rd * ((float(i) + j) * dt); if (!blockedB(p, uLs)) acc += 1.0; }
  float len_m = tmax * uSu;
  vec3 c = uSunP * (acc / float(N)) * len_m * hg(dot(uLs, rd), uG) * uHaze * 0.011 + uSky * 0.004 * uHaze * len_m;   // as in the mockup
  o = vec4(c, 1.0);
}`;

// 5. combine: direct + albedo x bounce + haze
const LK_COMBINE = GLSL_COMMON + `
uniform sampler2D uDirect, uNrm, uAlb, uGI, uHazeT; uniform vec2 uFull; uniform float uGIAmt; out vec4 o;
void main(){
  ivec2 ip = ivec2(gl_FragCoord.xy);
  vec4 D = texelFetch(uDirect, ip, 0); vec3 c = D.rgb;
  if (D.a > 0.0) {
    vec4 n0 = texelFetch(uNrm, ip, 0); ivec2 dim = textureSize(uGI, 0);
    vec2 g = (vec2(ip) + 0.5) * 0.5 - 0.5; ivec2 g0 = ivec2(floor(g)); vec2 f = fract(g);
    vec3 s = vec3(0.0); float ws = 0.0;
    for (int j = 0; j < 2; j++) for (int i = 0; i < 2; i++) {
      ivec2 gc = clamp(g0 + ivec2(i, j), ivec2(0), dim - 1); vec4 nq = texelFetch(uNrm, min(gc * 2, ivec2(uFull) - 1), 0);
      float w = (i == 0 ? 1.0 - f.x : f.x) * (j == 0 ? 1.0 - f.y : f.y) * exp(-abs(nq.w - n0.w) / (0.03 * n0.w + 1.0)) * pow(max(dot(nq.xyz, n0.xyz), 0.0), 6.0) + 1e-5;
      s += texelFetch(uGI, gc, 0).rgb * w; ws += w;
    }
    c += texelFetch(uAlb, ip, 0).rgb * (s / ws) * uGIAmt;
  }
  vec2 uv = (vec2(ip) + 0.5) / uFull; vec2 hx = 1.0 / vec2(textureSize(uHazeT, 0));
  vec3 h = vec3(0.0); float wh = 0.0;
  for (int y = -1; y <= 1; y++) for (int x = -1; x <= 1; x++) { float w = (2.0 - abs(float(x))) * (2.0 - abs(float(y))); h += texture(uHazeT, uv + vec2(x, y) * hx * 1.25).rgb * w; wh += w; }
  o = vec4(c + h / wh, 1.0);
}`;

// 6. lens defocus of the plate (gather; the same circle of confusion as the dust)
const LK_PDOF = GLSL_COMMON + `
uniform sampler2D uPlate, uDist; uniform vec2 uWH; uniform float uFocal, uFilm, uFstop, uFocusM, uSu, uMaxR, uFront, uBack, uSkyMul, uRot; uniform int uDof, uN;
out vec4 o;
float hash12(vec2 p){ vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float cocPx(float d){ if (uDof == 0) return 0.0; float s2 = max(d > 0.0 ? d * uSu : 1e3, 0.02) * 1000.0, s1 = uFocusM * 1000.0;
  float c = (uFocal / uFstop) * uFocal * (s2 - s1) / (s2 * max(s1 - uFocal, 1.0)) / uFilm * uWH.x; return c * (c < 0.0 ? uFront : uBack); }
// plate and distance may be at a different resolution than the view
ivec2 at(sampler2D t, ivec2 p){ ivec2 s = textureSize(t, 0); return clamp(ivec2((vec2(p) + 0.5) / uWH * vec2(s)), ivec2(0), s - 1); }
float dAt(ivec2 p){ return texelFetch(uDist, at(uDist, p), 0).r; }
vec3 pl(ivec2 p){ vec3 c = texelFetch(uPlate, at(uPlate, p), 0).rgb; return dAt(p) > 0.0 ? c : c * uSkyMul; }
void main(){
  ivec2 ip = ivec2(gl_FragCoord.xy), mx = ivec2(uWH) - 1;
  float d0 = dAt(ip); vec3 c0 = pl(ip);
  float r0 = abs(cocPx(d0)) * 0.5;
  if (uMaxR < 0.75) { o = vec4(c0, 1.0); return; }
  float w0 = 1.0 / max(3.14159 * r0 * r0, 1.0);
  vec3 acc = c0 * w0; float ws = w0;
  const float GA = 2.39996323; int N = uN;
  float jr = hash12(vec2(ip)) * 6.2831853 + uRot, jo = hash12(vec2(ip) + 17.3 + uRot);
  float sa = 3.14159 * uMaxR * uMaxR / float(N);
  for (int i = 0; i < 400; i++) {
    if (i >= N) break;
    float r = sqrt((float(i) + jo) / float(N)) * uMaxR, a = float(i) * GA + jr;
    ivec2 sp = clamp(ip + ivec2(floor(vec2(cos(a), sin(a)) * r + 0.5)), ivec2(0), mx);
    float ds = dAt(sp), rs = abs(cocPx(ds)) * 0.5;
    float w = clamp(rs - r + 1.0, 0.0, 1.0);
    if ((ds > 0.0 ? ds : 1e9) > (d0 > 0.0 ? d0 : 1e9) * 1.02) w *= clamp(r0 - r + 1.0, 0.0, 1.0);
    w *= sa / max(3.14159 * rs * rs, 1.0);
    acc += pl(sp) * w; ws += w;
  }
  o = vec4(acc / ws, 1.0);
}`;

// 7. plate x set brightness + dust, into an HDR image at plate resolution
const LK_COMP = GLSL_COMMON + `
uniform sampler2D uPlate, uDust; uniform int uHasPlate, uShowPlate, uShowDust, uDustFlip; uniform float uSet; uniform vec2 uWH;
out vec4 o;
void main(){
  vec2 uv = gl_FragCoord.xy / uWH;            // texture rows run top-down for plates
  vec3 c = vec3(0.0);
  if (uShowPlate == 1 && uHasPlate == 1) c += texture(uPlate, vec2(uv.x, uv.y)).rgb * uSet;
  if (uShowDust == 1) { ivec2 ds = textureSize(uDust, 0); ivec2 q = ivec2(vec2(uv.x, uDustFlip == 1 ? 1.0 - uv.y : uv.y) * vec2(ds));
    c += texelFetch(uDust, clamp(q, ivec2(0), ds - 1), 0).rgb; }   // float32 buffers can't always be filtered
  o = vec4(c, 1.0);
}`;
const LK_DOWN = GLSL_COMMON + `
uniform sampler2D uTex; uniform vec2 uRes; out vec4 o;
void main(){ vec2 uv = gl_FragCoord.xy / uRes, t = 1.0 / vec2(textureSize(uTex, 0));
  vec3 s = min(texture(uTex, uv + t * vec2(-1, -1)).rgb, vec3(8.0)) + min(texture(uTex, uv + t * vec2(1, -1)).rgb, vec3(8.0))
         + min(texture(uTex, uv + t * vec2(-1, 1)).rgb, vec3(8.0)) + min(texture(uTex, uv + t * vec2(1, 1)).rgb, vec3(8.0));
  o = vec4(s * 0.25, 1.0); }`;
const LK_UP = GLSL_COMMON + `
uniform sampler2D uLow, uCur; uniform vec2 uRes; out vec4 o;
void main(){ vec2 uv = gl_FragCoord.xy / uRes, t = 1.0 / vec2(textureSize(uLow, 0));
  vec3 s = texture(uLow, uv).rgb * 4.0;
  s += (texture(uLow, uv + vec2(t.x, 0.0)).rgb + texture(uLow, uv - vec2(t.x, 0.0)).rgb + texture(uLow, uv + vec2(0.0, t.y)).rgb + texture(uLow, uv - vec2(0.0, t.y)).rgb) * 2.0;
  s += texture(uLow, uv + t).rgb + texture(uLow, uv - t).rgb + texture(uLow, uv + vec2(t.x, -t.y)).rgb + texture(uLow, uv + vec2(-t.x, t.y)).rgb;
  o = vec4(s / 16.0 + texture(uCur, uv).rgb, 1.0); }`;
// 8. to the screen: glow, gain, tone curve
const LK_FINAL = `#version 300 es
precision highp float;
uniform sampler2D uHdr, uBloom; uniform float uGain, uBloomAmt, uHal; uniform int uLook;
in vec2 vUV; out vec4 o;
vec3 agxCurve(vec3 x){ vec3 x2 = x*x, x4 = x2*x2; return 15.5*x4*x2 - 40.14*x4*x + 31.96*x4 - 6.868*x2*x + 0.4298*x2 + 0.1191*x - 0.00232; }
vec3 agx(vec3 c){
  const mat3 m = mat3(0.842479062253094,0.0423282422610123,0.0423756549057051, 0.0784335999999992,0.878468636469772,0.0784336, 0.0792237451477643,0.0791661274605434,0.879142973793104);
  const mat3 mi = mat3(1.19687900512017,-0.0528968517574562,-0.0529716355144438, -0.0980208811401368,1.15190312990417,-0.0980434501171241, -0.0990297440797205,-0.0989611768448433,1.15107367264116);
  const float lo = -12.47393, hi = 4.026069;
  c = m * max(c, vec3(1e-10)); c = clamp(log2(c), lo, hi); c = (c - lo) / (hi - lo); c = agxCurve(c);
  float l = dot(c, vec3(0.2126, 0.7152, 0.0722)); c = l + (c - l) * 1.12;
  return clamp(mi * c, 0.0, 1.0); }
vec3 srgb(vec3 x){ x = clamp(x, 0.0, 1.0); return mix(12.92*x, 1.055*pow(x, vec3(1.0/2.4)) - 0.055, step(vec3(0.0031308), x)); }
void main(){
  vec2 uv = vUV;            // the HDR image's rows run top-down, like vUV
  vec3 c = texture(uHdr, uv).rgb;
  vec3 b = texture(uBloom, uv).rgb;
  c += b * uBloomAmt;
  float hl = dot(b, vec3(0.3, 0.6, 0.1)); c += vec3(1.0, 0.3, 0.1) * max(hl - 0.9, 0.0) * uHal * 0.2;
  c *= uGain;
  o = vec4(uLook == 1 ? agx(c) : srgb(c), 1.0);
}`;

class MFLook {
  constructor(pv) {
    this.pv = pv; const P = (vs, fs) => pv._prog(vs, fs);
    this.pG = P(VS_FULL, LK_GBUF); this.pGI = P(VS_FULL, LK_GI); this.pAt = P(VS_FULL, LK_ATROUS); this.pHaze = P(VS_FULL, LK_HAZE);
    this.pComb = P(VS_FULL, LK_COMBINE); this.pDof = P(VS_FULL, LK_PDOF); this.pComp = P(VS_FULL, LK_COMP);
    this.pDown = P(VS_FULL, LK_DOWN); this.pUp = P(VS_FULL, LK_UP); this.pFinal = P(VS_QUAD, LK_FINAL);
    this.cache = new Map();             // frame -> {plate, dist, bytes, key}: the lit stand-in, kept so playback is free
    this.budget = 420 * 1024 * 1024;
    this.scratch = null;
  }
  _lin(t) { const gl = this.pv.gl; gl.bindTexture(gl.TEXTURE_2D, t); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR); return t; }
  _half(w, h) { const gl = this.pv.gl; return this._lin(this.pv._tex(w, h, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT, null)); }
  _scratch(W, H) {
    const pv = this.pv, gl = pv.gl;
    if (this.scratch && this.scratch.w === W && this.scratch.h === H) return this.scratch;
    if (this.scratch) for (const k in this.scratch) if (this.scratch[k] && this.scratch[k].w) pv._free(this.scratch[k]);
    for (const t of (this.scratch && this.scratch.bloom) || []) pv._free(t.d, t.u);
    const hw = Math.max(1, W >> 1), hh = Math.max(1, H >> 1), f32 = () => pv._tex(W, H, gl.RGBA32F, gl.RGBA, gl.FLOAT, null);
    const s = { w: W, h: H, direct: this._half(W, H), nrm: f32(), alb: this._half(W, H), gi: this._half(hw, hh), gi2: this._half(hw, hh),
                haze: this._half(hw, hh), dof: this._half(W, H), hdr: this._half(W, H), bloom: [] };
    let bw = W, bh = H;
    for (let i = 0; i < 6; i++) { bw = Math.max(1, bw >> 1); bh = Math.max(1, bh >> 1); s.bloom.push({ w: bw, h: bh, d: this._half(bw, bh), u: this._half(bw, bh) }); }
    return (this.scratch = s);
  }
  clear() { for (const e of this.cache.values()) this.pv._free(e.plate, e.dist, e.dof); this.cache.clear(); }
  _camU(prog, c, W, H) {
    const gl = this.pv.gl, th = Math.tan(c.hfov / 2);
    gl.uniform3fv(prog.u.uC, [...c.pos, ...c.right, ...c.up, ...c.back]); gl.uniform1f(prog.u.uTh, th); gl.uniform1f(prog.u.uTv, th * H / W); gl.uniform2f(prog.u.uWH, W, H);
  }
  _lightU(prog, L) {
    const gl = this.pv.gl, az = L.az * Math.PI / 180, el = L.el * Math.PI / 180, I = L.intensity;
    gl.uniform3fv(prog.u.uLs, [-Math.cos(az) * Math.cos(el), Math.sin(el), -Math.sin(az) * Math.cos(el)]);
    gl.uniform3fv(prog.u.uSunP, L.kelvin.map(v => v * I)); gl.uniform3fv(prog.u.uSky, [0.66 * I * 0.45, 0.76 * I * 0.45, 0.95 * I * 0.45]); gl.uniform1f(prog.u.uInt, I);
    this.pv._bind(prog, 7, 'uBoxes', this.pv.boxes.tex); gl.uniform1i(prog.u.uNB, this.pv.boxes.nb);
  }
  // the stand-in, lit, for one frame (cached)
  standin(frame, W, H, L, haze, su, opt) {
    opt = opt || {};
    const key = [W, H, haze, opt.key || ''].join('|');
    const hit = this.cache.get(frame);
    if (hit && hit.key === key) { this.cache.delete(frame); this.cache.set(frame, hit); return hit; }
    if (hit) { this.pv._free(hit.plate, hit.dist, hit.dof); this.cache.delete(frame); }
    const pv = this.pv, gl = pv.gl, s = this._scratch(W, H), c = pv.camAt(frame);
    gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST); gl.disable(gl.SCISSOR_TEST); gl.bindVertexArray(pv.vao);
    const plate = this._half(W, H), dist = pv._tex(W, H, gl.R32F, gl.RED, gl.FLOAT, null);
    let p = this.pG; gl.useProgram(p); this._camU(p, c, W, H); this._lightU(p, L);
    const rm = L.room; gl.uniform1i(p.u.uCut, rm && opt.cut ? 1 : 0);
    if (rm) { gl.uniform3fv(p.u.uRoomLo, rm[0]); gl.uniform3fv(p.u.uRoomHi, rm[1]); }
    pv._target([s.direct, s.nrm, s.alb, dist], W, H); gl.drawArrays(gl.TRIANGLES, 0, 3);
    const hw = s.gi.w, hh = s.gi.h;
    p = this.pGI; gl.useProgram(p); this._camU(p, c, W, H); this._lightU(p, L);
    pv._bind(p, 0, 'uNrm', s.nrm); gl.uniform1i(p.u.uSpp, opt.spp || 24); gl.uniform2f(p.u.uFull, W, H);
    pv._target([s.gi], hw, hh); gl.drawArrays(gl.TRIANGLES, 0, 3);
    p = this.pAt; gl.useProgram(p); let src = s.gi, dst = s.gi2;
    for (const st of [1, 2, 4]) { pv._target([dst], hw, hh); pv._bind(p, 0, 'uTex', src); pv._bind(p, 1, 'uNrm', s.nrm); gl.uniform2f(p.u.uFull, W, H); gl.uniform1i(p.u.uStep, st);
      gl.drawArrays(gl.TRIANGLES, 0, 3); [src, dst] = [dst, src]; }
    p = this.pHaze; gl.useProgram(p); this._camU(p, c, W, H); this._lightU(p, L);
    pv._bind(p, 0, 'uNrm', s.nrm); gl.uniform2f(p.u.uFull, W, H); gl.uniform1f(p.u.uHaze, haze); gl.uniform1f(p.u.uG, 0.55); gl.uniform1f(p.u.uSu, su);
    pv._target([s.haze], hw, hh); gl.drawArrays(gl.TRIANGLES, 0, 3);
    p = this.pComb; gl.useProgram(p);
    pv._bind(p, 0, 'uDirect', s.direct); pv._bind(p, 1, 'uNrm', s.nrm); pv._bind(p, 2, 'uAlb', s.alb); pv._bind(p, 3, 'uGI', src); pv._bind(p, 4, 'uHazeT', s.haze);
    gl.uniform2f(p.u.uFull, W, H); gl.uniform1f(p.u.uGIAmt, 1.0);
    pv._target([plate], W, H); gl.drawArrays(gl.TRIANGLES, 0, 3);
    const e = { plate, dist, key, bytes: W * H * 20 };
    this.cache.set(frame, e);
    let total = 0; for (const v of this.cache.values()) total += v.bytes;
    for (const [f, v] of this.cache) { if (total <= this.budget || v === e) break; pv._free(v.plate, v.dist, v.dof); this.cache.delete(f); total -= v.bytes; }
    return e;
  }
  // everything to the screen. o: {plate, dist, defocusPlate, lens:{focal, film, fstop, focusM, su, front, back, dof, maxR}, dust:{tex, flip},
  //   view, set, skyMul, gain, glow, look, rect, W, H}
  show(o) {
    const pv = this.pv, gl = pv.gl, W = o.W, H = o.H, s = this._scratch(W, H);
    gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST); gl.disable(gl.SCISSOR_TEST); gl.bindVertexArray(pv.vao);
    let plate = o.plate;
    if (plate && (o.defocusPlate || o.skyMul !== 1)) {
      const L = o.lens, ent = o.cacheEntry;
      const dk = [W, H, L.focal, L.film, L.fstop, L.focusM, L.front, L.back, L.dof, L.maxR, o.skyMul, o.defocusPlate].join('|');
      if (ent && ent.dof && ent.dofKey === dk) plate = ent.dof;
      else {
        // the lens blur of the set: 4 passes of 96 samples, averaged (additively), so it comes out smooth; kept per frame
        const out = ent ? (ent.dof || (ent.dof = this._half(W, H))) : s.dof;
        const p = this.pDof; gl.useProgram(p);
        pv._bind(p, 0, 'uPlate', o.plate); pv._bind(p, 1, 'uDist', o.dist || pv.dummy);
        gl.uniform2f(p.u.uWH, W, H); gl.uniform1f(p.u.uFocal, L.focal); gl.uniform1f(p.u.uFilm, L.film); gl.uniform1f(p.u.uFstop, L.fstop);
        gl.uniform1f(p.u.uFocusM, L.focusM); gl.uniform1f(p.u.uSu, L.su); gl.uniform1f(p.u.uMaxR, o.defocusPlate ? L.maxR : 0);
        gl.uniform1f(p.u.uFront, L.front); gl.uniform1f(p.u.uBack, L.back); gl.uniform1i(p.u.uDof, L.dof ? 1 : 0); gl.uniform1f(p.u.uSkyMul, o.dist ? o.skyMul : 1);
        const passes = o.defocusPlate && L.dof ? (o.fast ? 1 : 4) : 1;
        gl.uniform1i(p.u.uN, 96);
        pv._target([out], W, H); gl.clearBufferfv(gl.COLOR, 0, [0, 0, 0, 0]);
        gl.enable(gl.BLEND); gl.blendFunc(gl.CONSTANT_ALPHA, gl.ONE); gl.blendColor(0, 0, 0, 1 / passes);
        for (let k = 0; k < passes; k++) { gl.uniform1f(p.u.uRot, k * 1.618); gl.drawArrays(gl.TRIANGLES, 0, 3); }
        gl.disable(gl.BLEND);
        if (ent) ent.dofKey = dk;
        plate = out;
      }
    }
    let p = this.pComp; gl.useProgram(p);
    pv._bind(p, 0, 'uPlate', plate || pv.dummy); pv._bind(p, 1, 'uDust', (o.dust && o.dust.tex) || pv.dummy);
    gl.uniform1i(p.u.uHasPlate, plate ? 1 : 0); gl.uniform1i(p.u.uShowPlate, o.view !== 'dust' ? 1 : 0);
    gl.uniform1i(p.u.uShowDust, o.view !== 'plate' && o.dust && o.dust.tex ? 1 : 0); gl.uniform1i(p.u.uDustFlip, o.dust && o.dust.flip ? 1 : 0);
    gl.uniform1f(p.u.uSet, o.set); gl.uniform2f(p.u.uWH, W, H);
    pv._target([s.hdr], W, H); gl.drawArrays(gl.TRIANGLES, 0, 3);
    // glow
    let prev = s.hdr;
    p = this.pDown; gl.useProgram(p);
    for (const b of s.bloom) { pv._target([b.d], b.w, b.h); pv._bind(p, 0, 'uTex', prev); gl.uniform2f(p.u.uRes, b.w, b.h); gl.drawArrays(gl.TRIANGLES, 0, 3); prev = b.d; }
    p = this.pUp; gl.useProgram(p);
    for (let i = s.bloom.length - 2; i >= 0; i--) { const b = s.bloom[i];
      pv._target([b.u], b.w, b.h); pv._bind(p, 0, 'uLow', i === s.bloom.length - 2 ? s.bloom[i + 1].d : s.bloom[i + 1].u); pv._bind(p, 1, 'uCur', b.d);
      gl.uniform2f(p.u.uRes, b.w, b.h); gl.drawArrays(gl.TRIANGLES, 0, 3); }
    // screen
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, o.cw, o.ch); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    p = this.pFinal; gl.useProgram(p);
    gl.uniform4fv(p.u.uRect, o.rect);
    pv._bind(p, 0, 'uHdr', s.hdr); pv._bind(p, 1, 'uBloom', s.bloom[0].u);
    gl.uniform1f(p.u.uGain, o.gain); gl.uniform1f(p.u.uBloomAmt, o.glow * 0.06 / s.bloom.length * 6); gl.uniform1f(p.u.uHal, o.glow * 0.4); gl.uniform1i(p.u.uLook, o.look === 'filmic' ? 1 : 0);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }
}
window.MFLook = MFLook;
