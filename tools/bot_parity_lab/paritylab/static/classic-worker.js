"use strict";
importScripts(new URL("/static/worker-common.js", self.location.origin).toString());
self.onmessage = async event => {
  if (!event.data || event.data.type !== "probe") return;
  try {
    self.postMessage(await self.collectParityWorkerProbe());
  } catch (error) {
    self.postMessage({unavailable: true, error: String(error), stack: error && error.stack ? String(error.stack) : ""});
  }
};
