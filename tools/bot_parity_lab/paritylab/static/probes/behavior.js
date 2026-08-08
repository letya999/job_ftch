(() => {
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {};

  const rounded = value => Math.round(Number(value || 0) * 100) / 100;

  probes.eventData = event => {
    const data = {};
    if ("clientX" in event) {
      data.x = rounded(event.clientX);
      data.y = rounded(event.clientY);
      data.screenX = rounded(event.screenX);
      data.screenY = rounded(event.screenY);
      data.movementX = rounded(event.movementX);
      data.movementY = rounded(event.movementY);
      data.buttons = event.buttons;
      data.button = event.button;
      const target = event.target instanceof Element ? event.target : null;
      if (target) {
        const rect = target.getBoundingClientRect();
        data.target = target.id || target.tagName;
        data.targetRect = {
          left: rounded(rect.left), top: rounded(rect.top),
          width: rounded(rect.width), height: rounded(rect.height)
        };
      }
    }
    if ("pointerType" in event) {
      data.pointerType = event.pointerType;
      data.pointerId = event.pointerId;
      data.pressure = event.pressure;
      data.tangentialPressure = event.tangentialPressure;
      data.width = event.width;
      data.height = event.height;
      data.tiltX = event.tiltX;
      data.tiltY = event.tiltY;
      data.twist = event.twist;
      data.isPrimary = event.isPrimary;
    }
    if ("deltaY" in event) {
      data.deltaX = rounded(event.deltaX);
      data.deltaY = rounded(event.deltaY);
      data.deltaZ = rounded(event.deltaZ);
      data.deltaMode = event.deltaMode;
    }
    if (event.type === "scroll") {
      data.scrollX = scrollX;
      data.scrollY = scrollY;
    }
    if (event.type === "visibilitychange") data.visibilityState = document.visibilityState;
    if (event.type === "keydown" || event.type === "keyup") {
      const key = event.key || "";
      data.category = key.length === 1 ? "printable" : (/^(Arrow|Page|Home|End)/.test(key) ? "navigation" : "control");
      data.codeFamily = String(event.code || "").replace(/\d+$/, "#").replace(/(Key|Digit).*/, "$1");
      data.repeat = event.repeat;
      data.composing = event.isComposing;
      data.alt = event.altKey;
      data.ctrl = event.ctrlKey;
      data.meta = event.metaKey;
      data.shift = event.shiftKey;
    }
    if (event.type === "focus" || event.type === "blur") {
      data.target = event.target && event.target.id ? event.target.id : event.target === window ? "window" : "other";
    }
    return data;
  };
})();
