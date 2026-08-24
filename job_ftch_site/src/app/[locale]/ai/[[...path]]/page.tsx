import { notFound } from "next/navigation";
import { MachineControls } from "@/components/molecules/machine-controls";
import { localeOf, locales } from "@/lib/i18n";
import { readPublicData } from "@/lib/public-api";

function MdLink({ label, href }: { label: string; href: string }) { return <a href={href}>[{label}]({href})</a>; }

export default async function MachinePage({ params }: { params: Promise<{ locale: string; path?: string[] }> }) {
  const { locale: value, path = [] } = await params; if (!locales.includes(value as never)) notFound();
  const locale = localeOf(value); const ru = locale === "ru"; const page = path[0] ?? "home";
  let content: React.ReactNode;
  if (page === "jobs") { const jobs = await readPublicData("/jobs") as Array<Record<string, unknown>>; content = <><h1># {ru ? "Текущие вакансии" : "Current vacancies"}</h1><p>{ru ? "Актуальные структурированные карточки со ссылками на первоисточники." : "Current structured vacancy cards with direct source links."}</p>{jobs.map((job, index) => <section key={String(job.id ?? index)}><h2>## {String(job.title ?? (ru ? "Без названия" : "Untitled"))}</h2><p>- company: {String(job.company ?? "")}</p><p>- location: {String(job.location ?? "")}</p><p>- source: {String(job.source ?? "")}</p>{job.url ? <MdLink label={ru ? "Открыть вакансию" : "Open vacancy"} href={String(job.url)} /> : null}</section>)}</>;
  } else if (page === "sources") { const sources = await readPublicData("/sources") as Array<Record<string, unknown>>; content = <><h1># {ru ? "Источники" : "Sources"}</h1><p>{ru ? "Полный публичный реестр источников." : "Complete public source registry."}</p>{sources.map((source, index) => <section key={String(source.source_id ?? index)}><h2>## {String(source.display_name ?? source.public_name ?? source.source_id)}</h2><p>- type: {String(source.kind)}</p><p>- description: {String(source.description ?? "")}</p><p>- url: {String(source.public_url ?? "")}</p></section>)}</>;
  } else if (page === "home") { content = <><h1># job_ftch — {ru ? "pipeline для любых вакансий" : "pipeline for any vacancy"}</h1><p>{ru ? "Собирает, очищает, нормализует, проверяет и публикует вакансии из разных источников." : "Collects, sanitizes, normalizes, validates and publishes vacancies from multiple sources."}</p><MdLink label={ru ? "Вакансии" : "Vacancies"} href={`/${locale}/ai/jobs`} /><MdLink label={ru ? "Источники" : "Sources"} href={`/${locale}/ai/sources`} /><h2>## API</h2><pre>{`GET /api/v1/jobs?limit=20\nGET /api/v1/sources\nGET /api/health`}</pre></>;
  } else notFound();
  return <div className="machine-shell"><header className="machine-header"><span>$</span><MdLink label="job_ftch" href={`/${locale}/ai`} /><MdLink label={ru ? "Вакансии" : "Vacancies"} href={`/${locale}/ai/jobs`} /><MdLink label={ru ? "Источники" : "Sources"} href={`/${locale}/ai/sources`} /><MdLink label={ru ? "Документация" : "Documentation"} href="/docs" /><MdLink label="llms.txt" href="/llms.txt" /><MachineControls locale={locale} /></header><main id="main" className="machine-document">{content}</main><footer className="machine-footer">© 2026 job_ftch · {ru ? "машинная проекция" : "machine projection"}</footer></div>;
}
