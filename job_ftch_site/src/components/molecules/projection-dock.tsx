"use client";

import { usePathname } from "next/navigation";
import { copy, localeOf } from "@/lib/i18n";

export function ProjectionDock() {
  const pathname = usePathname();
  const parts = pathname.split("/").filter(Boolean);
  const locale = localeOf(parts[0] ?? "ru");
  const machine = parts[1] === "ai";
  const tail = machine ? parts.slice(2) : parts.slice(1);
  const humanHref = `/${locale}${tail.length ? `/${tail.join("/")}` : ""}`;
  const machineHref = `/${locale}/ai${tail.length ? `/${tail.join("/")}` : ""}`;
  const t = copy[locale].projection;
  return <aside className="projection-dock" aria-label={t.label}>
    <a className={!machine ? "active" : ""} aria-current={!machine ? "page" : undefined} href={humanHref}><i aria-hidden /> {t.human}</a>
    <a className={machine ? "active" : ""} aria-current={machine ? "page" : undefined} href={machineHref}><i aria-hidden /> {t.machine}</a>
  </aside>;
}
