"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

export const CONSENT_STORAGE_KEY = "job_ftch_consent";
export type Consent = { analytics: boolean; marketing: boolean };

export function CookieConsent() {
  const locale = usePathname().startsWith("/en") ? "en" : "ru";
  const ru = locale === "ru";
  const [visible, setVisible] = useState(false);
  const [analytics, setAnalytics] = useState(false);
  useEffect(() => setVisible(!localStorage.getItem(CONSENT_STORAGE_KEY)), []);
  function persist(value: Consent) {
    localStorage.setItem(CONSENT_STORAGE_KEY, JSON.stringify(value));
    window.dispatchEvent(new CustomEvent("job-ftch-consent", { detail: value }));
    setVisible(false);
  }
  if (!visible) return null;
  return <section className="consent-banner" role="dialog" aria-modal="false" aria-labelledby="consent-title">
    <div><h2 id="consent-title">{ru ? "Настройки приватности." : "Privacy settings."}</h2><p>{ru ? "Необходимые данные работают всегда. Аналитика включается только после вашего выбора." : "Essential storage is always active. Analytics starts only after your choice."} <a href={`/${locale}/legal/cookies`}>{ru ? "Подробнее" : "Learn more"}</a></p><label><input type="checkbox" checked={analytics} onChange={(event) => setAnalytics(event.target.checked)} /> {ru ? "разрешить аналитику" : "allow analytics"}</label></div>
    <div className="consent-actions"><button className="button button-primary" onClick={() => persist({ analytics: true, marketing: true })}>{ru ? "Принять всё" : "Accept all"}</button><button className="button button-secondary" onClick={() => persist({ analytics, marketing: false })}>{ru ? "Сохранить выбор" : "Save choice"}</button><button className="button button-secondary" onClick={() => persist({ analytics: false, marketing: false })}>{ru ? "Только необходимые" : "Essential only"}</button></div>
  </section>;
}
