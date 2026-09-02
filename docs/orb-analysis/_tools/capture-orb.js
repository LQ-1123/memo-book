/* capture-orb.js — 计算加载球一个周期的每一帧绘制点
   方法：下方 GEOMETRY 区按 app/static/js/ai-orb.js 逐行静态复制（标注了源码行号），
   公式与常量一字不差；不使用 eval/动态执行。
   周期 = 6 / 1.92 = 3.125 s；导出 188 帧均匀覆盖一个周期（phase = k/188），
   对应每帧 dt = (1/188)/0.32 ≈ 16.6224 ms（60fps 标称）。
   输出：frames.json（480×480 设备像素坐标，cssSize 240） */
"use strict";
const fs = require("fs");
const path = require("path");

const OUT = path.resolve(__dirname, "frames.json");

/* ===== GEOMETRY — 复制自 app/static/js/ai-orb.js（行号对照） ===== */
// L7-9
const TAU = Math.PI * 2, PERIOD = 6, BASE_SPREAD = 0.3, PERSPECTIVE = 3.5, MIN_RADIUS = 0.6, MAX_DOTS = 1024;
const RAD = Math.PI / 180;
const DOT = "#A5B4BF";

// L11-16
function spin(p, yaw, pitch) {
  const ca = Math.cos(yaw), sa = Math.sin(yaw);
  const rx = p[0] * ca - p[2] * sa, rz = p[0] * sa + p[2] * ca;
  const co = Math.cos(pitch), so = Math.sin(pitch);
  return [rx, p[1] * co - rz * so, p[1] * so + rz * co, p[3], p[4], p[5]];
}
// L17-28
function frame(t, P, out) {
  const n = 150, turns = 2 + 6 * (0.5 - 0.5 * Math.cos(TAU * t));
  for (let i = 0; i < n; i += 1) {
    const u = (i / n + t) % 1, th = Math.PI * u, sr = Math.sin(th);
    const az = u * TAU * turns + TAU * t;
    const f = Math.pow(Math.sin(Math.PI * u), 0.45);
    out.push(spin(
      [Math.cos(az) * sr, Math.cos(th), Math.sin(az) * sr, 0.6 + 0.9 * f, f, i % 15 === 0 ? P.acc : P.dot],
      0.3, 0.36
    ));
  }
}
// L29-45
function project(pts, size, P, emit) {
  const c = size / 2, R = size * BASE_SPREAD * P.sp, yaw = P.yw + TAU * P.sn * P.t;
  const list = [];
  for (let i = 0; i < pts.length; i++) {
    const q = spin(pts[i], yaw, P.pc), z = q[2];
    const s = PERSPECTIVE / (PERSPECTIVE - z);
    const f = Math.max(0, Math.min(1, (z + 1.1) / 2.2));
    list.push([
      c + q[0] * R * s, c + q[1] * R * s,
      0.4 + 1.6 * f * s * (q[3] === undefined ? 1 : q[3]),
      (0.07 + 0.93 * Math.pow(f, 1.55)) * (q[4] === undefined ? 1 : q[4]),
      q[5] || P.dot, z
    ]);
  }
  list.sort((a, b) => a[5] - b[5]);
  for (let k = 0; k < list.length; k++) emit(list[k][0], list[k][1], list[k][2], list[k][3], list[k][4]);
}
// L46-54
function autoFit(size, P) {
  const half = size / 2; let ext = 0;
  function emit(x, y, r, a) { if (a <= 0.05 || r <= 0.15) return; ext = Math.max(ext, Math.abs(x - half) + 0.5 * r, Math.abs(y - half) + 0.5 * r); }
  for (let k = 0; k < 20; k++) {
    const probe = { n: 1, sp: P.sp, ds: 1, yw: P.restTurn, sn: P.sn, pc: P.restTilt, t: k / 20, dot: "#fff", acc: "#fff" };
    const out = []; frame(probe.t, probe, out); project(out, size, probe, emit);
  }
  return ext > 1 ? Math.max(0.55, Math.min(1.7, 0.415 * size / ext)) : 1;
}
// L55
function dotScaleFor(size) { return size <= 46 ? 0.4 : 0.4 + ((size - 46) / 144) * 0.6; }
/* ===== GEOMETRY END ===== */

// mount() 的参数构造（L57-71）+ drawFrame 的过滤/缩放（L75-95），去掉 canvas 调用
const CSS_SIZE = 240;
const size = Math.round(CSS_SIZE * 2); // L60: DPR 2 → 480
const P = {
  n: 1, sp: 0.4, ds: dotScaleFor(size) * 1.31,
  yw: 5 * RAD, sn: 3, pc: -45 * RAD,
  restTurn: 5 * RAD, restTilt: -45 * RAD,
  t: 0, dot: DOT, acc: DOT
};
const fit = autoFit(size, P);

const N_FRAMES = 188;
const DT = (1 / N_FRAMES) / 0.32; // phase 步长 1/188 对应的秒数（L78: phase += dt*1.92/PERIOD）

let phase = 0;
const frames = [];
for (let k = 0; k < N_FRAMES; k += 1) {
  const out = []; frame(phase, P, out);
  let drawn = 0; const half = size / 2; const dots = [];
  project(out, size, P, (x, y, r, a, col) => {
    if (drawn >= MAX_DOTS) return;                       // L83
    const rr = r * (0.55 + 0.45 * fit);                  // L84
    if (rr <= 0.05 || a <= 0.004) return;                // L85
    const cx = half + (x - half) * fit, cy = half + (y - half) * fit; // L86
    let dr = rr, da = Math.min(1, a);                    // L87
    if (dr < MIN_RADIUS) { da *= (dr / MIN_RADIUS) * (dr / MIN_RADIUS); dr = MIN_RADIUS; } // L88
    dots.push([+cx.toFixed(3), +cy.toFixed(3), +dr.toFixed(3), +da.toFixed(4)]);
    drawn += 1;                                          // L91
  });
  frames.push({ k, phase: +(k / N_FRAMES).toFixed(6), count: dots.length, dots });
  phase = (phase + DT * 1.92 / PERIOD) % 1;              // L78
}

// 参考数据：turns(t) 呼吸曲线（L18 公式）
const turns = frames.map(f => +(2 + 6 * (0.5 - 0.5 * Math.cos(TAU * f.phase))).toFixed(4));

const result = {
  size, cssSize: CSS_SIZE, frameCount: N_FRAMES, period: 6 / 1.92,
  fit: +fit.toFixed(4), dotScale: +P.ds.toFixed(3), dotColor: DOT,
  params: { spread: P.sp, yaw0: 5, spinPerPeriod: P.sn, pitch: -45, particles: 150, accentEvery: 15 },
  turns, frames
};
fs.writeFileSync(OUT, JSON.stringify(result));
const counts = frames.map(f => f.count);
console.log(`size=${size} fit=${fit.toFixed(4)} frames=${N_FRAMES} dots/frame min=${Math.min(...counts)} max=${Math.max(...counts)} first=${counts[0]} last=${counts[N_FRAMES - 1]}`);
console.log(`frames.json -> ${OUT} (${(fs.statSync(OUT).size / 1e6).toFixed(2)} MB)`);
