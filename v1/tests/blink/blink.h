// A small stand-in for Foundry's Blink, so the node's kernels can be compiled and run as plain C++ in the tests.
// It covers only the subset of Blink the kernels use: scalar and vector types, the maths functions, random-access
// images with clamped edges, params and locals, and process(int2 pos). Nuke's y axis points up, as in Blink.
#pragma once
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <algorithm>

struct float2 { float x, y; float2() : x(0), y(0) {} explicit float2(float s) : x(s), y(s) {} float2(float a, float b) : x(a), y(b) {} };
struct float3 { float x, y, z; float3() : x(0), y(0), z(0) {} explicit float3(float s) : x(s), y(s), z(s) {} float3(float a, float b, float c) : x(a), y(b), z(c) {} };
struct float4 { float x, y, z, w; float4() : x(0), y(0), z(0), w(0) {} explicit float4(float s) : x(s), y(s), z(s), w(s) {} float4(float a, float b, float c, float d) : x(a), y(b), z(c), w(d) {}
  float& operator[](int i) { return (&x)[i]; } };
struct int2 { int x, y; int2() : x(0), y(0) {} int2(int a, int b) : x(a), y(b) {} };
struct int4 { int x, y, z, w; int4() : x(0), y(0), z(0), w(0) {} int4(int a, int b, int c, int d) : x(a), y(b), z(c), w(d) {} };

#define VOPS(T) \
  inline T operator+(T a, T b) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] + (&b.x)[i]; return r; } \
  inline T operator-(T a, T b) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] - (&b.x)[i]; return r; } \
  inline T operator*(T a, T b) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] * (&b.x)[i]; return r; } \
  inline T operator/(T a, T b) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] / (&b.x)[i]; return r; } \
  inline T operator*(T a, float s) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] * s; return r; } \
  inline T operator*(float s, T a) { return a * s; } \
  inline T operator/(T a, float s) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] / s; return r; } \
  inline T operator+(T a, float s) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] + s; return r; } \
  inline T operator-(T a, float s) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = (&a.x)[i] - s; return r; } \
  inline T operator-(T a) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) (&r.x)[i] = -(&a.x)[i]; return r; } \
  inline T& operator+=(T& a, T b) { a = a + b; return a; } \
  inline T& operator-=(T& a, T b) { a = a - b; return a; } \
  inline T& operator*=(T& a, float s) { a = a * s; return a; } \
  inline T& operator*=(T& a, T b) { a = a * b; return a; } \
  inline float dot(T a, T b) { float s = 0; for (int i = 0; i < (int)(sizeof(T) / 4); i++) s += (&a.x)[i] * (&b.x)[i]; return s; } \
  inline T min(T a, T b) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) r_set(r, i, std::min((&a.x)[i], (&b.x)[i])); return r; } \
  inline T max(T a, T b) { T r; for (int i = 0; i < (int)(sizeof(T) / 4); i++) r_set(r, i, std::max((&a.x)[i], (&b.x)[i])); return r; }

template <class T> inline void r_set(T& r, int i, float v) { (&r.x)[i] = v; }
VOPS(float2)
VOPS(float3)
VOPS(float4)

inline float3 cross(float3 a, float3 b) { return float3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x); }
inline float min(float a, float b) { return a < b ? a : b; }
inline float max(float a, float b) { return a > b ? a : b; }
inline int min(int a, int b) { return a < b ? a : b; }
inline int max(int a, int b) { return a > b ? a : b; }
inline float clamp(float x, float a, float b) { return x < a ? a : (x > b ? b : x); }
inline int clamp(int x, int a, int b) { return x < a ? a : (x > b ? b : x); }
inline float lerp(float a, float b, float t) { return a + (b - a) * t; }
inline float rsqrt(float x) { return 1.0f / std::sqrt(x); }
inline float sign(float x) { return x > 0 ? 1.0f : (x < 0 ? -1.0f : 0.0f); }
using std::sin; using std::cos; using std::tan; using std::asin; using std::acos; using std::atan; using std::atan2;
using std::exp; using std::log; using std::pow; using std::sqrt; using std::floor; using std::ceil; using std::fabs;
using std::fmod; using std::round;

struct recti { int x1, y1, x2, y2; int width() const { return x2 - x1; } int height() const { return y2 - y1; } };

// every image has four components; kernels hold ImgRef (a pointer plus the current position), so a kernel can be
// copied per thread
struct Img {
  int W = 0, H = 0;
  std::vector<float> data;
  recti bounds{0, 0, 0, 0};
  int kComps = 4;
  int cx = 0, cy = 0;                       // the current position (for point access)
  void alloc(int w, int h) { W = w; H = h; data.assign((size_t)w * h * 4, 0.0f); bounds = recti{0, 0, w, h}; }
  float4& at(int x, int y) {
    x = x < 0 ? 0 : (x >= W ? W - 1 : x);
    y = y < 0 ? 0 : (y >= H ? H - 1 : y);
    return *reinterpret_cast<float4*>(&data[((size_t)y * W + x) * 4]);
  }
  float4& operator()(int x, int y) { return at(x, y); }
  float& operator()(int x, int y, int c) { return at(x, y)[c]; }
  float4& operator()() { return at(cx, cy); }
};

template <class T, class U> inline void defineParam(T& v, const char*, U d) { v = T(d); }
inline void defineParam(float3& v, const char*, float3 d) { v = d; }
inline void defineParam(float4& v, const char*, float4 d) { v = d; }
inline void defineParam(float2& v, const char*, float2 d) { v = d; }

inline bool load_raw(Img& im, const char* path) {
  FILE* f = fopen(path, "rb");
  if (!f) return false;
  int wh[2];
  if (fread(wh, 4, 2, f) != 2) { fclose(f); return false; }
  im.alloc(wh[0], wh[1]);
  size_t n = fread(im.data.data(), 4, im.data.size(), f);
  fclose(f);
  return n == im.data.size();
}

inline bool save_raw(Img& im, const char* path) {
  FILE* f = fopen(path, "wb");
  if (!f) return false;
  int wh[2] = {im.W, im.H};
  fwrite(wh, 4, 2, f);
  fwrite(im.data.data(), 4, im.data.size(), f);
  fclose(f);
  return true;
}

struct ImgRef {
  Img* im = nullptr;
  recti bounds{0, 0, 0, 0};
  int kComps = 4;
  int cx = 0, cy = 0;
  void bind(Img* p) { im = p; bounds = p->bounds; }
  float4& operator()(int x, int y) { return im->at(x, y); }
  float& operator()(int x, int y, int c) { return im->at(x, y)[c]; }
  float4& operator()() { return im->at(cx, cy); }
};

struct KernelBase { void init() {} };
