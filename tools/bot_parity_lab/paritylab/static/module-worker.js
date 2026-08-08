const normalize = value => {
  if (value === null || value === undefined) return value ?? null;
  if (ArrayBuffer.isView(value)) return Array.from(value);
  if (Array.isArray(value)) return value.map(normalize);
  if (typeof value === "object") {
    const output = {};
    for (const key of Reflect.ownKeys(value).slice(0, 256)) {
      try { output[String(key)] = normalize(value[key]); }
      catch (error) { output[String(key)] = `[throws:${String(error)}]`; }
    }
    return output;
  }
  if (["string", "number", "boolean"].includes(typeof value)) return value;
  return String(value);
};

const userAgentData = async () => {
  if (!navigator.userAgentData) return null;
  const value = {
    brands: Array.from(navigator.userAgentData.brands || []),
    mobile: navigator.userAgentData.mobile,
    platform: navigator.userAgentData.platform
  };
  if (typeof navigator.userAgentData.getHighEntropyValues === "function") {
    try {
      value.highEntropy = await navigator.userAgentData.getHighEntropyValues([
        "architecture", "bitness", "formFactors", "fullVersionList", "model",
        "platformVersion", "uaFullVersion", "wow64"
      ]);
    } catch (error) { value.highEntropyError = String(error); }
  }
  return value;
};

const offscreen = () => {
  if (typeof OffscreenCanvas !== "function") return {supported: false};
  try {
    const canvas = new OffscreenCanvas(32, 32);
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    if (!gl) return {supported: true, webgl: false};
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return {
      supported: true,
      webgl: true,
      context: typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext ? "webgl2" : "webgl",
      vendor: gl.getParameter(gl.VENDOR),
      renderer: gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      unmaskedVendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null,
      unmaskedRenderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null,
      extensions: (gl.getSupportedExtensions() || []).slice().sort(),
      maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE)
    };
  } catch (error) { return {supported: true, error: String(error)}; }
};

self.onmessage = async event => {
  if (!event.data || event.data.type !== "probe") return;
  let errorStack = "";
  try { throw new Error("module-worker-stack-probe"); }
  catch (error) { errorStack = String(error.stack || error); }
  self.postMessage(normalize({
    runtime: {
      webdriver: navigator.webdriver,
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      vendor: navigator.vendor,
      language: navigator.language,
      languages: Array.from(navigator.languages || []),
      hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemory: navigator.deviceMemory ?? null,
      maxTouchPoints: navigator.maxTouchPoints,
      userAgentData: await userAgentData()
    },
    locale: {
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      resolvedLocale: Intl.DateTimeFormat().resolvedOptions().locale,
      dateOffset: new Date().getTimezoneOffset(),
      dateString: new Date(1722816000000).toString(),
      numberSample: new Intl.NumberFormat().format(1234567.89)
    },
    offscreen: offscreen(),
    codeIntegrity: {
      functionToStringNative: /\{\s*\[native code\]\s*\}/.test(Function.prototype.toString.call(Function.prototype.toString)),
      fetchNative: typeof fetch === "function" ? /\{\s*\[native code\]\s*\}/.test(Function.prototype.toString.call(fetch)) : null,
      errorStack
    },
    capabilities: {
      moduleWorker: true,
      crypto: Boolean(self.crypto),
      indexedDB: Boolean(self.indexedDB),
      caches: Boolean(self.caches),
      fetch: typeof self.fetch,
      crossOriginIsolated: self.crossOriginIsolated
    }
  }));
};
