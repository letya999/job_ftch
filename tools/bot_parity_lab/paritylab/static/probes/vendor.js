(() => {
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {};
  const sid = new URL(document.currentScript?.src || location.href).searchParams.get("sid") || "unassigned";

  let vendorSequence = 0;

  const post = async (component, version, result) => {
    vendorSequence += 1;
    const response = await fetch(`/api/vendor/${component}?sid=${encodeURIComponent(sid)}`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({sequence: vendorSequence, result})
    });
    if (!response.ok) throw new Error(`${component} evidence rejected: ${response.status}`);
    return {component, version, stored: true};
  };

  const fingerprintjs = async () => {
    const module = await import("/static/vendor/fingerprintjs-5.2.0.esm.js");
    const agent = await module.load({delayFallback: 0});
    const result = await agent.get();
    return post("fingerprintjs", "5.2.0", {
      visitorId: result.visitorId,
      confidence: result.confidence,
      components: result.components
    });
  };

  const thumbmark = async () => {
    const module = await import("/static/vendor/thumbmark-1.10.1.esm.js");
    const agent = new module.Thumbmark({logging: false, timeout: 5000, performance: true});
    const result = await agent.get();
    return post("thumbmark", "1.10.1", result);
  };

  const botd = async () => {
    const module = await import("/static/vendor/botd-2.0.0.esm.js");
    const agent = await module.load();
    return post("botd", "2.0.0", await agent.detect());
  };

  probes.collectVendorOracles = async safe => {
    const results = await Promise.all([
      safe("vendor.fingerprintjs", fingerprintjs, {component: "fingerprintjs", stored: false}),
      safe("vendor.thumbmark", thumbmark, {component: "thumbmark", stored: false}),
      safe("vendor.botd", botd, {component: "botd", stored: false})
    ]);
    return Object.fromEntries(results.map(item => [item.component, item]));
  };
})();
