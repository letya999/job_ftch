"use client";

import { useEffect, useState } from "react";
import { CONSENT_STORAGE_KEY, type Consent } from "./cookie-consent";

export function ConsentedAnalytics() {
  const [consent, setConsent] = useState<Consent | null>(null);
  useEffect(() => {
    const read = () => { try { setConsent(JSON.parse(localStorage.getItem(CONSENT_STORAGE_KEY) || "null")); } catch { setConsent(null); } };
    read(); window.addEventListener("job-ftch-consent", read); return () => window.removeEventListener("job-ftch-consent", read);
  }, []);
  useEffect(() => {
    if (!consent?.analytics || process.env.NEXT_PUBLIC_ANALYTICS_ENABLED === "false") return;
    const ga = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID?.trim();
    const ym = process.env.NEXT_PUBLIC_YANDEX_METRIKA_COUNTER_ID?.trim();
    if (ga) { const script = document.createElement("script"); script.async = true; script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(ga)}`; document.head.appendChild(script); const inline = document.createElement("script"); inline.text = `window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}gtag('js',new Date());gtag('config','${ga}')`; document.head.appendChild(inline); }
    if (ym && /^\d+$/.test(ym)) { const script = document.createElement("script"); script.async = true; script.src = "https://mc.yandex.ru/metrika/tag.js"; document.head.appendChild(script); const inline = document.createElement("script"); inline.text = `window.ym=window.ym||function(){(window.ym.a=window.ym.a||[]).push(arguments)};window.ym(${ym},'init',{clickmap:true,trackLinks:true,accurateTrackBounce:true,webvisor:false})`; document.head.appendChild(inline); }
  }, [consent]);
  return null;
}
