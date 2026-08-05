"use strict";
importScripts(new URL("/static/worker-common.js", self.location.origin).toString());
self.onconnect = event => {
  const port = event.ports[0];
  port.onmessage = async message => {
    if (!message.data || message.data.type !== "probe") return;
    try {
      port.postMessage(await self.collectParityWorkerProbe());
    } catch (error) {
      port.postMessage({unavailable: true, error: String(error), stack: error && error.stack ? String(error.stack) : ""});
    }
  };
  port.start();
};
