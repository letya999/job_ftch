import { notFound } from "next/navigation";
import { JobCatalog, type Job } from "@/components/organisms/job-catalog";
import { ProjectFooter } from "@/components/organisms/project-footer";
import { ProjectHeader } from "@/components/organisms/project-header";
import { copy, localeOf, locales } from "@/lib/i18n";
import { readPublicData } from "@/lib/public-api";
export default async function JobsPage({ params }: { params: Promise<{ locale: string }> }) { const value=(await params).locale; if(!locales.includes(value as never)) notFound(); const locale=localeOf(value); const t=copy[locale].catalog; let jobs: Job[] = []; try { jobs = await readPublicData("/jobs") as Job[]; } catch {} return <><ProjectHeader locale={locale} /><main id="main" className="page-wrap catalog-page"><header className="catalog-intro"><p>LIVE EXAMPLE / AI_JOBS</p><h1>{t.jobsTitle}</h1><div><p>{t.jobsBody} <a href="https://t.me/ai_engineer_jobs" target="_blank" rel="noreferrer">@ai_engineer_jobs</a></p></div></header><JobCatalog locale={locale} initialJobs={jobs} /></main><ProjectFooter locale={locale} /></>; }
