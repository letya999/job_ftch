"use client";

import { Github } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { ThemeToggle } from "@/components/atoms/theme-toggle";
import { copy, type Locale } from "@/lib/i18n";

export function ProjectHeader({ locale }: { locale: Locale }) {
  const t = copy[locale];
  const pathname = usePathname();
  const router = useRouter();
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  return <header className="top-nav"><div className="top-nav-inner"><Link className="top-brand" href={`/${locale}`}><img src="/brand/job-ftch-icon.png" alt="" /><b>JOB_FTCH</b></Link><nav aria-label={locale === "ru" ? "Основная навигация" : "Main navigation"}><Link href={`/${locale}/jobs`}>{t.nav.jobs}</Link><Link href={`/${locale}/sources`}>{t.nav.sources}</Link><a href="/docs">{t.nav.docs} ↗</a><label className="language-control"><span>{locale === "ru" ? "Язык" : "Language"}</span><select aria-label={locale === "ru" ? "Язык" : "Language"} value={locale} onChange={(event) => router.push(pathname.replace(/^\/(ru|en)/, `/${event.target.value}`))}><option value="ru">ru</option><option value="en">en</option></select></label><ThemeToggle /><a className="repo-link" href="https://github.com/letya999/job_ftch" target="_blank" rel="noreferrer" aria-label={locale === "ru" ? "Репозиторий job_ftch на GitHub" : "job_ftch repository on GitHub"}><Github size={20} /><span>letya999/job_ftch</span></a></nav></div></header>;
}
