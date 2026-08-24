"use client";
import { usePathname, useRouter } from "next/navigation";
import { ThemeToggle } from "@/components/atoms/theme-toggle";
import { type Locale } from "@/lib/i18n";

export function MachineControls({ locale }: { locale: Locale }) { const pathname=usePathname(); const router=useRouter(); return <span className="machine-controls"><label>{locale === "ru" ? "язык" : "language"}: <select value={locale} onChange={(event) => router.push(pathname.replace(/^\/(ru|en)/, `/${event.target.value}`))}><option value="ru">ru</option><option value="en">en</option></select></label><ThemeToggle /></span>; }
