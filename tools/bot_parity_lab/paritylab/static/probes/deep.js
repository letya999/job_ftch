(() => {
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {};
  const finiteObject = object => Object.fromEntries(
    Object.entries(object).filter(([, value]) => typeof value !== "number" || Number.isFinite(value))
  );
  const withTimeout = (promise, timeoutMs, label) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs))
  ]);

  const {geometryProbe, webGpuProbe, mediaProbe, fontRenderingProbe} =
    probes.createRenderingCollectors({finiteObject, withTimeout});

  probes.collectDeepExtras = async helpers => {
    const {safe, hashString, hashBytes, normalize} = helpers;
    const runtime = probes.collectRuntimeExtras ? await probes.collectRuntimeExtras(helpers) : {};
    const vendors = probes.collectVendorOracles ? await probes.collectVendorOracles(safe) : {};
    const [geometry, webgpu, mediaCapabilities, fontRendering, cssDefaults] = await Promise.all([
      safe("extras.geometry", () => withTimeout(geometryProbe(hashString), 3000, "geometry"), {supported: false}),
      safe("extras.webgpu", () => withTimeout(webGpuProbe(hashString), 4000, "webgpu"), {supported: false}),
      safe("extras.media", () => withTimeout(mediaProbe(), 5000, "media"), {}),
      safe("extras.fontRendering", () => withTimeout(fontRenderingProbe(hashString), 4000, "font rendering"), {supported: false}),
      safe("extras.cssDefaults", async () => {
        const tags = ["button", "input", "select", "textarea", "progress", "meter", "details"];
        const output = {};
        for (const tag of tags) {
          const node = document.createElement(tag);
          document.body.appendChild(node);
          const style = getComputedStyle(node);
          output[tag] = {
            appearance: style.appearance, fontFamily: style.fontFamily, fontSize: style.fontSize,
            lineHeight: style.lineHeight, borderRadius: style.borderRadius, boxSizing: style.boxSizing
          };
          node.remove();
        }
        return output;
      }, {})
    ]);
    return {
      runtime,
      vendors,
      geometry,
      webgpu,
      mediaCapabilities,
      fontRendering,
      cssDefaults
    };
  };
  probes.collectDeep = async helpers => {
    const {safe, hashString, hashBytes, normalize} = helpers;
    const data = {};
    data.webrtc = await safe("deep.webrtc", async () => {
      if (typeof RTCPeerConnection !== "function") return {supported: false};
      const pc = new RTCPeerConnection();
      pc.createDataChannel("parity");
      const candidates = [];
      const stateTransitions = [];
      const started = performance.now();
      pc.onicegatheringstatechange = () => stateTransitions.push({state: pc.iceGatheringState, atMs: performance.now() - started});
      pc.onicecandidate = event => {
        if (event.candidate) candidates.push({candidate: event.candidate, atMs: performance.now() - started});
      };
      await pc.createOffer().then(offer => pc.setLocalDescription(offer));
      await Promise.race([
        new Promise(resolve => {
          if (pc.iceGatheringState === "complete") resolve();
          else pc.addEventListener("icegatheringstatechange", () => {
            if (pc.iceGatheringState === "complete") resolve();
          });
        }),
        new Promise(resolve => setTimeout(resolve, 1500))
      ]);
      const types = {};
      const protocols = {};
      const addressClasses = {};
      let relatedAddressCount = 0;
      for (const item of candidates) {
        const candidate = item.candidate;
        const type = candidate.type || /typ (\w+)/.exec(candidate.candidate || "")?.[1] || "unknown";
        const protocol = candidate.protocol || "unknown";
        const address = String(candidate.address || "");
        const addressClass = address.endsWith(".local") ? "mdns" :
          address.includes(":") ? "ipv6" :
          /^(10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(address) ? "private-ipv4" :
          address ? "public-ipv4" : "unknown";
        types[type] = (types[type] || 0) + 1;
        protocols[protocol] = (protocols[protocol] || 0) + 1;
        addressClasses[addressClass] = (addressClasses[addressClass] || 0) + 1;
        if (candidate.relatedAddress) relatedAddressCount += 1;
      }
      const foundationText = candidates.map(item => item.candidate.foundation || "").join("|");
      const candidateIntervals = candidates.slice(1).map((item, index) => item.atMs - candidates[index].atMs);
      const sdp = pc.localDescription?.sdp || "";
      const sdpLines = sdp.split(/\r?\n/).filter(Boolean);
      const codecs = sdpLines.filter(line => line.startsWith("a=rtpmap:")).map(line => line.replace(/^a=rtpmap:\d+\s+/, "").split("/")[0]).sort();
      const sdpShape = sdpLines.map(line => line.split(/[=:\s]/, 1)[0] + ":" + (line.startsWith("a=") ? line.slice(2).split(":", 1)[0] : "line"));
      pc.close();
      return {
        supported: true,
        candidateCount: candidates.length,
        types,
        protocols,
        addressClasses,
        relatedAddressCount,
        candidateIntervals,
        gatheringDurationMs: performance.now() - started,
        finalGatheringState: pc.iceGatheringState,
        stateTransitions,
        codecs: Array.from(new Set(codecs)),
        sdpLineCount: sdpLines.length,
        extmapCount: sdpLines.filter(line => line.startsWith("a=extmap:")).length,
        sdpShapeHash: await hashString(sdpShape.join("|")),
        foundationsHash: await hashString(foundationText)
      };
    }, {supported: false});
    data.timing = await safe("deep.timing", async () => {
      const deltas = [];
      let previous = performance.now();
      let violations = 0;
      for (let index = 0; index < 1200; index += 1) {
        const current = performance.now();
        const delta = current - previous;
        if (delta > 0) deltas.push(delta);
        if (delta < 0) violations += 1;
        previous = current;
      }
      const driftSamples = [];
      for (let index = 0; index < 16; index += 1) {
        driftSamples.push(Date.now() - (performance.timeOrigin + performance.now()));
        await Promise.resolve();
      }
      const taskOrder = [];
      await new Promise(resolve => {
        const channel = new MessageChannel();
        channel.port1.onmessage = () => taskOrder.push("message");
        Promise.resolve().then(() => taskOrder.push("promise"));
        queueMicrotask(() => taskOrder.push("microtask"));
        channel.port2.postMessage(1);
        setTimeout(() => { taskOrder.push("timeout"); resolve(); }, 0);
      });
      const rafTimes = [];
      if (typeof requestAnimationFrame === "function" && document.visibilityState === "visible") {
        await new Promise(resolve => {
          const sample = stamp => {
            rafTimes.push(stamp);
            if (rafTimes.length >= 12) resolve();
            else requestAnimationFrame(sample);
          };
          requestAnimationFrame(sample);
        });
      }
      const timeoutDelays = [];
      for (let index = 0; index < 8; index += 1) {
        const started = performance.now();
        await new Promise(resolve => setTimeout(resolve, 0));
        timeoutDelays.push(performance.now() - started);
      }
      return {
        nowResolutionMs: deltas.length ? Math.min(...deltas) : null,
        nowPositiveDeltaCount: deltas.length,
        nowDeltaDigest: await hashString(deltas.slice(0, 256).map(value => value.toFixed(6)).join(",")),
        dateDriftMs: driftSamples[driftSamples.length - 1] ?? null,
        dateDriftRangeMs: driftSamples.length ? Math.max(...driftSamples) - Math.min(...driftSamples) : null,
        monotonicViolations: violations,
        taskOrder,
        rafIntervals: rafTimes.slice(1).map((value, index) => value - rafTimes[index]),
        timeoutDelays,
        timeOrigin: performance.timeOrigin,
        navigationStart: performance.getEntriesByType("navigation")[0]?.startTime ?? null,
        crossOriginIsolated: window.crossOriginIsolated,
        sharedArrayBuffer: typeof SharedArrayBuffer === "function",
        atomicsWaitAsync: typeof Atomics === "object" && typeof Atomics.waitAsync === "function"
      };
    }, null);
    data.mediaQueries = await safe("deep.mediaQueries", async () => {
      const queries = [
        "(prefers-color-scheme: dark)", "(prefers-color-scheme: light)",
        "(prefers-reduced-motion: reduce)", "(prefers-contrast: more)",
        "(forced-colors: active)", "(inverted-colors: inverted)",
        "(pointer: coarse)", "(pointer: fine)", "(hover: hover)", "(hover: none)",
        "(any-pointer: coarse)", "(display-mode: fullscreen)", "(update: fast)"
      ];
      const output = {};
      for (const query of queries) {
        try { output[query] = matchMedia(query).matches; }
        catch (_) { output[query] = null; }
      }
      return output;
    }, {});
    data.apiPresence = await safe("deep.apiPresence", async () => ({
      bluetooth: "bluetooth" in navigator,
      usb: "usb" in navigator,
      hid: "hid" in navigator,
      serial: "serial" in navigator,
      xr: "xr" in navigator,
      keyboardApi: "keyboard" in navigator,
      locks: "locks" in navigator,
      wakeLock: "wakeLock" in navigator,
      presentation: "presentation" in window,
      mediaSession: "mediaSession" in navigator,
      paymentRequest: "PaymentRequest" in window,
      credentials: "credentials" in navigator,
      gamepad: {
        present: "getGamepads" in navigator,
        connected: Array.from(navigator.getGamepads ? navigator.getGamepads() : []).filter(Boolean).length
      },
      sharedArrayBuffer: "SharedArrayBuffer" in window,
      atomics: "Atomics" in window,
      structuredClone: typeof structuredClone === "function",
      mathMl: "MathMLElement" in window,
      scheduler: "scheduler" in window,
      cookieStore: "cookieStore" in navigator
    }), {});
    data.audioContext = await safe("deep.audioContext", async () => {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return {supported: false};
      const context = new Ctx();
      const output = {
        supported: true,
        state: context.state,
        sampleRate: context.sampleRate,
        baseLatency: context.baseLatency ?? null,
        outputLatency: context.outputLatency ?? null,
        maxChannelCount: context.destination.maxChannelCount
      };
      if (context.state === "suspended") {
        try { await context.resume(); output.stateAfterResume = context.state; }
        catch (_) {}
      }
      await context.close();
      return output;
    }, {supported: false});
    data.canvasBlob = await safe("deep.canvasBlob", async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 64;
      canvas.height = 32;
      const context = canvas.getContext("2d");
      if (!context || typeof canvas.toBlob !== "function") return {supported: false};
      context.fillStyle = "#345";
      context.fillRect(0, 0, 64, 32);
      context.fillStyle = "#abc";
      context.font = "12px sans-serif";
      context.fillText("parity", 4, 16);
      const dataUrl = canvas.toDataURL("image/png");
      const blobHash = await new Promise(resolve => canvas.toBlob(async blob => {
        if (!blob) { resolve(null); return; }
        resolve(await hashBytes(await blob.arrayBuffer()));
      }, "image/png"));
      return {
        supported: true,
        dataUrlHash: await hashString(dataUrl),
        blobHash,
        blobProduced: blobHash !== null
      };
    }, {supported: false});
    data.memory = performance.memory ? {
      present: true,
      jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
      totalJSHeapSize: performance.memory.totalJSHeapSize,
      usedJSHeapSize: performance.memory.usedJSHeapSize
    } : {present: false};
    data.intl = {
      displayNames: "DisplayNames" in Intl,
      segmenter: "Segmenter" in Intl,
      pluralRules: "PluralRules" in Intl,
      relativeTimeFormat: "RelativeTimeFormat" in Intl,
      listFormat: "ListFormat" in Intl
    };
    data.fontsExtended = await safe("deep.fontsExtended", async () => {
      if (!document.fonts) return {supported: false};
      const extra = [
        "Cascadia Code", "Fira Code", "JetBrains Mono", "Calibri Light",
        "Franklin Gothic Medium", "Georgia Pro", "Verdana Pro", "Noto Sans Mono",
        "Ubuntu Mono", "Courier", "Lucida Sans", "Palatino Linotype",
        "Book Antiqua", "Impact", "MS Gothic", "SimSun", "PMingLiU",
        "Malgun Gothic", "Leelawadee UI"
      ];
      const available = extra.filter(family => {
        try { return document.fonts.check(`16px "${family}"`); }
        catch (_) { return false; }
      });
      return {supported: true, available};
    }, {supported: false});
    data.webgl2 = await safe("deep.webgl2", async () => {
      const canvas = document.createElement("canvas");
      const gl2 = canvas.getContext("webgl2");
      if (!gl2) return {available: false};
      const names = [
        "MAX_3D_TEXTURE_SIZE", "MAX_ARRAY_TEXTURE_LAYERS", "MAX_COLOR_ATTACHMENTS",
        "MAX_DRAW_BUFFERS", "MAX_SAMPLES", "MAX_UNIFORM_BUFFER_BINDINGS",
        "MAX_VERTEX_UNIFORM_BLOCKS", "MAX_FRAGMENT_UNIFORM_BLOCKS"
      ];
      const params = {};
      for (const name of names) {
        if (!(name in gl2)) continue;
        try { params[name] = normalize(gl2.getParameter(gl2[name])); }
        catch (error) { params[name] = `[throws:${String(error)}]`; }
      }
      return {available: true, params};
    }, {available: false});
    data.extras = window.ParityLabProbes && window.ParityLabProbes.collectDeepExtras
      ? await safe("deep.extras", () => window.ParityLabProbes.collectDeepExtras({safe, hashString, normalize}), {})
      : {unavailable: true};
    return data;
  };


})();
