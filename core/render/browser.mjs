// Launch arguments for headless Chrome/Edge/Chromium.
// Default (WebGL / 2D): uses Playwright's matched chromium-headless-shell unless PLAYWRIGHT_CHROME is set.
// WebGPU (`--webgpu` or `LEMO_WEBGPU=1`): defaults to full Chrome (`chrome`) or Edge (`msedge`) via `LEMO_BROWSER=auto` because
// chromium-headless-shell lacks DXIL / native WebGPU device creation on some systems (falling back to chromium if neither is installed).
// GPU backend: on macOS, Metal explicitly; other systems pass no --use-angle unless LEMO_ANGLE=metal|gl|vulkan|d3d12|swiftshader… is set.
export const EXE = process.env.PLAYWRIGHT_CHROME || undefined;   // undefined = let playwright find it
const angle = process.env.LEMO_ANGLE || (process.platform === 'darwin' ? 'metal' : 'default');
export const WEBGPU_ENV = /^(1|true|yes|on)$/i.test(process.env.LEMO_WEBGPU || '');
export const BROWSER_PREF = (process.env.LEMO_BROWSER || 'auto').toLowerCase();

export function buildLaunchArgs({ webgpu = WEBGPU_ENV } = {}) {
  return [
    ...(angle === 'default' ? [] : [`--use-angle=${angle}`]),
    '--enable-gpu',
    '--ignore-gpu-blocklist',
    ...(webgpu ? ['--enable-unsafe-webgpu'] : []),
    '--font-render-hinting=none',
    '--force-color-profile=srgb',
  ];
}
export const ARGS = buildLaunchArgs();

// Launch Chromium with optional WebGPU channel selection. Preserves `chromium` (headless-shell) for non-WebGPU renders when LEMO_BROWSER=auto.
export async function launchBrowser(chromium, { webgpu = WEBGPU_ENV } = {}) {
  const args = buildLaunchArgs({ webgpu });
  if (EXE) return chromium.launch({ executablePath: EXE, args });

  if (BROWSER_PREF === 'chromium') return chromium.launch({ args });
  if (BROWSER_PREF === 'chrome' || BROWSER_PREF === 'msedge') {
    return chromium.launch({ channel: BROWSER_PREF, args });
  }
  if (BROWSER_PREF !== 'auto') {
    throw new Error(`unknown LEMO_BROWSER "${BROWSER_PREF}" (use auto, chrome, msedge, or chromium)`);
  }

  if (!webgpu) return chromium.launch({ args });

  const channels = process.platform === 'win32'
    ? ['chrome', 'msedge', undefined]
    : ['chrome', 'msedge', undefined];
  let lastErr = null;
  for (const channel of channels) {
    try {
      return await chromium.launch({ ...(channel ? { channel } : {}), args });
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

// A lost WebGL context leaves the page alive but no longer drawing: the frames come out blank and the render would still
// exit 0. Two causes: a virtualised GPU (most cloud VMs and CI runners: Chrome creates the context and drops it right away),
// or a page with more WebGL canvases than Chrome keeps (16; it drops the oldest). Caught on the way down (capture phase:
// webglcontextlost does not bubble; a canvas outside the document, like the probe below, never reaches window). Not counted:
// a context the page lets go itself (WEBGL_lose_context.loseContext(), which three.js forceContextLoss() calls) and one that
// comes back (webglcontextrestored). window.__glLost names a canvas still lost; page.mjs checks it before the first frame,
// still.mjs after each still and video.mjs while it renders.
export const GL_LOST_HELP = 'every frame from then on would come out blank. Either the page has more WebGL canvases than Chrome keeps (16, it drops the oldest),\n' +
  '  or the GPU dropped it: on a virtualised GPU (most cloud VMs and CI runners) Chrome starts WebGL on it and then drops it; run with\n' +
  '  LEMO_ANGLE=swiftshader (software rendering, slower; see core/README.md).';
export const WATCH_GL_LOST = `(() => {
  const own = new WeakSet(), wrapped = new WeakSet(), lost = new Set(), name = c => c && c.id ? 'canvas#' + c.id : 'canvas';
  for (const C of [self.WebGLRenderingContext, self.WebGL2RenderingContext]) if (C) {
    const get = C.prototype.getExtension;
    C.prototype.getExtension = function (n) {
      const x = get.apply(this, arguments), cv = this.canvas;
      if (x && /^webgl_lose_context$/i.test(n) && !wrapped.has(x)) {
        wrapped.add(x); const lose = x.loseContext;
        x.loseContext = function () { own.add(cv); return lose.apply(this, arguments); };
      }
      return x;
    };
  }
  addEventListener('webglcontextlost', e => { if (own.has(e.target)) return; lost.add(e.target); window.__glLost = name(e.target); }, true);
  addEventListener('webglcontextrestored', e => { own.delete(e.target); lost.delete(e.target); window.__glLost = lost.size ? name([...lost][0]) : null; }, true);
})();`;
export const glLost = page => page.evaluate(() => window.__glLost || null);

// warn once if WebGL ended up on software rendering (2D-canvas demos are unaffected; never fails the render)
let warned = false;
export async function warnIfSoftwareGL(page) {
  if (warned) return; warned = true;
  try {
    const r = await page.evaluate(() => {
      const c = document.createElement('canvas'), g = c.getContext('webgl2') || c.getContext('webgl'); if (!g) return null;
      const e = g.getExtension('WEBGL_debug_renderer_info'), name = e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : '';
      g.getExtension('WEBGL_lose_context')?.loseContext(); return String(name);
    });
    if (r && /swiftshader|llvmpipe|software/i.test(r)) console.error(`[note] WebGL runs on software rendering (${r.slice(0, 80)}): 3D/CRT demos will be slow. macOS: LEMO_ANGLE=metal; Linux: needs GPU drivers. See core/README.md.`);
  } catch {}
}

// Track unexpected WebGPU device losses (ignores explicit device.destroy() where info.reason === 'destroyed').
export const WATCH_GPU_LOST = `(() => {
  const Proto = self.GPUAdapter && self.GPUAdapter.prototype;
  if (!Proto || Proto.__lemoWrapped) return;
  Proto.__lemoWrapped = true;
  const req = Proto.requestDevice;
  Proto.requestDevice = async function () {
    const dev = await req.apply(this, arguments);
    if (dev && dev.lost) {
      dev.lost.then(info => {
        if (!info || info.reason === 'destroyed') return;
        window.__gpuLost = info.message || info.reason || 'device lost';
      }).catch(() => {});
    }
    return dev;
  };
})();`;
export const gpuLost = page => page.evaluate(() => window.__gpuLost || null);
export const GPU_LOST_HELP = 'the WebGPU device was lost during rendering. Try --workers 1, updating GPU drivers, or setting LEMO_BROWSER=chrome|msedge (see core/README.md).';

let warnedGPU = false;
export async function warnIfNoWebGPU(page) {
  if (warnedGPU) return; warnedGPU = true;
  try {
    const info = await page.evaluate(async () => {
      if (!navigator.gpu) return { ok: false, reason: 'navigator.gpu missing' };
      const adapter = await navigator.gpu.requestAdapter();
      if (!adapter) return { ok: false, reason: 'requestAdapter() returned null' };
      const fallback = Boolean(adapter.isFallbackAdapter ?? adapter.info?.isFallbackAdapter);
      const desc = adapter.info ? [adapter.info.vendor, adapter.info.architecture, adapter.info.device].filter(Boolean).join(' ') : '';
      return { ok: true, fallback, desc };
    });
    if (!info.ok) {
      console.error(`[note] WebGPU unavailable (${info.reason}): run with Chrome/Edge via LEMO_BROWSER=chrome|msedge or PLAYWRIGHT_CHROME=/path/to/chrome (see core/README.md).`);
    } else if (info.fallback) {
      console.error(`[note] WebGPU is using a fallback/software adapter${info.desc ? ` (${info.desc})` : ''}: rendering may be slow. Try LEMO_BROWSER=chrome|msedge.`);
    }
  } catch {}
}
