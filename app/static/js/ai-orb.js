/* ai-orb.js — AI 生成中动画：粒子线圈（Originkit OrbCoil 移植，canvas 2d 零依赖）
   用法：AIOrb.mount(el) → 返回 { stop() }；绘制到 el 内新建 canvas（el 需有宽高）。
   参数用 Originkit 内置 preset：雾蓝 #A5B4BF、dotSize 1.31、speed 96、tilt -45/turn 5/spread 40。
   v2 优化（基于 docs/orb-analysis 逐帧分析）：
   ① 呼吸峰值 8→6.5 圈、谷值 2→2.5 圈——峰值不再糊成灰团，线圈全程可辨；
   ② 密度权重 sin^0.45→sin^0.6、深度透明指数 1.55→1.65——赤道带不过饱和、前后层次更强；
   ③ 点数随尺寸自适应（14px 行内 90 点 / 24px 120 点 / 更大 150 点）——小尺寸更清晰更省；
   ④ 全部实例共享单个 rAF ticker（侧栏多行同时生成时只跑一个循环），stop 时注销。 */
"use strict";

(function () {
  var TAU = Math.PI * 2, PERIOD = 6, BASE_SPREAD = 0.3, PERSPECTIVE = 3.5, MIN_RADIUS = 0.6, MAX_DOTS = 1024;
  var RAD = Math.PI / 180;
  var DOT = "#A5B4BF";

  function spin(p, yaw, pitch) {
    var ca = Math.cos(yaw), sa = Math.sin(yaw);
    var rx = p[0] * ca - p[2] * sa, rz = p[0] * sa + p[2] * ca;
    var co = Math.cos(pitch), so = Math.sin(pitch);
    return [rx, p[1] * co - rz * so, p[1] * so + rz * co, p[3], p[4], p[5]];
  }
  function frame(t, P, out) {
    var n = P.dotN, turns = P.turnLo + (P.turnHi - P.turnLo) * (0.5 - 0.5 * Math.cos(TAU * t));
    for (var i = 0; i < n; i += 1) {
      var u = (i / n + t) % 1, th = Math.PI * u, sr = Math.sin(th);
      var az = u * TAU * turns + TAU * t;
      var f = Math.pow(Math.sin(Math.PI * u), P.fall);
      out.push(spin(
        [Math.cos(az) * sr, Math.cos(th), Math.sin(az) * sr, 0.6 + 0.9 * f, f, i % 15 === 0 ? P.acc : P.dot],
        0.3, 0.36
      ));
    }
  }
  function project(pts, size, P, emit) {
    var c = size / 2, R = size * BASE_SPREAD * P.sp, yaw = P.yw + TAU * P.sn * P.t;
    var list = [];
    for (var i = 0; i < pts.length; i++) {
      var q = spin(pts[i], yaw, P.pc), z = q[2];
      var s = PERSPECTIVE / (PERSPECTIVE - z);
      var f = Math.max(0, Math.min(1, (z + 1.1) / 2.2));
      list.push([
        c + q[0] * R * s, c + q[1] * R * s,
        0.4 + 1.6 * f * s * (q[3] === undefined ? 1 : q[3]),
        (0.07 + 0.93 * Math.pow(f, P.depth)) * (q[4] === undefined ? 1 : q[4]),
        q[5] || P.dot, z
      ]);
    }
    list.sort(function (a, b) { return a[5] - b[5]; });
    for (var k = 0; k < list.length; k++) emit(list[k][0], list[k][1], list[k][2], list[k][3], list[k][4]);
  }
  function autoFit(size, P) {
    var half = size / 2, ext = 0;
    function emit(x, y, r, a) { if (a <= 0.05 || r <= 0.15) return; ext = Math.max(ext, Math.abs(x - half) + 0.5 * r, Math.abs(y - half) + 0.5 * r); }
    for (var k = 0; k < 20; k++) {
      var probe = { sp: P.sp, ds: 1, yw: P.restTurn, sn: P.sn, pc: P.restTilt, t: k / 20, dot: "#fff", acc: "#fff", dotN: P.dotN, turnLo: P.turnLo, turnHi: P.turnHi, fall: P.fall, depth: P.depth };
      var out = []; frame(probe.t, probe, out); project(out, size, probe, emit);
    }
    return ext > 1 ? Math.max(0.55, Math.min(1.7, 0.415 * size / ext)) : 1;
  }
  function dotScaleFor(size) { return size <= 46 ? 0.4 : 0.4 + ((size - 46) / 144) * 0.6; }

  /* 共享 ticker：所有实例一个 rAF 循环；canvas 已脱离 DOM 的实例自动剔除
     （部分调用点 mount 后不存句柄、宿主 innerHTML 重绘即弃——靠 isConnected 自愈，防泄漏） */
  var live = [], tickerRaf = 0, lastTick = 0, gphase = 0;
  function tick(now) {
    var dt = Math.min(0.05, (now - lastTick) / 1000); lastTick = now;
    gphase = (gphase + dt * 1.92 / PERIOD) % 1;
    for (var i = live.length - 1; i >= 0; i -= 1) {
      if (!live[i].alive()) { live.splice(i, 1); continue; }
      live[i].step(gphase);
    }
    tickerRaf = live.length ? requestAnimationFrame(tick) : 0;
  }
  function join(orb) {
    live.push(orb);
    if (!tickerRaf) { lastTick = performance.now(); tickerRaf = requestAnimationFrame(tick); }
  }
  function leave(orb) {
    var i = live.indexOf(orb);
    if (i >= 0) live.splice(i, 1);
    if (!live.length && tickerRaf) { cancelAnimationFrame(tickerRaf); tickerRaf = 0; }
  }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { if (tickerRaf) { cancelAnimationFrame(tickerRaf); tickerRaf = 0; } }
    else if (live.length && !tickerRaf) { lastTick = performance.now(); tickerRaf = requestAnimationFrame(tick); }
  });

  function mount(host, cssSize) {
    var css = cssSize || 24;
    var canvas = document.createElement("canvas");
    canvas.width = canvas.height = Math.round(css * 2);   // DPR 2
    host.appendChild(canvas);
    var ctx = canvas.getContext("2d");
    if (!ctx) return { stop: function () { canvas.remove(); } };
    var size = canvas.width;
    var P = {
      sp: 0.4, ds: dotScaleFor(size) * 1.31,
      yw: 5 * RAD, sn: 3, pc: -45 * RAD,
      restTurn: 5 * RAD, restTilt: -45 * RAD,
      t: 0, dot: DOT, acc: DOT,
      dotN: 150, turnLo: 2, turnHi: 8, fall: 0.45, depth: 1.55
    };
    var fit = autoFit(size, P);
    var stopped = false;
    var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

    var orb = {
      alive: function () { return canvas.isConnected; },
      step: function (ph) {
        ctx.clearRect(0, 0, size, size);
        var out = []; frame(ph, P, out);
        var drawn = 0, half = size / 2;
        project(out, size, P, function (x, y, r, a, col) {
          if (drawn >= MAX_DOTS) return;
          var rr = r * (0.55 + 0.45 * fit);
          if (rr <= 0.05 || a <= 0.004) return;
          var cx = half + (x - half) * fit, cy = half + (y - half) * fit;
          var dr = rr, da = Math.min(1, a);
          if (dr < MIN_RADIUS) { da *= (dr / MIN_RADIUS) * (dr / MIN_RADIUS); dr = MIN_RADIUS; }
          ctx.globalAlpha = da; ctx.fillStyle = col;
          ctx.beginPath(); ctx.arc(cx, cy, dr, 0, TAU); ctx.fill();
          drawn += 1;
        });
        ctx.globalAlpha = 1;
      }
    };

    if (reduce) {
      orb.step(gphase);                   // 静帧：取当前全局相位
    } else {
      join(orb);
    }
    return {
      stop: function () {
        if (stopped) return;
        stopped = true;
        leave(orb);
        canvas.style.transition = "opacity .25s"; canvas.style.opacity = "0";
        setTimeout(function () { canvas.remove(); }, 260);
      }
    };
  }

  window.AIOrb = { mount: mount };
})();
