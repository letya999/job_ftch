(() => {
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {};

  probes.createRuntimeCollector = helpers => {
    const {safe, withTimeout, hashBytes, hashString, normalize, captureError, performanceSamples, sid} = helpers;
  const propertyDescriptor = (object, name) => {
    const descriptor = Object.getOwnPropertyDescriptor(object, name);
    if (!descriptor) return null;
    return {
      configurable: descriptor.configurable,
      enumerable: descriptor.enumerable,
      writable: Object.prototype.hasOwnProperty.call(descriptor, "writable") ? descriptor.writable : null,
      hasGetter: typeof descriptor.get === "function",
      hasSetter: typeof descriptor.set === "function",
      getterSource: descriptor.get ? Function.prototype.toString.call(descriptor.get) : null,
      setterSource: descriptor.set ? Function.prototype.toString.call(descriptor.set) : null
    };
  };

  const nativeShape = (fn) => {
    try { return /\{\s*\[native code\]\s*\}/.test(Function.prototype.toString.call(fn)); }
    catch (_) { return false; }
  };

  const probeUserAgentData = async (navigatorRef) => {
    const data = navigatorRef.userAgentData;
    if (!data) return null;
    const basic = {
      brands: Array.from(data.brands || []).map(item => ({brand: item.brand, version: item.version})),
      mobile: Boolean(data.mobile),
      platform: data.platform || ""
    };
    if (typeof data.getHighEntropyValues === "function") {
      const high = await safe("uaData.highEntropy", () => data.getHighEntropyValues([
        "architecture", "bitness", "formFactors", "fullVersionList", "model",
        "platformVersion", "uaFullVersion", "wow64"
      ]), null);
      basic.highEntropy = normalize(high);
    }
    return basic;
  };

  const probePermissions = async (navigatorRef) => {
    const names = [
      "accelerometer", "background-sync", "camera", "clipboard-read", "clipboard-write",
      "geolocation", "gyroscope", "magnetometer", "microphone", "midi", "notifications",
      "payment-handler", "persistent-storage", "push", "speaker-selection"
    ];
    const states = {};
    const errors = {};
    if (!navigatorRef.permissions || typeof navigatorRef.permissions.query !== "function") {
      return {supported: false, states, errors};
    }
    await Promise.all(names.map(async name => {
      try {
        const result = await withTimeout(navigatorRef.permissions.query({name}), 1500, `permission:${name}`);
        states[name] = result.state;
      } catch (error) {
        errors[name] = `${error.name || "Error"}: ${error.message || String(error)}`;
      }
    }));
    return {supported: true, states, errors};
  };

  const probePlugins = (navigatorRef) => {
    const plugins = [];
    const mimeTypes = [];
    try {
      for (const plugin of Array.from(navigatorRef.plugins || [])) {
        plugins.push({
          name: plugin.name,
          filename: plugin.filename,
          description: plugin.description,
          length: plugin.length,
          mimeTypes: Array.from(plugin).map(item => item.type)
        });
      }
    } catch (error) { captureError("plugins", error); }
    try {
      for (const mime of Array.from(navigatorRef.mimeTypes || [])) {
        mimeTypes.push({type: mime.type, suffixes: mime.suffixes, description: mime.description});
      }
    } catch (error) { captureError("mimeTypes", error); }
    return {pluginCount: plugins.length, mimeTypeCount: mimeTypes.length, plugins, mimeTypes};
  };

  const probeChrome = (windowRef) => {
    const chrome = windowRef.chrome;
    if (!chrome) return {exists: false, keys: [], runtimeExists: false, runtimeKeys: []};
    let keys = [];
    let runtimeKeys = [];
    try { keys = Reflect.ownKeys(chrome).map(String).sort(); }
    catch (error) { captureError("chrome.keys", error); }
    try { runtimeKeys = chrome.runtime ? Reflect.ownKeys(chrome.runtime).map(String).sort() : []; }
    catch (error) { captureError("chrome.runtime.keys", error); }
    return {
      exists: true,
      keys,
      runtimeExists: Boolean(chrome.runtime),
      runtimeKeys,
      loadTimesType: typeof chrome.loadTimes,
      csiType: typeof chrome.csi,
      appType: typeof chrome.app
    };
  };

  const probeWebGL = (documentRef = document) => {
    const canvas = documentRef.createElement("canvas");
    canvas.width = 32;
    canvas.height = 32;
    const gl = canvas.getContext("webgl2", {antialias: false}) || canvas.getContext("webgl", {antialias: false});
    if (!gl) return {available: false};
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    const parameterNames = [
      "ALIASED_LINE_WIDTH_RANGE", "ALIASED_POINT_SIZE_RANGE", "ALPHA_BITS", "BLUE_BITS",
      "DEPTH_BITS", "GREEN_BITS", "MAX_COMBINED_TEXTURE_IMAGE_UNITS", "MAX_CUBE_MAP_TEXTURE_SIZE",
      "MAX_FRAGMENT_UNIFORM_VECTORS", "MAX_RENDERBUFFER_SIZE", "MAX_TEXTURE_IMAGE_UNITS",
      "MAX_TEXTURE_SIZE", "MAX_VARYING_VECTORS", "MAX_VERTEX_ATTRIBS",
      "MAX_VERTEX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_UNIFORM_VECTORS", "MAX_VIEWPORT_DIMS",
      "RED_BITS", "RENDERER", "SHADING_LANGUAGE_VERSION", "STENCIL_BITS", "VENDOR", "VERSION"
    ];
    const params = {};
    for (const name of parameterNames) {
      if (!(name in gl)) continue;
      try { params[name] = normalize(gl.getParameter(gl[name])); }
      catch (error) { params[name] = `[throws:${String(error)}]`; }
    }
    const extensions = (gl.getSupportedExtensions() || []).slice().sort();
    const result = {
      available: true,
      context: gl instanceof WebGL2RenderingContext ? "webgl2" : "webgl",
      vendor: gl.getParameter(gl.VENDOR),
      renderer: gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      unmaskedVendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null,
      unmaskedRenderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null,
      extensions,
      params
    };
    try {
      const vertex = gl.createShader(gl.VERTEX_SHADER);
      const fragment = gl.createShader(gl.FRAGMENT_SHADER);
      gl.shaderSource(vertex, "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}");
      gl.shaderSource(fragment, "precision mediump float;void main(){gl_FragColor=vec4(.17,.31,.47,1.);}");
      gl.compileShader(vertex);
      gl.compileShader(fragment);
      result.shader = {
        vertexCompiled: gl.getShaderParameter(vertex, gl.COMPILE_STATUS),
        fragmentCompiled: gl.getShaderParameter(fragment, gl.COMPILE_STATUS),
        vertexLog: gl.getShaderInfoLog(vertex),
        fragmentLog: gl.getShaderInfoLog(fragment)
      };
    } catch (error) { result.shaderError = String(error); }
    return result;
  };

  const probeCanvas = async (documentRef = document) => {
    const canvas = documentRef.createElement("canvas");
    canvas.width = 420;
    canvas.height = 140;
    const context = canvas.getContext("2d");
    if (!context) return {supported: false, hash: null};
    context.textBaseline = "alphabetic";
    context.fillStyle = "#f2f4f8";
    context.fillRect(0, 0, canvas.width, canvas.height);
    const gradient = context.createLinearGradient(0, 0, 420, 140);
    gradient.addColorStop(0, "#137c8b");
    gradient.addColorStop(0.5, "#ff8c42");
    gradient.addColorStop(1, "#5d3fd3");
    context.fillStyle = gradient;
    context.fillRect(12.5, 11.5, 397.25, 117.75);
    context.globalCompositeOperation = "multiply";
    context.fillStyle = "rgba(255,255,255,.76)";
    context.beginPath();
    context.arc(96.2, 70.4, 47.3, 0, Math.PI * 2);
    context.fill();
    context.globalCompositeOperation = "source-over";
    context.font = "19.3px 'Arial', sans-serif";
    context.fillStyle = "#102a43";
    context.fillText("Parity Ω≈ç√∫˜µ≤≥÷ 😀", 34.25, 71.75);
    context.strokeStyle = "rgba(12, 41, 64, .87)";
    context.lineWidth = 1.35;
    context.strokeText("Browser 2026", 220.4, 104.6);
    const image = context.getImageData(0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/png");
    return {
      supported: true,
      hash: await hashBytes(image.data),
      dataUrlHash: await hashString(dataUrl),
      dataUrlLength: dataUrl.length,
      textMetrics: normalize(context.measureText("Parity Ω≈ç√∫˜µ≤≥÷ 😀"))
    };
  };

  const probeAudio = async () => {
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Offline) return {supported: false, hash: null};
    const context = new Offline(1, 5000, 44100);
    const oscillator = context.createOscillator();
    oscillator.type = "triangle";
    oscillator.frequency.value = 10000;
    const compressor = context.createDynamicsCompressor();
    compressor.threshold.value = -50;
    compressor.knee.value = 40;
    compressor.ratio.value = 12;
    compressor.attack.value = 0;
    compressor.release.value = 0.25;
    oscillator.connect(compressor);
    compressor.connect(context.destination);
    oscillator.start(0);
    const buffer = await context.startRendering();
    const channel = buffer.getChannelData(0);
    const selected = channel.slice(1000, 5000);
    const bytes = new Uint8Array(selected.buffer.slice(selected.byteOffset, selected.byteOffset + selected.byteLength));
    let sum = 0;
    let absolute = 0;
    for (const value of selected) { sum += value; absolute += Math.abs(value); }
    return {
      supported: true,
      sampleRate: buffer.sampleRate,
      length: buffer.length,
      hash: await hashBytes(bytes),
      sum,
      absoluteSum: absolute,
      compressorReduction: compressor.reduction
    };
  };

  const probeFonts = (documentRef = document) => {
    const candidates = [
      "Arial", "Arial Black", "Calibri", "Cambria", "Candara", "Comic Sans MS", "Consolas",
      "Courier New", "DejaVu Sans", "Georgia", "Helvetica", "Inter", "Liberation Sans",
      "Lucida Console", "Menlo", "Microsoft YaHei", "Noto Sans", "Roboto", "Segoe UI",
      "Tahoma", "Times New Roman", "Trebuchet MS", "Ubuntu", "Verdana"
    ];
    const test = "mmmmmmmmmmlliWWΩЖ😀";
    const size = "72px";
    const baselines = ["monospace", "sans-serif", "serif"];
    const container = documentRef.createElement("div");
    container.style.cssText = "position:absolute;left:-99999px;top:-99999px;visibility:hidden;white-space:nowrap";
    documentRef.body.appendChild(container);
    const measure = family => {
      const span = documentRef.createElement("span");
      span.textContent = test;
      span.style.cssText = `font-size:${size};font-family:${family};font-kerning:normal;font-variant-ligatures:normal`;
      container.appendChild(span);
      const rect = span.getBoundingClientRect();
      const output = {width: rect.width, height: rect.height};
      span.remove();
      return output;
    };
    const baselineMetrics = Object.fromEntries(baselines.map(name => [name, measure(name)]));
    const metrics = {};
    const available = [];
    for (const candidate of candidates) {
      const perBaseline = {};
      let differs = false;
      for (const baseline of baselines) {
        const value = measure(`"${candidate}",${baseline}`);
        perBaseline[baseline] = value;
        if (value.width !== baselineMetrics[baseline].width || value.height !== baselineMetrics[baseline].height) differs = true;
      }
      metrics[candidate] = perBaseline;
      if (differs || (documentRef.fonts && documentRef.fonts.check(`${size} "${candidate}"`))) available.push(candidate);
    }
    container.remove();
    return {available, count: available.length, baselineMetrics, metrics};
  };

  const probeMedia = async (navigatorRef) => {
    if (!navigatorRef.mediaDevices || typeof navigatorRef.mediaDevices.enumerateDevices !== "function") {
      return {supported: false, count: 0, devices: []};
    }
    const devices = await navigatorRef.mediaDevices.enumerateDevices();
    return {
      supported: true,
      count: devices.length,
      labelsExposed: devices.some(device => Boolean(device.label)),
      devices: devices.map(device => ({
        kind: device.kind,
        label: device.label,
        deviceIdHash: device.deviceId ? null : "",
        groupIdPresent: Boolean(device.groupId)
      })),
      kindCounts: devices.reduce((output, device) => {
        output[device.kind] = (output[device.kind] || 0) + 1;
        return output;
      }, {})
    };
  };

  const probeBattery = async (navigatorRef) => {
    if (typeof navigatorRef.getBattery !== "function") return {supported: false};
    const battery = await navigatorRef.getBattery();
    return {
      supported: true,
      charging: battery.charging,
      chargingTime: battery.chargingTime,
      dischargingTime: battery.dischargingTime,
      level: battery.level
    };
  };

  const probeStorage = async (navigatorRef, windowRef) => {
    const output = {
      secureContext: windowRef.isSecureContext,
      cookie: document.cookie,
      localStorage: {supported: false},
      sessionStorage: {supported: false},
      indexedDB: {supported: Boolean(windowRef.indexedDB)},
      serviceWorker: {supported: Boolean(navigatorRef.serviceWorker)},
      estimate: null,
      persisted: null
    };
    try {
      const key = `parity-${sid}`;
      windowRef.localStorage.setItem(key, "1");
      output.localStorage = {supported: windowRef.localStorage.getItem(key) === "1", length: windowRef.localStorage.length};
      windowRef.localStorage.removeItem(key);
    } catch (error) { output.localStorage = {supported: false, error: String(error)}; }
    try {
      const historyKey = "__paritylab_session_visits";
      const priorVisits = Number(windowRef.localStorage.getItem(historyKey) || "0");
      const visits = Number.isFinite(priorVisits) ? priorVisits + 1 : 1;
      windowRef.localStorage.setItem(historyKey, String(visits));
      output.history = {
        sessionPresent: windowRef.localStorage.getItem(historyKey) === String(visits),
        visits
      };
    } catch (error) { output.history = {sessionPresent: false, error: String(error)}; }
    try {
      const key = `parity-${sid}`;
      windowRef.sessionStorage.setItem(key, "1");
      output.sessionStorage = {supported: windowRef.sessionStorage.getItem(key) === "1", length: windowRef.sessionStorage.length};
      windowRef.sessionStorage.removeItem(key);
    } catch (error) { output.sessionStorage = {supported: false, error: String(error)}; }
    if (navigatorRef.storage && typeof navigatorRef.storage.estimate === "function") {
      output.estimate = normalize(await navigatorRef.storage.estimate());
    }
    if (navigatorRef.storage && typeof navigatorRef.storage.persisted === "function") {
      output.persisted = await navigatorRef.storage.persisted();
    }
    return output;
  };

  const probeSpeech = async (windowRef) => {
    if (!windowRef.speechSynthesis) return {supported: false, count: 0, voices: []};
    let voices = windowRef.speechSynthesis.getVoices();
    if (!voices.length) {
      await new Promise(resolve => {
        let resolved = false;
        const finish = () => { if (!resolved) { resolved = true; resolve(); } };
        windowRef.speechSynthesis.addEventListener("voiceschanged", finish, {once: true});
        setTimeout(finish, 500);
      });
      voices = windowRef.speechSynthesis.getVoices();
    }
    return {
      supported: true,
      count: voices.length,
      voices: voices.slice(0, 100).map(voice => ({
        name: voice.name, lang: voice.lang, localService: voice.localService, default: voice.default
      }))
    };
  };

  const probeCodeIntegrity = (windowRef, navigatorRef) => {
    const toStringSource = Function.prototype.toString.call(Function.prototype.toString);
    const samples = {
      querySelector: windowRef.Document && windowRef.Document.prototype.querySelector,
      createElement: windowRef.Document && windowRef.Document.prototype.createElement,
      getContext: windowRef.HTMLCanvasElement && windowRef.HTMLCanvasElement.prototype.getContext,
      permissionsQuery: navigatorRef.permissions && navigatorRef.permissions.query,
      fetch: windowRef.fetch,
      evaluateLike: windowRef.eval
    };
    const nativeSamples = {};
    const nonNativeExpected = [];
    for (const [name, fn] of Object.entries(samples)) {
      if (typeof fn !== "function") continue;
      const source = Function.prototype.toString.call(fn);
      nativeSamples[name] = {native: nativeShape(fn), source};
      if (!nativeShape(fn)) nonNativeExpected.push(name);
    }
    let errorStack = "";
    try { throw new Error("parity-stack-probe"); }
    catch (error) { errorStack = String(error.stack || error); }
    return {
      functionToString: toStringSource,
      functionToStringNative: nativeShape(Function.prototype.toString),
      nativeSamples,
      nonNativeExpected,
      descriptors: {
        navigatorWebdriver: propertyDescriptor(Object.getPrototypeOf(navigatorRef), "webdriver"),
        navigatorLanguages: propertyDescriptor(Object.getPrototypeOf(navigatorRef), "languages"),
        navigatorPlugins: propertyDescriptor(Object.getPrototypeOf(navigatorRef), "plugins"),
        screenWidth: propertyDescriptor(Object.getPrototypeOf(windowRef.screen), "width")
      },
      errorStack
    };
  };

  const probeAutomation = (windowRef, errorStack) => {
    const exact = [
      "__playwright__binding__", "__pwInitScripts", "__puppeteer_evaluation_script__",
      "_Selenium_IDE_Recorder", "callPhantom", "_phantom", "phantom",
      "webdriver", "domAutomation", "domAutomationController"
    ];
    const regexes = [/^cdc_[a-zA-Z0-9_]+_Array$/, /^cdc_[a-zA-Z0-9_]+_Promise$/, /^cdc_[a-zA-Z0-9_]+_Symbol$/];
    const ownKeys = Reflect.ownKeys(windowRef).map(String);
    const suspiciousGlobals = ownKeys.filter(key => exact.includes(key) || regexes.some(pattern => pattern.test(key)));
    const source = String(errorStack || "").toLowerCase();
    const markerTokens = ["playwright", "puppeteer", "utilityscript", "__puppeteer_evaluation_script__", "selenium", "webdriver"];
    const stackMarkers = markerTokens.filter(token => source.includes(token));
    return {suspiciousGlobals, stackMarkers, ownKeyCount: ownKeys.length};
  };

  const probePerformance = () => {
    const entries = performance.getEntries().slice(0, 1000).map(entry => {
      const value = typeof entry.toJSON === "function" ? entry.toJSON() : {
        name: entry.name, entryType: entry.entryType, startTime: entry.startTime, duration: entry.duration
      };
      if (value.name && typeof value.name === "string") {
        try {
          const parsed = new URL(value.name, location.href);
          if (parsed.origin === location.origin) value.name = parsed.pathname;
        } catch (_) {}
      }
      return value;
    });
    const navigation = performance.getEntriesByType("navigation").map(entry => entry.toJSON ? entry.toJSON() : normalize(entry));
    const resources = performance.getEntriesByType("resource").map(entry => entry.toJSON ? entry.toJSON() : normalize(entry));
    return {
      timeOrigin: performance.timeOrigin,
      now: performance.now(),
      navigation,
      resourceCount: resources.length,
      resources: resources.slice(0, 500),
      entries,
      observerSamples: normalize(performanceSamples)
    };
  };

  const collectWindowProbe = async () => {
    const navigatorRef = window.navigator;
    const codeIntegrity = probeCodeIntegrity(window, navigatorRef);
    const data = {
      runtime: {
        webdriver: navigatorRef.webdriver,
        userAgent: navigatorRef.userAgent,
        appVersion: navigatorRef.appVersion,
        appName: navigatorRef.appName,
        product: navigatorRef.product,
        productSub: navigatorRef.productSub,
        platform: navigatorRef.platform,
        vendor: navigatorRef.vendor,
        vendorSub: navigatorRef.vendorSub,
        language: navigatorRef.language,
        languages: Array.from(navigatorRef.languages || []),
        hardwareConcurrency: navigatorRef.hardwareConcurrency,
        deviceMemory: navigatorRef.deviceMemory ?? null,
        maxTouchPoints: navigatorRef.maxTouchPoints,
        cookieEnabled: navigatorRef.cookieEnabled,
        doNotTrack: navigatorRef.doNotTrack,
        globalPrivacyControl: navigatorRef.globalPrivacyControl ?? null,
        pdfViewerEnabled: navigatorRef.pdfViewerEnabled ?? null,
        onLine: navigatorRef.onLine,
        userAgentData: await probeUserAgentData(navigatorRef)
      },
      permissions: await safe("permissions", () => withTimeout(probePermissions(navigatorRef), 4000, "permissions"), {supported: false, states: {}, errors: {timeout: "probe timed out"}}),
      plugins: probePlugins(navigatorRef),
      chrome: probeChrome(window),
      window: {
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
        screenX: window.screenX,
        screenY: window.screenY,
        pageXOffset: window.pageXOffset,
        pageYOffset: window.pageYOffset,
        visualViewport: window.visualViewport ? normalize({
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          scale: window.visualViewport.scale,
          offsetLeft: window.visualViewport.offsetLeft,
          offsetTop: window.visualViewport.offsetTop
        }) : null,
        devicePixelRatio: window.devicePixelRatio,
        isSecureContext: window.isSecureContext,
        crossOriginIsolated: window.crossOriginIsolated
      },
      screen: {
        width: screen.width, height: screen.height,
        availWidth: screen.availWidth, availHeight: screen.availHeight,
        availLeft: screen.availLeft, availTop: screen.availTop,
        colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth,
        orientation: screen.orientation ? normalize({type: screen.orientation.type, angle: screen.orientation.angle}) : null
      },
      locale: {
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        resolvedLocale: Intl.DateTimeFormat().resolvedOptions().locale,
        calendar: Intl.DateTimeFormat().resolvedOptions().calendar,
        numberingSystem: Intl.DateTimeFormat().resolvedOptions().numberingSystem,
        hourCycle: Intl.DateTimeFormat().resolvedOptions().hourCycle,
        dateOffset: new Date().getTimezoneOffset(),
        dateString: new Date(1722816000000).toString(),
        intlDate: new Intl.DateTimeFormat(undefined, {dateStyle: "full", timeStyle: "long"}).format(new Date(1722816000000)),
        numberSample: new Intl.NumberFormat().format(1234567.89),
        collatorSample: ["a", "ä", "z"].sort(new Intl.Collator().compare)
      },
      webgl: probeWebGL(document),
      canvas: await safe("canvas", () => probeCanvas(document), {supported: false, hash: null}),
      audio: await safe("audio", () => withTimeout(probeAudio(), 4000, "audio"), {supported: false, hash: null}),
      fonts: await safe("fonts", async () => probeFonts(document), {available: [], count: 0}),
      media: await safe("media", () => withTimeout(probeMedia(navigatorRef), 3000, "media devices"), {supported: false, count: 0}),
      battery: await safe("battery", () => withTimeout(probeBattery(navigatorRef), 2000, "battery"), {supported: false}),
      storage: await safe("storage", () => withTimeout(probeStorage(navigatorRef, window), 3000, "storage"), {}),
      clipboard: {
        apiPresent: Boolean(navigatorRef.clipboard),
        readTextType: navigatorRef.clipboard ? typeof navigatorRef.clipboard.readText : "undefined",
        writeTextType: navigatorRef.clipboard ? typeof navigatorRef.clipboard.writeText : "undefined"
      },
      notifications: {
        apiPresent: "Notification" in window,
        permission: "Notification" in window ? Notification.permission : null
      },
      speech: await safe("speech", () => probeSpeech(window), {supported: false, count: 0}),
      codeIntegrity,
      automation: probeAutomation(window, codeIntegrity.errorStack),
      userActivation: navigatorRef.userActivation ? {
        isActive: navigatorRef.userActivation.isActive,
        hasBeenActive: navigatorRef.userActivation.hasBeenActive
      } : null,
      performance: probePerformance()
    };
    return data;
  };

  const collectIframeProbe = async () => {
    const iframe = document.createElement("iframe");
    iframe.style.cssText = "position:absolute;width:10px;height:10px;left:-9999px;top:-9999px;border:0";
    iframe.srcdoc = "<!doctype html><html><body></body></html>";
    document.body.appendChild(iframe);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("iframe timeout")), 2000);
      iframe.addEventListener("load", () => { clearTimeout(timer); resolve(); }, {once: true});
    });
    const frameWindow = iframe.contentWindow;
    const frameDocument = iframe.contentDocument;
    if (!frameWindow || !frameDocument) throw new Error("same-origin iframe unavailable");
    const result = {
      runtime: {
        webdriver: frameWindow.navigator.webdriver,
        userAgent: frameWindow.navigator.userAgent,
        platform: frameWindow.navigator.platform,
        vendor: frameWindow.navigator.vendor,
        language: frameWindow.navigator.language,
        languages: Array.from(frameWindow.navigator.languages || []),
        hardwareConcurrency: frameWindow.navigator.hardwareConcurrency,
        deviceMemory: frameWindow.navigator.deviceMemory ?? null,
        maxTouchPoints: frameWindow.navigator.maxTouchPoints,
        userAgentData: await probeUserAgentData(frameWindow.navigator)
      },
      locale: {
        timezone: frameWindow.Intl.DateTimeFormat().resolvedOptions().timeZone,
        resolvedLocale: frameWindow.Intl.DateTimeFormat().resolvedOptions().locale,
        dateOffset: new frameWindow.Date().getTimezoneOffset()
      },
      window: {
        devicePixelRatio: frameWindow.devicePixelRatio,
        innerWidth: frameWindow.innerWidth,
        innerHeight: frameWindow.innerHeight
      },
      webgl: probeWebGL(frameDocument),
      codeIntegrity: {
        functionToStringNative: /\{\s*\[native code\]\s*\}/.test(frameWindow.Function.prototype.toString.call(frameWindow.Function.prototype.toString)),
        webdriverDescriptor: propertyDescriptor(Object.getPrototypeOf(frameWindow.navigator), "webdriver")
      }
    };
    iframe.remove();
    return result;
  };

    return {collectWindowProbe, collectIframeProbe, probePerformance};
  };


})();
