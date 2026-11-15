// WebGPU helpers for Lemo Opuscar film pages (served at /core/webgpu/index.mjs).
// Supports raw WGSL render/compute passes as well as Three.js WebGPURenderer (with optional WebGL fallback).

export const FULLSCREEN_VERT_WGSL = /* wgsl */ `
struct FullscreenOut {
  @builtin(position) pos: vec4f,
  @location(0) uv: vec2f,
};

@vertex
fn vs_main(@builtin(vertex_index) vid: u32) -> FullscreenOut {
  let x = f32(i32(vid & 1u) * 4 - 1);
  let y = f32(i32(vid >> 1u) * 4 - 1);
  var out: FullscreenOut;
  out.pos = vec4f(x, y, 0.0, 1.0);
  out.uv = vec2f((x + 1.0) * 0.5, (1.0 - y) * 0.5);
  return out;
}
`;

/** Return true when navigator.gpu is exposed in the current browser context. */
export function hasWebGPU() {
  return typeof navigator !== 'undefined' && Boolean(navigator.gpu);
}

/** Initialize a WebGPU adapter, device, and configured canvas context. */
export async function initWebGPU(
  canvas,
  { powerPreference, alphaMode = 'premultiplied', format, requiredFeatures, requiredLimits } = {},
) {
  if (!hasWebGPU()) {
    throw new Error('WebGPU is not available in this browser (launch with --webgpu or LEMO_BROWSER=chrome|msedge).');
  }
  const adapter = await navigator.gpu.requestAdapter(powerPreference ? { powerPreference } : undefined);
  if (!adapter) {
    throw new Error('navigator.gpu.requestAdapter() returned null (no compatible GPU adapter).');
  }
  const deviceDesc = {};
  if (requiredFeatures) deviceDesc.requiredFeatures = requiredFeatures;
  if (requiredLimits) deviceDesc.requiredLimits = requiredLimits;
  const device = await adapter.requestDevice(deviceDesc);

  const preferredFormat = navigator.gpu.getPreferredCanvasFormat();
  const chosenFormat = format || preferredFormat;
  const context = canvas ? canvas.getContext('webgpu') : null;
  if (canvas && !context) {
    throw new Error('canvas.getContext("webgpu") returned null.');
  }
  if (context) {
    context.configure({ device, format: chosenFormat, alphaMode });
  }
  return { gpu: navigator.gpu, adapter, device, context, format: chosenFormat, preferredFormat };
}

/** Compile WGSL source and throw with line/column diagnostics on any compilation error. */
export async function compileWGSL(device, code, label = 'shader.wgsl') {
  const module = device.createShaderModule({ label, code });
  if (typeof module.getCompilationInfo === 'function') {
    const info = await module.getCompilationInfo();
    const errors = info.messages.filter((m) => m.type === 'error');
    if (errors.length > 0) {
      const detail = errors.map((e) => `${label}:${e.lineNum}:${e.linePos} ${e.message}`).join('\n');
      throw new Error(`WGSL compile error:\n${detail}`);
    }
  }
  return module;
}

/** Fetch a .wgsl shader URL and compile it with line diagnostics. */
export async function loadWGSL(device, url, label = url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch WGSL shader ${url}: HTTP ${res.status}`);
  }
  return compileWGSL(device, await res.text(), label);
}

/** Create a fullscreen triangle render pass pipeline with an optional auto-bound uniform buffer at @group(0) @binding(0). */
export async function createFullscreenPass(
  device,
  {
    code,
    format = navigator.gpu.getPreferredCanvasFormat(),
    vertexEntry = 'vs_main',
    fragmentEntry = 'fs_main',
    uniformSize = 0,
    layout = 'auto',
    blend,
    label = 'fullscreen-pass',
  },
) {
  const module = await compileWGSL(device, code, label);
  const pipeline = await device.createRenderPipelineAsync({
    label,
    layout,
    vertex: { module, entryPoint: vertexEntry },
    fragment: {
      module,
      entryPoint: fragmentEntry,
      targets: [{ format, ...(blend ? { blend } : {}) }],
    },
    primitive: { topology: 'triangle-list', cullMode: 'none' },
  });

  let uniformBuffer = null;
  let bindGroup = null;
  if (uniformSize > 0) {
    const aligned = Math.max(16, Math.ceil(uniformSize / 16) * 16);
    uniformBuffer = device.createBuffer({
      label: `${label}:uniforms`,
      size: aligned,
      usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
    });
    bindGroup = device.createBindGroup({
      label: `${label}:bg0`,
      layout: pipeline.getBindGroupLayout(0),
      entries: [{ binding: 0, resource: { buffer: uniformBuffer } }],
    });
  }

  function draw(
    encoder,
    view,
    { uniformData, clearColor = { r: 0, g: 0, b: 0, a: 1 }, loadOp = 'clear', storeOp = 'store', bindGroups } = {},
  ) {
    if (uniformData && uniformBuffer) {
      device.queue.writeBuffer(uniformBuffer, 0, uniformData);
    }
    const pass = encoder.beginRenderPass({
      label: `${label}:pass`,
      colorAttachments: [{ view, clearValue: clearColor, loadOp, storeOp }],
    });
    pass.setPipeline(pipeline);
    if (bindGroups) {
      bindGroups.forEach((bg, idx) => {
        if (bg) pass.setBindGroup(idx, bg);
      });
    } else if (bindGroup) {
      pass.setBindGroup(0, bindGroup);
    }
    pass.draw(3);
    pass.end();
  }

  return { module, pipeline, uniformBuffer, bindGroup, draw };
}

/** Create a compute pipeline from WGSL source and return a concise dispatch helper. */
export async function createComputePass(
  device,
  { code, entryPoint = 'cs_main', layout = 'auto', label = 'compute-pass' },
) {
  const module = await compileWGSL(device, code, label);
  const pipeline = await device.createComputePipelineAsync({
    label,
    layout,
    compute: { module, entryPoint },
  });

  function dispatch(encoder, workgroupsX, workgroupsY = 1, workgroupsZ = 1, bindGroups = []) {
    const pass = encoder.beginComputePass({ label: `${label}:pass` });
    pass.setPipeline(pipeline);
    bindGroups.forEach((bg, idx) => {
      if (bg) pass.setBindGroup(idx, bg);
    });
    pass.dispatchWorkgroups(workgroupsX, workgroupsY, workgroupsZ);
    pass.end();
  }

  return { module, pipeline, dispatch };
}

/** Initialize Three.js WebGPURenderer (r170+) sized at DPR 1 for deterministic frame capture. */
export async function makeThreeRenderer(
  canvas,
  {
    width = canvas?.width || 1920,
    height = canvas?.height || 1080,
    antialias = true,
    forceWebGL = false,
    toneMapping,
    toneMappingExposure,
  } = {},
) {
  const THREE = await import('/node_modules/three/build/three.webgpu.js');
  const renderer = new THREE.WebGPURenderer({ canvas, antialias, forceWebGL });
  await renderer.init();
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  if (toneMapping !== undefined) renderer.toneMapping = toneMapping;
  if (toneMappingExposure !== undefined) renderer.toneMappingExposure = toneMappingExposure;
  const isWebGPU = Boolean(renderer.backend?.isWebGPUBackend);
  return { THREE, renderer, isWebGPU };
}
