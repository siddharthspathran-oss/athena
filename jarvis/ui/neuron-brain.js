/*! NeuronBrain - Athena dashboard component. Plain JS + Canvas 2D, no dependencies.
 *
 *   const brain = new NeuronBrain(el, {regions: [{id, label, color}]});
 *   brain.update({regions: {id: {active, firing, sub}}, cards: [{title, worker, status, note}]});
 *   brain.destroy();
 *
 * The container needs a width; give it a height for wide layouts (narrow layouts grow to fit the cards).
 */
(function (global) {
  'use strict';

  var CSS = [
    '.nb-root{position:relative;width:100%;height:100%;min-height:360px;display:flex;flex-direction:column;background:#07060b;color:#ece9f7;overflow:hidden;font:14px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}',
    '.nb-root.nb-narrow{height:auto}',
    '.nb-stage{position:relative;flex:1 1 auto;min-height:320px}',
    '.nb-narrow .nb-stage{flex:none;height:clamp(300px,92vw,430px);min-height:0}',
    '.nb-canvas{position:absolute;inset:0;width:100%;height:100%;display:block}',
    '.nb-labels{position:absolute;inset:0;pointer-events:none;overflow:hidden}',
    '.nb-label{position:absolute;left:0;top:0;white-space:nowrap;padding:3px 8px 4px;border-radius:7px;background:rgba(9,8,15,.78);border:1px solid rgba(var(--c),.3);will-change:transform;line-height:1.2}',
    '.nb-label.nb-on{border-color:rgba(var(--c),.85);box-shadow:0 0 14px rgba(var(--c),.35)}',
    '.nb-name{display:block;font-size:10.5px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:rgb(var(--l))}',
    '.nb-sub{display:block;font-size:11px;color:#bdb8d3}',
    '.nb-on .nb-sub{color:#f1eefb}',
    '.nb-compact .nb-label:not(.nb-on) .nb-sub{display:none}',
    '.nb-lead{position:absolute;inset:0;width:100%;height:100%;overflow:visible}',
    '.nb-cards{z-index:3;margin:0;padding:10px 12px 12px;border:1px solid rgba(255,255,255,.08);border-radius:14px;background:rgba(12,10,20,.74);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}',
    '.nb-wide .nb-cards{position:absolute;left:14px;right:14px;bottom:14px}',
    '.nb-narrow .nb-cards{margin:0 10px 12px}',
    '.nb-ch{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}',
    '.nb-ct{font-size:11px;font-weight:700;letter-spacing:.24em;color:#d4d0e6}',
    '.nb-cc{font-size:11px;color:#a8a2c2}',
    '.nb-grid{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(210px,1fr))}',
    '.nb-wide .nb-grid{grid-template-columns:repeat(3,minmax(0,1fr))}',
    '.nb-empty{font-size:12px;color:#a8a2c2}',
    '.nb-card{--sc:#8fb4ff;position:relative;overflow:hidden;padding:8px 11px 9px;border-radius:10px;border:1px solid rgba(255,255,255,.08);border-left:3px solid var(--sc);background:rgba(255,255,255,.04);animation:nb-in .4s ease-out}',
    '.nb-card.nb-done{--sc:#5fe3a1}',
    '.nb-card.nb-failed{--sc:#ff6b7a;background:rgba(255,107,122,.1)}',
    '.nb-t{font-size:13px;font-weight:600;color:#f4f2fb;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.nb-w{font-size:11px;color:#bdb8d3}',
    '.nb-n{font-size:12px;color:#a8a2c2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.nb-s{margin-top:3px;font-size:11px;font-weight:600;color:var(--sc)}',
    '.nb-p{display:none;position:relative;height:2px;margin-top:6px;border-radius:2px;overflow:hidden;background:rgba(255,255,255,.1)}',
    '.nb-running .nb-p{display:block}',
    '.nb-p::after{content:"";position:absolute;top:0;bottom:0;width:40%;background:linear-gradient(90deg,transparent,#8fb4ff,transparent);animation:nb-sh 1.4s linear infinite}',
    '@keyframes nb-sh{from{left:-40%}to{left:100%}}',
    '@keyframes nb-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}',
    '@media (prefers-reduced-motion:reduce){.nb-card{animation:none}.nb-p::after{animation:none;left:0;width:45%}}'
  ].join('\n');

  var STATUS = {running: 'Running', done: '✓ Done', failed: '✕ Failed'};
  var D = 3.6;            // camera distance (scene units)
  var MAX_CARDS = 6;

  function rng(seed) {
    return function () {
      seed = (seed + 0x6D2B79F5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function el(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }

  // Resolve any CSS colour to [r,g,b] by letting the browser rasterise it.
  var probe;
  function toRGB(c) {
    probe = probe || document.createElement('canvas');
    probe.width = probe.height = 1;
    var g = probe.getContext('2d', {willReadFrequently: true});
    g.fillStyle = '#888';
    g.fillStyle = c;
    g.fillRect(0, 0, 1, 1);
    var d = g.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2]];
  }

  function makeSprite(rgb) {
    var s = 64, cv = document.createElement('canvas');
    cv.width = cv.height = s;
    var g = cv.getContext('2d'), gr = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    var c = rgb.join(',');
    gr.addColorStop(0, 'rgba(255,255,255,1)');
    gr.addColorStop(0.14, 'rgba(255,255,255,.75)');
    gr.addColorStop(0.3, 'rgba(' + c + ',.55)');
    gr.addColorStop(0.65, 'rgba(' + c + ',.12)');
    gr.addColorStop(1, 'rgba(' + c + ',0)');
    g.fillStyle = gr;
    g.fillRect(0, 0, s, s);
    return cv;
  }

  function NeuronBrain(container, opts) {
    var self = this;
    if (!document.getElementById('nb-css')) {
      var st = el('style');
      st.id = 'nb-css';
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    this.host = container;
    this.time = 0;
    this.yaw = 0.6;
    this.sparks = [];
    this.tier = -1;
    this.dead = false;
    this.raf = 0;
    this.cardEls = {};
    this.reduced = false;

    var root = this.root = el('div', 'nb-root nb-wide');
    root.setAttribute('role', 'group');
    root.setAttribute('aria-label', 'Athena neuron brain: activity of each region and parallel jobs');
    var stage = this.stage = el('div', 'nb-stage', root);
    this.canvas = el('canvas', 'nb-canvas', stage);
    this.canvas.setAttribute('aria-hidden', 'true');
    this.ctx = this.canvas.getContext('2d');
    this.labelLayer = el('div', 'nb-labels', stage);
    var NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'nb-lead');
    svg.setAttribute('aria-hidden', 'true');
    this.labelLayer.appendChild(svg);
    var cards = this.cards = el('section', 'nb-cards', root);
    cards.setAttribute('aria-label', 'Parallel minds');
    var ch = el('div', 'nb-ch', cards);
    el('div', 'nb-ct', ch).textContent = 'PARALLEL MINDS';
    this.count = el('div', 'nb-cc', ch);
    this.grid = el('div', 'nb-grid', cards);
    this.grid.setAttribute('role', 'list');
    this.empty = el('div', 'nb-empty', cards);
    this.empty.textContent = 'No parallel jobs right now.';
    container.appendChild(root);

    var list = (opts && opts.regions) || [];
    this.regions = list.map(function (r, i) {
      var rgb = toRGB(r.color), light = rgb.map(function (v) { return Math.round(v + (255 - v) * 0.55); });
      var line = document.createElementNS(NS, 'line');
      line.setAttribute('stroke', 'rgba(' + rgb.join(',') + ',.6)');
      line.setAttribute('stroke-width', '1');
      line.style.display = 'none';
      svg.appendChild(line);
      var lab = el('div', 'nb-label', self.labelLayer);
      lab.style.setProperty('--c', rgb.join(','));
      lab.style.setProperty('--l', light.join(','));
      var nm = el('span', 'nb-name', lab), sb = el('span', 'nb-sub', lab);
      nm.textContent = r.label;
      sb.textContent = 'idle';
      return {id: r.id, label: r.label, rgb: rgb, spr: makeSprite(rgb), lab: lab, sub: sb, w: 0, h: 0, line: line, ld: false,
        act: 0.16, tAct: 0.16, fire: 0, tFire: 0, active: false, acc: 0, i: i};
    });
    this.byId = {};
    this.regions.forEach(function (r) { self.byId[r.id] = r; });

    this.mq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    this.onMotion = function () { self.reduced = !!(self.mq && self.mq.matches); if (self.reduced) self.sparks.length = 0; };
    this.onMotion();
    if (this.mq && this.mq.addEventListener) this.mq.addEventListener('change', this.onMotion);

    this.onVis = function () { self.vis = !document.hidden; self.sync(); };
    this.vis = !document.hidden;
    this.inView = true;
    document.addEventListener('visibilitychange', this.onVis);
    if (window.IntersectionObserver) {
      this.io = new IntersectionObserver(function (e) { self.inView = e[e.length - 1].isIntersecting; self.sync(); });
      this.io.observe(root);
    }
    if (window.ResizeObserver) {
      this.ro = new ResizeObserver(function () { self.resize(); });
      this.ro.observe(root);
    } else {
      this.onWin = function () { self.resize(); };
      window.addEventListener('resize', this.onWin);
    }
    this.tick = function (now) { self.frame(now); };
    this.resize();
    this.renderCards([]);
    this.sync();
  }

  var P = NeuronBrain.prototype;

  P.sync = function () {
    var run = this.vis && this.inView && !this.dead;
    if (run && !this.raf) { this.last = 0; this.raf = requestAnimationFrame(this.tick); }
    else if (!run && this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
  };

  P.resize = function () {
    if (this.dead) return;
    var rw = this.root.clientWidth;
    if (!rw) return;
    this.root.classList.toggle('nb-wide', rw >= 720);
    this.root.classList.toggle('nb-narrow', rw < 720);
    var compact = rw < 600;   // phones: idle tags show the name only
    if (compact !== this.compact) {
      this.compact = compact;
      this.root.classList.toggle('nb-compact', compact);
      this.regions.forEach(function (R) { R.w = 0; });
    }
    var w = this.stage.clientWidth, h = this.stage.clientHeight;
    if (!w || !h) return;
    var dpr = this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = w; this.h = h;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    var reserve = rw >= 720 ? this.cards.offsetHeight + 14 : 0;
    this.cx = w / 2;
    this.cy = (h - reserve) / 2 + 16;
    this.S = Math.max(80, Math.min(w / 2.8, (h - reserve) / 2.8));
    // particle budget by screen size
    var tier = w < 640 ? 0 : w < 1100 ? 1 : 2;
    if (tier !== this.tier) { this.tier = tier; this.build(); }
  };

  P.build = function () {
    var regs = this.regions, n = regs.length, R = rng(7);
    this.sparks.length = 0;
    this.sparkCap = [36, 60, 90][this.tier];
    if (!n) { this.N = 0; return; }
    var per = Math.max(10, Math.round([300, 480, 640][this.tier] / n)), N = this.N = per * n;
    var X = this.X = new Float32Array(N), Y = this.Y = new Float32Array(N), Z = this.Z = new Float32Array(N);
    this.PX = new Float32Array(N); this.PY = new Float32Array(N); this.PK = new Float32Array(N); this.PZ = new Float32Array(N);
    this.ph = new Float32Array(N);
    this.reg = new Uint8Array(N);
    var cr = this.cr = 0.85 / Math.sqrt(n), cen = this.cen = [], i, j, k;
    for (i = 0; i < n; i++) {   // region centres on a brain-shaped ellipsoid shell (fibonacci spiral)
      var y = 1 - (i + 0.5) * 2 / n, rr = Math.sqrt(1 - y * y), th = i * 2.399963 + 0.4;
      cen.push([Math.cos(th) * rr * 1.08, y * 0.72, Math.sin(th) * rr * 0.88]);
    }
    function g() { return (R() + R() + R() - 1.5) * 1.3; }
    for (i = 0; i < N; i++) {
      var r = Math.floor(i / per), c = cen[r];
      this.reg[i] = r;
      X[i] = c[0] + g() * cr; Y[i] = c[1] + g() * cr * 0.8; Z[i] = c[2] + g() * cr;
      this.ph[i] = R() * 6.283;
    }
    var ea = [], eb = [], er = [], seen = {}, adj = this.adj = [];
    for (i = 0; i < N; i++) adj.push([]);
    function add(a, b, r) {
      var key = a < b ? a * N + b : b * N + a;
      if (a === b || seen[key]) return;
      seen[key] = 1;
      adj[a].push(ea.length); adj[b].push(ea.length);
      ea.push(a); eb.push(b); er.push(r);
    }
    function d2(a, b) { var dx = X[a] - X[b], dy = Y[a] - Y[b], dz = Z[a] - Z[b]; return dx * dx + dy * dy + dz * dz; }
    for (var r = 0; r < n; r++) {          // intra-region: each neuron links to its 2 nearest
      for (i = r * per; i < (r + 1) * per; i++) {
        var b1 = -1, b2 = -1, d1 = 1e9, dd2 = 1e9;
        for (j = r * per; j < (r + 1) * per; j++) {
          if (j === i) continue;
          var d = d2(i, j);
          if (d < d1) { b2 = b1; dd2 = d1; b1 = j; d1 = d; } else if (d < dd2) { b2 = j; dd2 = d; }
        }
        add(i, b1, r); if (b2 >= 0) add(i, b2, r);
      }
    }
    this.regEdges = regs.map(function () { return []; });
    for (i = 0; i < ea.length; i++) this.regEdges[er[i]].push(i);
    var firstInter = ea.length, pairs = {};
    for (var a = 0; a < n; a++) {          // inter-region: 6 shortest links to each of the 2 nearest regions
      var order = cen.map(function (c, idx) {
        return {idx: idx, d: Math.pow(c[0] - cen[a][0], 2) + Math.pow(c[1] - cen[a][1], 2) + Math.pow(c[2] - cen[a][2], 2)};
      }).filter(function (o) { return o.idx !== a; }).sort(function (p, q) { return p.d - q.d; }).slice(0, 2);
      for (k = 0; k < order.length; k++) {
        var bR = order[k].idx, pk = a < bR ? a + '-' + bR : bR + '-' + a;
        if (pairs[pk]) continue;
        pairs[pk] = 1;
        var cand = [];
        for (i = a * per; i < (a + 1) * per; i++) {
          var best = -1, bd = 1e9;
          for (j = bR * per; j < (bR + 1) * per; j++) { var dj = d2(i, j); if (dj < bd) { bd = dj; best = j; } }
          cand.push([bd, i, best]);
        }
        cand.sort(function (p, q) { return p[0] - q[0]; });
        for (j = 0; j < 6 && j < cand.length; j++) add(cand[j][1], cand[j][2], a);
      }
    }
    this.ea = Int32Array.from(ea); this.eb = Int32Array.from(eb);
    this.interEdges = regs.map(function () { return []; });
    for (i = firstInter; i < ea.length; i++) this.interEdges[er[i]].push(i);
  };

  P.update = function (state) {
    if (this.dead || !state) return;
    var rs = state.regions;
    if (rs) for (var id in rs) {
      var R = this.byId[id], v = rs[id] || {};
      if (!R) continue;
      var f = clamp(+v.firing || 0, 0, 1);
      R.active = !!v.active;
      R.tFire = R.active ? f : 0;
      R.tAct = R.active ? 0.62 + 0.38 * f : 0.16;
      var txt = v.sub != null ? String(v.sub) : (R.active ? 'firing ' + Math.round(f * 100) + '%' : 'idle');
      if (R.sub.textContent !== txt) { R.sub.textContent = txt; R.w = 0; }
      if (R.lab.classList.contains('nb-on') !== R.active) R.w = 0;   // compact tags change size when (de)activated
      R.lab.classList.toggle('nb-on', R.active);
    }
    if (state.cards) this.renderCards(state.cards);
  };

  P.renderCards = function (cards) {
    var list = cards.slice(0, MAX_CARDS), keep = {}, prev = this.cardEls, next = {}, self = this;
    list.forEach(function (c, i) {
      var status = STATUS[c.status] ? c.status : 'running', key = c.title + '|' + c.worker, e = prev[key];
      if (keep[key]) key += '#' + i;
      keep[key] = 1;
      if (!e) {
        e = el('div', 'nb-card');
        e.setAttribute('role', 'listitem');
        e.t = el('div', 'nb-t', e); e.w = el('div', 'nb-w', e); e.n = el('div', 'nb-n', e);
        e.s = el('div', 'nb-s', e); el('div', 'nb-p', e);
      }
      next[key] = e;
      e.className = 'nb-card nb-' + status;
      if (e.t.textContent !== c.title) e.t.textContent = c.title;
      e.w.textContent = c.worker || '';
      e.n.textContent = c.note || '';
      e.n.style.display = c.note ? '' : 'none';
      e.s.textContent = STATUS[status];
      if (self.grid.children[i] !== e) self.grid.insertBefore(e, self.grid.children[i] || null);
    });
    for (var k in prev) if (!next[k] && prev[k].parentNode) prev[k].parentNode.removeChild(prev[k]);
    this.cardEls = next;
    this.count.textContent = list.length ? list.length + (list.length === 1 ? ' job' : ' jobs') : '';
    this.empty.style.display = list.length ? 'none' : '';
    this.grid.style.display = list.length ? '' : 'none';
    if (this.w) this.resize();
  };

  P.frame = function (now) {
    this.raf = requestAnimationFrame(this.tick);
    var dt = this.last ? Math.min(0.05, (now - this.last) / 1000) : 0.016;
    this.last = now;
    if (!this.w || !this.N) return;
    var rm = this.reduced, regs = this.regions, n = regs.length, N = this.N, i, r, R;
    this.time += dt;
    var t = this.time, k = 1 - Math.exp(-dt * 3);
    for (i = 0; i < n; i++) { R = regs[i]; R.act += (R.tAct - R.act) * k; R.fire += (R.tFire - R.fire) * k; }
    this.yaw += dt * (rm ? 0.025 : 0.13);
    var pitch = 0.24 + (rm ? 0 : 0.05 * Math.sin(t * 0.2)),
      cy_ = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(pitch), sp = Math.sin(pitch),
      S = this.S, cx = this.cx, cy = this.cy, dpr = this.dpr, u = S / 260,
      X = this.X, Y = this.Y, Z = this.Z, PX = this.PX, PY = this.PY, PK = this.PK, PZ = this.PZ;
    for (i = 0; i < N; i++) {
      var x1 = X[i] * cy_ + Z[i] * sy, z1 = -X[i] * sy + Z[i] * cy_,
        y2 = Y[i] * cp - z1 * sp, z2 = Y[i] * sp + z1 * cp, s = D / (D - z2);
      PX[i] = cx + x1 * S * s; PY[i] = cy + y2 * S * s; PK[i] = s; PZ[i] = z2;
    }

    var ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#07060b';
    ctx.fillRect(0, 0, this.w, this.h);
    ctx.globalCompositeOperation = 'lighter';

    // core light, tinted by whichever regions are awake
    var cr_ = 0, cg = 0, cb = 0, wsum = 0;
    for (i = 0; i < n; i++) { R = regs[i]; var wgt = R.act * R.act; cr_ += R.rgb[0] * wgt; cg += R.rgb[1] * wgt; cb += R.rgb[2] * wgt; wsum += wgt; }
    if (wsum) { cr_ = cr_ / wsum | 0; cg = cg / wsum | 0; cb = cb / wsum | 0; }
    var avg = n ? wsum / n : 0, pulse = rm ? 0 : 0.03 * Math.sin(t * 1.3), cc = cr_ + ',' + cg + ',' + cb;
    var gr = ctx.createRadialGradient(cx, cy, 0, cx, cy, S * 0.95), ca = 0.14 + 0.5 * avg + pulse;
    gr.addColorStop(0, 'rgba(' + cc + ',' + ca + ')');
    gr.addColorStop(0.45, 'rgba(' + cc + ',' + ca * 0.25 + ')');
    gr.addColorStop(1, 'rgba(' + cc + ',0)');
    ctx.fillStyle = gr;
    ctx.fillRect(cx - S, cy - S, S * 2, S * 2);
    var g2 = ctx.createRadialGradient(cx, cy, 0, cx, cy, S * 0.16);
    g2.addColorStop(0, 'rgba(255,255,255,' + (0.35 + 0.4 * avg) + ')');
    g2.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g2;
    ctx.fillRect(cx - S * 0.2, cy - S * 0.2, S * 0.4, S * 0.4);

    // filaments: per region, back half dimmer than front half
    var ea = this.ea, eb = this.eb;
    ctx.lineWidth = Math.max(0.6, 0.8 * u);
    ctx.lineCap = 'round';
    for (r = 0; r < n; r++) {
      R = regs[r];
      var rgb = R.rgb.join(','), a0 = 0.06 + 0.3 * R.act + 0.2 * R.fire;
      for (var pass = 0; pass < 2; pass++) {
        for (var q = 0; q < 2; q++) {
          var lst = q ? this.interEdges[r] : this.regEdges[r];
          ctx.strokeStyle = 'rgba(' + rgb + ',' + (a0 * (pass ? 1 : 0.4) * (q ? 0.55 : 1)) + ')';
          ctx.beginPath();
          for (i = 0; i < lst.length; i++) {
            var a = ea[lst[i]], b = eb[lst[i]];
            if (((PZ[a] + PZ[b]) > 0) !== !!pass) continue;
            ctx.moveTo(PX[a], PY[a]); ctx.lineTo(PX[b], PY[b]);
          }
          ctx.stroke();
        }
      }
    }

    // neurons
    var ph = this.ph, reg = this.reg;
    for (i = 0; i < N; i++) {
      R = regs[reg[i]];
      var df = clamp((PZ[i] + 1.1) / 2.2, 0, 1),
        tw = rm ? 1 : 0.78 + 0.22 * Math.sin(t * (0.6 + 2.6 * R.act) + ph[i]),
        sz = (6 + 9 * R.act) * PK[i] * u;
      ctx.globalAlpha = clamp((0.2 + 0.8 * R.act) * (0.3 + 0.7 * df) * tw, 0, 1);
      ctx.drawImage(R.spr, PX[i] - sz / 2, PY[i] - sz / 2, sz, sz);
    }

    // sparks travelling along filaments
    var sparks = this.sparks, adj = this.adj;
    if (!rm) {
      for (r = 0; r < n; r++) {
        R = regs[r];
        if (!R.active || sparks.length >= this.sparkCap) continue;
        R.acc += (1.5 + 14 * R.fire) * R.act * dt;
        while (R.acc >= 1) {
          R.acc -= 1;
          var src = (Math.random() < 0.25 && this.interEdges[r].length) ? this.interEdges[r] : this.regEdges[r];
          if (sparks.length < this.sparkCap) sparks.push({e: src[Math.random() * src.length | 0], t: 0, d: 1, sp: 1.8 + Math.random() * 1.8, r: r, h: 2 + (Math.random() * 3 | 0)});
        }
      }
    }
    for (i = sparks.length - 1; i >= 0; i--) {
      var sk = sparks[i];
      sk.t += dt * sk.sp;
      if (sk.t >= 1) {
        var node = sk.d > 0 ? eb[sk.e] : ea[sk.e], opts = adj[node];
        if (--sk.h > 0 && opts.length > 1 && Math.random() < 0.8) {
          var ne = opts[Math.random() * opts.length | 0];
          if (ne === sk.e) ne = opts[(opts.indexOf(ne) + 1) % opts.length];
          sk.e = ne; sk.d = ea[ne] === node ? 1 : -1; sk.t = 0;
        } else { sparks.splice(i, 1); continue; }
      }
      var from = sk.d > 0 ? ea[sk.e] : eb[sk.e], to = sk.d > 0 ? eb[sk.e] : ea[sk.e], spr = regs[sk.r].spr;
      for (var tr = 0; tr < 3; tr++) {
        var tt = Math.max(0, sk.t - tr * 0.09), sx = PX[from] + (PX[to] - PX[from]) * tt, sy2 = PY[from] + (PY[to] - PY[from]) * tt,
          ss = (tr ? 15 : 22) * u * PK[from];
        ctx.globalAlpha = tr === 0 ? 1 : tr === 1 ? 0.5 : 0.22;
        ctx.drawImage(spr, sx - ss / 2, sy2 - ss / 2, ss, ss);
      }
    }
    ctx.globalAlpha = 1;

    // floating labels follow their region in 3D (crisp HTML)
    var cen = this.cen, w = this.w, items = [];
    for (r = 0; r < n; r++) {
      R = regs[r];
      var c = cen[r], lx = c[0] * cy_ + c[2] * sy, lz = -c[0] * sy + c[2] * cy_,
        ly = c[1] * cp - lz * sp, lzz = c[1] * sp + lz * cp, ls = D / (D - lzz);
      if (!R.w) { R.w = R.lab.offsetWidth || 90; R.h = R.lab.offsetHeight || 34; }
      var rx = cx + lx * S * ls, ry = cy + ly * S * ls,
        px = clamp(rx, R.w / 2 + 4, w - R.w / 2 - 4), py = Math.max(R.h + 4, ry - this.cr * S * ls * 0.9 - 6);
      items[r] = {R: R, px: px, y: py, y0: py, rx: rx, ry: ry, df: clamp((lzz + 1.1) / 2.2, 0, 1)};
    }
    // nudge overlapping tags apart vertically (a few passes, top to bottom)
    var ord = items.slice().sort(function (p, q) { return p.y0 - q.y0; }), m, A, B;
    for (var pass = 0; pass < 3; pass++) {
      for (i = 1; i < n; i++) {
        B = ord[i];
        for (m = 0; m < i; m++) {
          A = ord[m];
          if (Math.abs(A.px - B.px) < (A.R.w + B.R.w) / 2 + 2 && B.y - B.R.h < A.y + 2 && A.y - A.R.h < B.y) B.y = A.y + B.R.h + 3;
        }
        if (B.y > this.h - 4) B.y = this.h - 4;
      }
    }
    for (r = 0; r < n; r++) {
      var it = items[r];
      R = it.R;
      R.lab.style.transform = 'translate(' + it.px.toFixed(1) + 'px,' + it.y.toFixed(1) + 'px) translate(-50%,-100%)';
      R.lab.style.opacity = (0.78 + 0.22 * it.df).toFixed(2);
      R.lab.style.zIndex = 1 + Math.round(it.df * 10);
      var moved = Math.abs(it.y - it.y0) > 3;
      if (moved) {   // thin leader line keeps a displaced tag tied to its region
        R.line.setAttribute('x1', it.px.toFixed(1)); R.line.setAttribute('y1', (it.y - it.R.h > it.ry ? it.y - it.R.h : it.y).toFixed(1));
        R.line.setAttribute('x2', it.rx.toFixed(1)); R.line.setAttribute('y2', it.ry.toFixed(1));
      }
      if (moved !== R.ld) { R.ld = moved; R.line.style.display = moved ? '' : 'none'; }
    }
  };

  P.destroy = function () {
    if (this.dead) return;
    this.dead = true;
    this.sync();
    document.removeEventListener('visibilitychange', this.onVis);
    if (this.mq && this.mq.removeEventListener) this.mq.removeEventListener('change', this.onMotion);
    if (this.io) this.io.disconnect();
    if (this.ro) this.ro.disconnect();
    if (this.onWin) window.removeEventListener('resize', this.onWin);
    if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
  };

  global.NeuronBrain = NeuronBrain;
  if (typeof module !== 'undefined' && module.exports) module.exports = NeuronBrain;
})(typeof window !== 'undefined' ? window : this);
