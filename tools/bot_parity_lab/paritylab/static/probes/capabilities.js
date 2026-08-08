(() => {
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {};
  const deadline = (promise, timeoutMs, label) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs))
  ]);

  const storageShape = async () => {
    const manager = navigator.storage;
    const estimate = manager && manager.estimate ? await deadline(manager.estimate(), 2000, "storage estimate") : null;
    const persisted = manager && manager.persisted ? await deadline(manager.persisted(), 2000, "storage persisted") : null;
    const buckets = manager && manager.getDirectory ? "opfs" : manager && manager.buckets ? "buckets" : null;
    return {
      cookieEnabled: navigator.cookieEnabled,
      cookieStore: typeof cookieStore !== "undefined",
      localStorage: typeof localStorage !== "undefined",
      sessionStorage: typeof sessionStorage !== "undefined",
      indexedDB: typeof indexedDB !== "undefined",
      caches: typeof caches !== "undefined",
      serviceWorker: Boolean(navigator.serviceWorker),
      storageManager: Boolean(manager),
      persisted,
      estimate: estimate ? {usage: estimate.usage ?? null, quota: estimate.quota ?? null, usageDetails: estimate.usageDetails ?? null} : null,
      directory: Boolean(manager && manager.getDirectory),
      buckets,
      sharedStorage: "sharedStorage" in globalThis,
      storageAccessAPI: typeof document.hasStorageAccess === "function",
      requestStorageAccess: typeof document.requestStorageAccess === "function",
      ancestorOrigins: location.ancestorOrigins ? Array.from(location.ancestorOrigins) : [],
      origin: location.origin
    };
  };

  const mediaDeviceShape = async () => {
    const media = navigator.mediaDevices;
    const initialDevices = media && media.enumerateDevices
      ? await deadline(media.enumerateDevices(), 2500, "enumerate devices")
      : [];
    const permissionStates = {};
    if (navigator.permissions?.query) {
      for (const name of ["camera", "microphone"]) {
        try { permissionStates[name] = (await deadline(navigator.permissions.query({name}), 1000, `${name} permission`)).state; }
        catch (error) { permissionStates[name] = `error:${error.name || "Error"}`; }
      }
    }
    let deviceChangeEvents = 0;
    const onDeviceChange = () => { deviceChangeEvents += 1; };
    if (media) media.addEventListener("devicechange", onDeviceChange);
    await new Promise(resolve => setTimeout(resolve, 250));
    if (media) media.removeEventListener("devicechange", onDeviceChange);
    const devices = media && media.enumerateDevices
      ? await deadline(media.enumerateDevices(), 2500, "enumerate devices final")
      : [];
    const kindCounts = {};
    for (const device of devices) kindCounts[device.kind] = (kindCounts[device.kind] || 0) + 1;
    return {
      mediaDevices: Boolean(media),
      supportedConstraints: media && media.getSupportedConstraints ? media.getSupportedConstraints() : {},
      permissionStates,
      initialDeviceCount: initialDevices.length,
      deviceCount: devices.length,
      kindCounts,
      deviceChangeEvents,
      initialLabelsExposed: initialDevices.some(device => Boolean(device.label)),
      labelsExposed: devices.some(device => Boolean(device.label)),
      groupIdsExposed: devices.some(device => Boolean(device.groupId)),
      inputCapabilities: typeof InputDeviceInfo !== "undefined" && typeof InputDeviceInfo.prototype.getCapabilities === "function",
      trackCapabilities: typeof MediaStreamTrack !== "undefined" && typeof MediaStreamTrack.prototype.getCapabilities === "function",
      selectAudioOutput: Boolean(media && media.selectAudioOutput),
      captureController: typeof CaptureController !== "undefined",
      restrictionTarget: typeof RestrictionTarget !== "undefined"
    };
  };

  const embeddedStorageShape = async () => {
    const target = String(window.__PARITY_CONFIG__?.storageFrameUrl || "");
    if (!target) return {supported: false, reason: "frame URL unavailable"};
    const expectedOrigin = new URL(target).origin;
    const iframe = document.createElement("iframe");
    iframe.hidden = true;
    iframe.src = target;
    document.body.appendChild(iframe);
    try {
      return await deadline(new Promise(resolve => {
        const receive = event => {
          if (event.origin !== expectedOrigin || event.source !== iframe.contentWindow) return;
          if (!event.data || event.data.type !== "parity-storage-frame") return;
          removeEventListener("message", receive);
          resolve({supported: true, sameOrigin: expectedOrigin === location.origin, ...event.data.result});
        };
        addEventListener("message", receive);
      }), 5000, "embedded storage");
    } finally {
      iframe.remove();
    }
  };

  probes.collectRuntimeExtras = async ({safe}) => ({
    display: await safe("runtime.display", async () => ({
      screen: {
        width: screen.width,
        height: screen.height,
        availWidth: screen.availWidth,
        availHeight: screen.availHeight,
        availLeft: screen.availLeft ?? null,
        availTop: screen.availTop ?? null,
        colorDepth: screen.colorDepth,
        pixelDepth: screen.pixelDepth,
        orientation: screen.orientation ? {
          type: screen.orientation.type,
          angle: screen.orientation.angle
        } : null,
        extended: Boolean(screen.isExtended)
      },
      viewport: {
        innerWidth: innerWidth,
        innerHeight: innerHeight,
        outerWidth: outerWidth,
        outerHeight: outerHeight,
        screenX: screenX,
        screenY: screenY,
        devicePixelRatio: devicePixelRatio,
        visualViewport: visualViewport ? {
          width: visualViewport.width,
          height: visualViewport.height,
          scale: visualViewport.scale,
          offsetLeft: visualViewport.offsetLeft,
          offsetTop: visualViewport.offsetTop
        } : null
      }
    }), null),
    preferences: await safe("runtime.preferences", async () => {
      const queries = [
        "(prefers-color-scheme: dark)", "(prefers-color-scheme: light)",
        "(prefers-reduced-motion: reduce)", "(prefers-reduced-transparency: reduce)",
        "(prefers-contrast: more)", "(prefers-contrast: less)",
        "(forced-colors: active)", "(inverted-colors: inverted)",
        "(monochrome)", "(dynamic-range: high)", "(video-dynamic-range: high)",
        "(color-gamut: srgb)", "(color-gamut: p3)", "(color-gamut: rec2020)",
        "(pointer: none)", "(pointer: coarse)", "(pointer: fine)",
        "(any-pointer: coarse)", "(any-pointer: fine)",
        "(hover: none)", "(hover: hover)", "(any-hover: hover)",
        "(update: none)", "(update: slow)", "(update: fast)",
        "(overflow-block: scroll)", "(scripting: enabled)"
      ];
      return Object.fromEntries(queries.map(query => {
        try { return [query, matchMedia(query).matches]; }
        catch (_) { return [query, null]; }
      }));
    }, {}),
    navigatorShape: await safe("runtime.navigatorShape", async () => ({
      pdfViewerEnabled: navigator.pdfViewerEnabled ?? null,
      cookieEnabled: navigator.cookieEnabled,
      onLine: navigator.onLine,
      maxTouchPoints: navigator.maxTouchPoints,
      deviceMemory: navigator.deviceMemory ?? null,
      hardwareConcurrency: navigator.hardwareConcurrency,
      globalPrivacyControl: navigator.globalPrivacyControl ?? null,
      doNotTrack: navigator.doNotTrack,
      webdriver: navigator.webdriver,
      virtualKeyboard: "virtualKeyboard" in navigator,
      userAgentData: "userAgentData" in navigator
    }), {}),
    storage: await safe("runtime.storageShape", storageShape, {}),
    embeddedStorage: await safe("runtime.embeddedStorage", embeddedStorageShape, {supported: false}),
    mediaDevices: await safe("runtime.mediaDeviceShape", mediaDeviceShape, {})
  });
})();
