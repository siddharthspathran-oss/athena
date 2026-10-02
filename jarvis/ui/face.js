/* Athena's face: a WebGL particle sphere that changes with her state, and
   captions that condense out of particle dust word by word.

     const face = new AthenaFace(canvas);   face.setState('listening'); face.setLevel(0.3);
     const caps = new AthenaCaptions(canvas2d); caps.say('Good morning.');

   idle - soft lilac, slow and dim          listening - amber, breathing with the mic
   thinking - violet ribbon knot      speaking  - pink blob pulsing with the voice
   No libraries: plain WebGL1, so it runs offline in the HUD. */
(function () {
  "use strict";

  const STATES = {
    idle:      { c1: [0.66, 0.56, 1.00], c2: [0.86, 0.78, 1.00], morph: 0, amp: 0.05, shimmer: 0.25, glow: 0.50, spin: 0.08, size: 1.0 },
    listening: { c1: [0.97, 0.64, 0.26], c2: [1.00, 0.76, 0.36], morph: 0, amp: 0.07, shimmer: 0.65, glow: 0.95, spin: 0.10, size: 1.0 },
    thinking:  { c1: [0.52, 0.40, 1.00], c2: [0.68, 0.54, 1.00], morph: 1, amp: 0.00, shimmer: 0.20, glow: 1.00, spin: 0.55, size: 0.9 },
    speaking:  { c1: [1.00, 0.36, 0.72], c2: [1.00, 0.52, 0.80], morph: 0, amp: 0.42, shimmer: 0.35, glow: 1.00, spin: 0.16, size: 1.0 },
  };

  // Ashima 3D simplex noise (MIT) - the "fur" and the speaking wobble.
  const NOISE = `
  vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
  vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
  float snoise(vec3 v){
    const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
    vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
    vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
    vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy; i=mod289(i);
    vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
    float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx; vec4 j=p-49.0*floor(p*ns.z*ns.z);
    vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_); vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy;
    vec4 h=1.0-abs(x)-abs(y); vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
    vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
    vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
    vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
    vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
    p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
    vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
    return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
  }`;

  const VERT = `
  precision highp float;
  attribute vec3 aSphere; attribute float aT; attribute vec4 aRand;
  uniform float uTime, uLevel, uMorph, uAmp, uShimmer, uSpin, uSize, uDpr, uAspect, uScale;
  varying float vAlpha; varying float vMix;
  ${NOISE}
  vec3 knot(float t){                       // (2,3) torus knot, ~unit radius
    float r = cos(3.0*t) + 2.0;
    return vec3(r*cos(2.0*t), r*sin(2.0*t), -sin(3.0*t)) / 2.6;
  }
  void main(){
    // sphere shell with a little depth, breathing and wobbling
    float shell = 0.93 + 0.07*aRand.x;
    float wob = snoise(aSphere*1.5 + vec3(0.0, uTime*0.55, uTime*0.3));
    float fur = snoise(aSphere*7.0 + uTime*0.8) * 0.025;
    float r = shell*(1.0 + uAmp*wob*0.32 + uLevel*0.16 + 0.015*sin(uTime*1.3)) + fur;
    vec3 s = aSphere * r;
    // thinking: particles flow along a torus knot, in a loose tube
    float t = aT*6.2831853 + uTime*0.45;
    vec3 k = knot(t);
    vec3 dk = normalize(knot(t+0.01) - k);
    vec3 n1 = normalize(cross(dk, vec3(0.0,1.0,0.0)+0.001)); vec3 n2 = cross(dk, n1);
    float a = aRand.y*6.2831853 + uTime*1.2; float tube = 0.05 + 0.09*aRand.z;
    k += (n1*cos(a) + n2*sin(a)) * tube;
    // fold the shell into a band on the way to the knot, as in the reference
    float band = smoothstep(0.0, 0.5, uMorph) * (1.0 - smoothstep(0.5, 1.0, uMorph));
    s.y *= 1.0 - 0.75*band;
    vec3 p = mix(s, k, smoothstep(0.25, 1.0, uMorph));
    p += (aRand.xyz - 0.5) * uShimmer * 0.05 * sin(uTime*3.0 + aRand.w*40.0);
    // slow turn, slight tilt
    float ang = uTime*uSpin;
    p = vec3(p.x*cos(ang) + p.z*sin(ang), p.y, -p.x*sin(ang) + p.z*cos(ang));
    float tilt = 0.32; p = vec3(p.x, p.y*cos(tilt) - p.z*sin(tilt), p.y*sin(tilt) + p.z*cos(tilt));
    float z = p.z + 3.0;
    vec2 xy = p.xy * (2.6 / z) * uScale;
    gl_Position = vec4(xy.x / uAspect, xy.y, 0.0, 1.0);
    float depth = clamp((3.9 - z) / 1.8, 0.0, 1.0);          // front = 1
    // the silhouette glows (a bubble, not a cloud); the knot keeps an even light
    float rim = 1.0 - abs(p.z) / max(length(p), 0.001);
    float edge = mix(0.7 + 0.8*rim*rim, 1.0, smoothstep(0.25, 1.0, uMorph));
    gl_PointSize = (1.7 + 2.1*aRand.w) * uSize * uDpr * (0.7 + 0.6*depth) * uScale;
    vAlpha = (0.30 + 0.70*depth) * (0.45 + 0.55*aRand.w) * edge;
    vMix = aRand.x;
  }`;

  const FRAG = `
  precision mediump float;
  uniform vec3 uC1, uC2; uniform float uGlow, uDensity;
  varying float vAlpha; varying float vMix;
  void main(){
    float d = length(gl_PointCoord - 0.5);
    float a = smoothstep(0.5, 0.0, d);
    gl_FragColor = vec4(mix(uC1, uC2, vMix) * uGlow, min(1.0, a * vAlpha * 1.35 * uDensity));
  }`;

  class AthenaFace {
    constructor(canvas, opts = {}) {
      this.canvas = canvas;
      const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false, antialias: false });
      if (!gl) throw new Error("WebGL is not available");
      this.gl = gl;
      const count = opts.count || 14000;
      const prog = this._program(VERT, FRAG);
      gl.useProgram(prog);
      this.u = {};
      ["uTime", "uLevel", "uMorph", "uAmp", "uShimmer", "uSpin", "uSize", "uDpr", "uAspect", "uScale", "uC1", "uC2", "uGlow", "uDensity"]
        .forEach((n) => (this.u[n] = gl.getUniformLocation(prog, n)));
      // Fibonacci sphere, knot parameter, randoms
      const sph = new Float32Array(count * 3), kt = new Float32Array(count), rnd = new Float32Array(count * 4);
      const ga = Math.PI * (3 - Math.sqrt(5));
      for (let i = 0; i < count; i++) {
        const y = 1 - (i / (count - 1)) * 2, rr = Math.sqrt(1 - y * y), th = ga * i;
        sph.set([Math.cos(th) * rr, y, Math.sin(th) * rr], i * 3);
        kt[i] = Math.random();
        rnd.set([Math.random(), Math.random(), Math.random(), Math.random()], i * 4);
      }
      const attr = (name, data, size) => {
        const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, name); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
      };
      attr("aSphere", sph, 3); attr("aT", kt, 1); attr("aRand", rnd, 4);
      this.count = count;
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);   // additive glow
      this.cur = Object.assign({}, STATES.idle); this.target = STATES.idle;
      this.state = "idle"; this.level = 0; this.levelS = 0; this.t0 = performance.now(); this.last = this.t0;
      this.speakLevel = null;            // set by setLevel while speaking; else a voice-like envelope
      this.reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      this._frame = this._frame.bind(this);
      if (!opts.manual) requestAnimationFrame(this._frame);   // manual: frames drawn on request (pre-rendering)
    }
    /* Draw the state exactly as it looks at time t, with no easing - used to
       pre-render the desktop overlay's frames (orb_frames.py). */
    renderAt(t) {
      this.cur = JSON.parse(JSON.stringify(this.target));
      this._draw(t, 0);
    }
    _program(vs, fs) {
      const gl = this.gl, mk = (type, src) => {
        const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
        return s;
      };
      const p = gl.createProgram(); gl.attachShader(p, mk(gl.VERTEX_SHADER, vs)); gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs));
      gl.linkProgram(p); if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      return p;
    }
    setState(name) { if (STATES[name]) { this.state = name; this.target = STATES[name]; } }
    setLevel(v) { this.level = Math.max(0, Math.min(1, v || 0)); }
    _frame(now) {
      const dt = Math.min(0.05, (now - this.last) / 1000); this.last = now;
      const t = this.reduced ? (now - this.t0) / 4000 : (now - this.t0) / 1000;
      this._draw(t, dt);
      requestAnimationFrame(this._frame);
    }
    _draw(t, dt) {
      const gl = this.gl, c = this.canvas, dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
      const k = 1 - Math.exp(-dt * 3.2);
      for (const key of ["morph", "amp", "shimmer", "glow", "spin", "size"]) this.cur[key] += (this.target[key] - this.cur[key]) * k;
      for (const key of ["c1", "c2"]) this.cur[key] = this.cur[key].map((v, i) => v + (this.target[key][i] - v) * k);
      // speaking without a real level: a syllable-like envelope
      let lv = this.level;
      if (this.state === "speaking" && lv < 0.02) lv = 0.35 + 0.35 * Math.max(0, Math.sin(t * 9.0) * Math.sin(t * 2.3 + 1));
      this.levelS = dt ? this.levelS + (lv - this.levelS) * (1 - Math.exp(-dt * 12)) : lv;
      gl.viewport(0, 0, w, h); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      const u = this.u, cur = this.cur;
      gl.uniform1f(u.uTime, t); gl.uniform1f(u.uLevel, this.levelS); gl.uniform1f(u.uMorph, cur.morph);
      gl.uniform1f(u.uAmp, cur.amp * (this.state === "speaking" ? 0.6 + this.levelS : 1));
      gl.uniform1f(u.uShimmer, cur.shimmer); gl.uniform1f(u.uSpin, cur.spin); gl.uniform1f(u.uSize, cur.size);
      gl.uniform1f(u.uDpr, dpr); gl.uniform1f(u.uAspect, w / Math.max(1, h)); gl.uniform1f(u.uScale, Math.min(1, w / h) * 0.92);
      // small faces pack the same particles into fewer pixels and wash out to white
      gl.uniform1f(u.uDensity, Math.max(0.5, Math.min(1, Math.min(c.clientWidth, c.clientHeight) / 380)));
      gl.uniform3fv(u.uC1, cur.c1); gl.uniform3fv(u.uC2, cur.c2); gl.uniform1f(u.uGlow, cur.glow);
      gl.drawArrays(gl.POINTS, 0, this.count);
    }
  }

  /* Captions: each word condenses out of a drifting cloud of dust, then
     settles into crisp text. A new sentence blows the old one away. */
  class AthenaCaptions {
    constructor(canvas, opts = {}) {
      this.c = canvas; this.ctx = canvas.getContext("2d");
      this.font = opts.font || "500 22px Figtree, system-ui, sans-serif";
      this.color = opts.color || "rgba(236,232,245,1)";
      this.accent = opts.accent || "#ff5fb8";
      this.words = []; this.dust = []; this.queue = []; this.next = 0;
      this._frame = this._frame.bind(this); requestAnimationFrame(this._frame);
    }
    setAccent(css) { this.accent = css; }
    say(text, msPerWord = 260) {
      for (const p of this.dust) { p.free = true; p.vx = (Math.random() - 0.5) * 3; p.vy = -Math.random() * 2; }
      for (const w of this.words) w.leaving = performance.now();
      this.queue = String(text || "").split(/\s+/).filter(Boolean);
      this.layoutWords = []; this.msPerWord = msPerWord; this.next = performance.now();
      this._layout();
    }
    _layout() {
      const ctx = this.ctx, dpr = Math.min(window.devicePixelRatio || 1, 2), W = this.c.clientWidth;
      ctx.font = this.font; const space = ctx.measureText(" ").width, lh = parseFloat(this.font.match(/(\d+)px/)[1]) * 1.35;
      const maxW = Math.min(W - 32, 560), lines = [[]]; let lw = 0;
      for (const word of this.queue) {
        const ww = ctx.measureText(word).width;
        if (lw + ww > maxW && lines[lines.length - 1].length) { lines.push([]); lw = 0; }
        lines[lines.length - 1].push({ word, ww }); lw += ww + space;
      }
      this.slots = [];
      lines.forEach((ln, li) => {
        const total = ln.reduce((s, x) => s + x.ww, 0) + space * (ln.length - 1);
        let x = (W - total) / 2;
        for (const it of ln) { this.slots.push({ word: it.word, x, y: 8 + lh * (li + 0.8), w: it.ww }); x += it.ww + space; }
      });
    }
    _spawn(slot, now) {
      // sample the word's pixels as targets for the dust
      const off = document.createElement("canvas"), g = off.getContext("2d");
      const h = 40; off.width = Math.ceil(slot.w) + 4; off.height = h;
      g.font = this.font; g.fillStyle = "#fff"; g.textBaseline = "alphabetic"; g.fillText(slot.word, 2, h * 0.72);
      const img = g.getImageData(0, 0, off.width, h).data, pts = [];
      for (let y = 0; y < h; y += 2) for (let x = 0; x < off.width; x += 2) if (img[(y * off.width + x) * 4 + 3] > 128) pts.push([x, y]);
      for (const [x, y] of pts) {
        const tx = slot.x + x - 2, ty = slot.y - h * 0.72 + y;
        this.dust.push({ x: tx + 30 + Math.random() * 60, y: ty + (Math.random() - 0.5) * 40, tx, ty, born: now, free: false, a: 1 });
      }
      this.words.push({ ...slot, born: now, leaving: 0 });
    }
    _frame(now) {
      const c = this.c, ctx = this.ctx, dpr = Math.min(window.devicePixelRatio || 1, 2);
      const W = c.clientWidth, H = c.clientHeight;
      if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
      while (this.slots && this.slots.length && now >= this.next) { this._spawn(this.slots.shift(), now); this.next += this.msPerWord; }
      ctx.fillStyle = this.accent;
      this.dust = this.dust.filter((p) => {
        if (p.free) { p.x += p.vx; p.y += p.vy; p.a -= 0.025; }
        else { const k = Math.min(1, (now - p.born) / 520); const e = 1 - Math.pow(1 - k, 3);
               p.cx = p.x + (p.tx - p.x) * e; p.cy = p.y + (p.ty - p.y) * e; p.a = 1 - Math.max(0, (k - 0.75) * 4); }
        if (p.a <= 0) return false;
        ctx.globalAlpha = p.a * 0.85; ctx.fillRect(p.free ? p.x : p.cx, p.free ? p.y : p.cy, 1.3, 1.3);
        return true;
      });
      ctx.font = this.font; ctx.textBaseline = "alphabetic";
      this.words = this.words.filter((w) => {
        let a = Math.min(1, Math.max(0, (now - w.born - 380) / 260));
        if (w.leaving) a *= 1 - Math.min(1, (now - w.leaving) / 350);
        if (w.leaving && now - w.leaving > 350) return false;
        ctx.globalAlpha = a; ctx.fillStyle = this.color; ctx.fillText(w.word, w.x, w.y);
        return true;
      });
      ctx.globalAlpha = 1;
      requestAnimationFrame(this._frame);
    }
  }

  window.AthenaFace = AthenaFace;
  window.AthenaCaptions = AthenaCaptions;
  window.AthenaFace.STATES = STATES;
})();
