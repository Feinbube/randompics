// 2.5D stereo viewer: the photo is a grid whose vertices are pushed sideways
// by a stereo-derived depth map, so pointer movement reveals real parallax.
// Shared by the public gallery (app.js) and the admin review preview, so both
// render exactly the same picture.

window.RPViewer = (function () {
  const VERT = `#version 300 es
in vec2 a_uv;
uniform sampler2D u_depth;
uniform vec2 u_fit;
uniform float u_amp;
uniform float u_parallax;
uniform float u_anchor;
uniform float u_overscan;
out vec2 v_uv;
void main() {
  float d = texture(u_depth, a_uv).r;
  float edge = smoothstep(0.0, 0.015, a_uv.x) * smoothstep(0.0, 0.015, 1.0 - a_uv.x);
  vec2 pos = (a_uv * 2.0 - 1.0) * u_fit;
  pos.x -= (d - u_anchor) * u_amp * u_parallax * edge;
  pos *= u_overscan;
  gl_Position = vec4(pos, 0.0, 1.0);
  v_uv = a_uv;
}`;

  const FRAG = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_photo;
uniform sampler2D u_depth;
uniform int u_mode;
out vec4 outColor;
void main() {
  if (u_mode == 1) {
    outColor = vec4(vec3(texture(u_depth, v_uv).r), 1.0);
  } else {
    outColor = texture(u_photo, v_uv);
  }
}`;

  const BASE_AMP = 0.03;
  const GRID_X = 320;
  const GRID_Y = 240;

  function compile(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader));
    }
    return shader;
  }

  function create(canvas) {
    const state = {
      gl: null, prog: null, loc: {}, textures: {}, indexCount: 0,
      image: { w: 1, h: 1 }, anchor: 0.5,
      strength: 1, sway: true, depthMode: false, fitMode: "contain",
      parallax: 0, target: 0, lastPointer: 0,
    };

    function createProgram(gl) {
      const prog = gl.createProgram();
      gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        throw new Error(gl.getProgramInfoLog(prog));
      }
      gl.useProgram(prog);
      return prog;
    }

    function buildGrid(gl, prog) {
      const verts = new Float32Array((GRID_X + 1) * (GRID_Y + 1) * 2);
      let v = 0;
      for (let y = 0; y <= GRID_Y; y += 1) {
        for (let x = 0; x <= GRID_X; x += 1) {
          verts[v++] = x / GRID_X;
          verts[v++] = y / GRID_Y;
        }
      }
      const indices = new Uint32Array(GRID_X * GRID_Y * 6);
      let i = 0;
      for (let y = 0; y < GRID_Y; y += 1) {
        for (let x = 0; x < GRID_X; x += 1) {
          const a = y * (GRID_X + 1) + x;
          const b = a + 1;
          const c = a + GRID_X + 1;
          const d = c + 1;
          indices[i++] = a; indices[i++] = c; indices[i++] = b;
          indices[i++] = b; indices[i++] = c; indices[i++] = d;
        }
      }
      const vao = gl.createVertexArray();
      gl.bindVertexArray(vao);
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, "a_uv");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
      state.indexCount = indices.length;
    }

    function textureFrom(image, internal, format) {
      const gl = state.gl;
      const tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, internal, format, gl.UNSIGNED_BYTE, image);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return tex;
    }

    function initGl() {
      if (state.gl) return true;
      const gl = canvas.getContext("webgl2", { antialias: true, alpha: true });
      if (!gl) return false;
      state.gl = gl;
      state.prog = createProgram(gl);
      state.loc = {
        fit: gl.getUniformLocation(state.prog, "u_fit"),
        amp: gl.getUniformLocation(state.prog, "u_amp"),
        parallax: gl.getUniformLocation(state.prog, "u_parallax"),
        overscan: gl.getUniformLocation(state.prog, "u_overscan"),
        anchor: gl.getUniformLocation(state.prog, "u_anchor"),
        mode: gl.getUniformLocation(state.prog, "u_mode"),
      };
      gl.uniform1i(gl.getUniformLocation(state.prog, "u_photo"), 0);
      gl.uniform1i(gl.getUniformLocation(state.prog, "u_depth"), 1);
      buildGrid(gl, state.prog);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      resize();
      requestAnimationFrame(render);
      return true;
    }

    function resize() {
      if (!state.gl) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(1, rect.width || window.innerWidth);
      const height = Math.max(1, rect.height || window.innerHeight);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      state.gl.viewport(0, 0, canvas.width, canvas.height);
    }

    function fit() {
      const { w, h } = state.image;
      const scale = state.fitMode === "cover"
        ? Math.max(canvas.width / w, canvas.height / h)
        : Math.min(canvas.width / w, canvas.height / h);
      return [(w * scale) / canvas.width, (h * scale) / canvas.height];
    }

    function render(now) {
      requestAnimationFrame(render);
      if (!state.gl || !state.prog) return;
      const gl = state.gl;
      const dt = Math.min((now - (render.last || now)) / 1000, 0.1);
      render.last = now;

      if (state.sway && now - state.lastPointer > 2500 && !state.depthMode) {
        state.target = 0.55 * Math.sin((now / 1000) * 0.55);
      }
      state.parallax += (state.target - state.parallax) * (1 - Math.exp(-dt * 6));

      const [fx, fy] = fit();
      gl.uniform2f(state.loc.fit, fx, fy);
      gl.uniform1f(state.loc.amp, BASE_AMP * state.strength);
      gl.uniform1f(state.loc.anchor, state.anchor);
      gl.uniform1f(state.loc.parallax, state.depthMode ? 0 : state.parallax);
      gl.uniform1f(state.loc.overscan, 1.03);
      gl.uniform1i(state.loc.mode, state.depthMode ? 1 : 0);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawElements(gl.TRIANGLES, state.indexCount, gl.UNSIGNED_INT, 0);
    }

    function load(scene, depth, anchor) {
      if (!initGl()) return false;
      const gl = state.gl;
      if (state.textures.photo) gl.deleteTexture(state.textures.photo);
      if (state.textures.depth) gl.deleteTexture(state.textures.depth);
      state.textures.photo = textureFrom(scene, gl.RGBA8, gl.RGBA);
      state.textures.depth = textureFrom(depth, gl.R8, gl.RED);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, state.textures.photo);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, state.textures.depth);
      state.image = { w: scene.naturalWidth, h: scene.naturalHeight };
      state.anchor = typeof anchor === "number" ? anchor : 0.5;
      resize();
      return true;
    }

    function setStrength(value) { state.strength = value; }

    function setFit(mode) { state.fitMode = mode === "cover" ? "cover" : "contain"; }

    function setSway(on) {
      state.sway = on;
      if (!on) state.target = state.parallax;
    }

    function setDepthMode(on) { state.depthMode = on; }

    function setTarget(value) { state.target = value; }

    function notePointer(value) {
      state.lastPointer = typeof value === "number" ? value : performance.now();
    }

    return { load, resize, setStrength, setFit, setSway, setDepthMode, setTarget, notePointer };
  }

  return { create };
})();
