(() => {
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {};
  probes.createRenderingCollectors = helpers => {
    const {finiteObject, withTimeout} = helpers;
  const geometryProbe = async hashString => {
    const host = document.createElement("div");
    host.style.cssText = "position:absolute;left:-10000px;top:-10000px;width:400px;contain:layout style paint;";
    host.innerHTML = `<div id="parity-geometry" style="font:13.37px Arial;transform:scale(1.003);letter-spacing:.17px;width:233.3px"><span>mmmmmmmmmmlli🙂ffi</span><input type="range"><select><option>parity</option></select><svg width="91.7" height="23.3"><text x=".3" y="17.2">glyph</text></svg></div>`;
    document.body.appendChild(host);
    try {
      const root = host.querySelector("#parity-geometry");
      const nodes = [root, ...root.querySelectorAll("span,input,select,svg,text")];
      const rects = nodes.map(node => {
        const rect = node.getBoundingClientRect();
        return {
          tag: node.tagName,
          x: rect.x, y: rect.y, width: rect.width, height: rect.height,
          clientWidth: node.clientWidth ?? null, clientHeight: node.clientHeight ?? null,
          offsetWidth: node.offsetWidth ?? null, offsetHeight: node.offsetHeight ?? null
        };
      });
      const ranges = [];
      const span = root.querySelector("span");
      if (span && span.firstChild) {
        for (let index = 0; index < span.firstChild.length; index += 2) {
          const range = document.createRange();
          range.setStart(span.firstChild, index);
          range.setEnd(span.firstChild, Math.min(index + 1, span.firstChild.length));
          const rect = range.getBoundingClientRect();
          ranges.push([rect.x, rect.y, rect.width, rect.height]);
        }
      }
      return {supported: true, rects, rangeRects: ranges, digest: await hashString(JSON.stringify({rects, ranges}))};
    } finally {
      host.remove();
    }
  };

  const webGpuProbe = async hashString => {
    if (!navigator.gpu) return {supported: false};
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return {supported: true, adapter: false};
    const features = Array.from(adapter.features || []).sort();
    const limits = {};
    for (const name in adapter.limits) {
      const value = adapter.limits[name];
      if (typeof value === "number" || typeof value === "string") limits[name] = value;
    }
    let info = null;
    try {
      if (adapter.info) info = finiteObject({
        vendor: adapter.info.vendor || "", architecture: adapter.info.architecture || "",
        device: adapter.info.device || "", description: adapter.info.description || ""
      });
      else if (typeof adapter.requestAdapterInfo === "function") info = finiteObject(await adapter.requestAdapterInfo());
    } catch (_) {}
    const shape = {features, limits, info, preferredCanvasFormat: navigator.gpu.getPreferredCanvasFormat()};
    let workload = {supported: false};
    let device = null;
    try {
      device = await adapter.requestDevice();
      const input = new Float32Array([0.125, -1.5, 3.25, 7.75, 11.5, -0.25, 2.0, 9.0]);
      const inputBuffer = device.createBuffer({
        size: input.byteLength,
        usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST
      });
      const outputBuffer = device.createBuffer({
        size: input.byteLength,
        usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC
      });
      const readBuffer = device.createBuffer({
        size: input.byteLength,
        usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ
      });
      device.queue.writeBuffer(inputBuffer, 0, input);
      const shader = device.createShaderModule({code: `
        @group(0) @binding(0) var<storage, read> inputData: array<f32>;
        @group(0) @binding(1) var<storage, read_write> outputData: array<f32>;
        @compute @workgroup_size(4)
        fn main(@builtin(global_invocation_id) id: vec3<u32>) {
          let i = id.x;
          if (i < 8u) {
            let x = inputData[i];
            outputData[i] = fma(sin(x), cos(x * 0.5), sqrt(abs(x) + 1.0));
          }
        }
      `});
      const pipeline = device.createComputePipeline({layout: "auto", compute: {module: shader, entryPoint: "main"}});
      const bindGroup = device.createBindGroup({
        layout: pipeline.getBindGroupLayout(0),
        entries: [
          {binding: 0, resource: {buffer: inputBuffer}},
          {binding: 1, resource: {buffer: outputBuffer}}
        ]
      });
      const encoder = device.createCommandEncoder();
      const pass = encoder.beginComputePass();
      pass.setPipeline(pipeline);
      pass.setBindGroup(0, bindGroup);
      pass.dispatchWorkgroups(2);
      pass.end();
      encoder.copyBufferToBuffer(outputBuffer, 0, readBuffer, 0, input.byteLength);
      device.queue.submit([encoder.finish()]);
      await readBuffer.mapAsync(GPUMapMode.READ);
      const bytes = new Uint8Array(readBuffer.getMappedRange().slice(0));
      const values = Array.from(new Float32Array(bytes.buffer)).map(value => Number(value.toPrecision(9)));
      workload = {
        supported: true,
        values,
        digest: await hashString(Array.from(bytes).join(","))
      };
      readBuffer.unmap();
      inputBuffer.destroy();
      outputBuffer.destroy();
      readBuffer.destroy();
    } catch (error) {
      workload = {supported: false, error: `${error && error.name || "Error"}: ${error && error.message || String(error)}`};
    } finally {
      if (device && typeof device.destroy === "function") device.destroy();
    }
    return {
      ...shape,
      workload,
      supported: true,
      adapter: true,
      digest: await hashString(JSON.stringify({shape, workload}))
    };
  };

  const mediaProbe = async () => {
    const video = document.createElement("video");
    const audio = document.createElement("audio");
    const codecs = [
      ["video/mp4; codecs=avc1.42E01E", video], ["video/mp4; codecs=hvc1", video],
      ["video/webm; codecs=vp8", video], ["video/webm; codecs=vp9", video],
      ["video/webm; codecs=av01.0.05M.08", video], ["audio/mp4; codecs=mp4a.40.2", audio],
      ["audio/webm; codecs=opus", audio], ["audio/ogg; codecs=vorbis", audio],
      ["audio/flac", audio], ["audio/mpeg", audio]
    ];
    const canPlayType = Object.fromEntries(codecs.map(([type, element]) => [type, element.canPlayType(type)]));
    const decoding = {};
    if (navigator.mediaCapabilities) {
      for (const [type] of codecs.filter(([item]) => item.startsWith("video/"))) {
        try {
          decoding[type] = await navigator.mediaCapabilities.decodingInfo({
            type: "file",
            video: {contentType: type, width: 640, height: 360, bitrate: 800000, framerate: 30}
          });
        } catch (error) { decoding[type] = {error: String(error)}; }
      }
    }
    return {
      canPlayType, decoding,
      mediaCapabilities: Boolean(navigator.mediaCapabilities),
      videoDecoder: typeof VideoDecoder === "function",
      audioDecoder: typeof AudioDecoder === "function",
      mediaSource: typeof MediaSource === "function"
    };
  };

  const fontRenderingProbe = async hashString => {
    const canvas = document.createElement("canvas");
    canvas.width = 720;
    canvas.height = 180;
    const context = canvas.getContext("2d");
    if (!context) return {supported: false};
    const samples = [
      "Sphinx of black quartz, judge my vow 0123456789",
      "Αλφάβητο Кириллица العربية देवनागरी",
      "漢字かな한글 🙂👩🏽‍💻 ffi AV Wa"
    ];
    const families = [
      "Arial", "Times New Roman", "Courier New", "Segoe UI", "Noto Sans",
      "system-ui", "serif", "sans-serif", "monospace"
    ];
    const metrics = {};
    context.textBaseline = "alphabetic";
    context.fillStyle = "#123456";
    context.fillRect(0, 0, canvas.width, canvas.height);
    for (const [familyIndex, family] of families.entries()) {
      context.font = `37.25px ${JSON.stringify(family)}, sans-serif`;
      metrics[family] = samples.map((sample, sampleIndex) => {
        const measured = context.measureText(sample);
        context.fillStyle = `rgb(${40 + familyIndex * 17},${80 + sampleIndex * 45},${190 - familyIndex * 11})`;
        context.fillText(sample, 4, 42 + sampleIndex * 52);
        return finiteObject({
          width: measured.width,
          actualBoundingBoxAscent: measured.actualBoundingBoxAscent,
          actualBoundingBoxDescent: measured.actualBoundingBoxDescent,
          actualBoundingBoxLeft: measured.actualBoundingBoxLeft,
          actualBoundingBoxRight: measured.actualBoundingBoxRight,
          fontBoundingBoxAscent: measured.fontBoundingBoxAscent,
          fontBoundingBoxDescent: measured.fontBoundingBoxDescent
        });
      });
    }
    const availability = Object.fromEntries(
      families.map(family => [family, document.fonts ? document.fonts.check(`16px ${JSON.stringify(family)}`) : null])
    );
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const sampledPixels = Array.from(pixels.filter((_, index) => index % 17 === 0));
    return {
      supported: true,
      availability,
      metrics,
      variableFonts: typeof CSS !== "undefined" && CSS.supports("font-variation-settings", '"wght" 500'),
      metricsDigest: await hashString(JSON.stringify(metrics)),
      rasterDigest: await hashString(sampledPixels.join(","))
    };
  };

    return {geometryProbe, webGpuProbe, mediaProbe, fontRenderingProbe};
  };
})();
