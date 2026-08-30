import { ArrowRight, Github } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ProjectFooter } from "@/components/organisms/project-footer";
import { ProjectHeader } from "@/components/organisms/project-header";
import { copy, localeOf, locales } from "@/lib/i18n";

const stages = {
  ru: [["СБОР", "Telegram · карьерные сайты · RSS · API"], ["ОЧИСТКА", "SanitizeNode всегда первым"], ["ПОНИМАНИЕ", "Извлечение · нормализация · дедупликация"], ["РЕШЕНИЕ", "EvidenceDecisionNode — единая граница решения"]],
  en: [["INGEST", "Telegram · career sites · RSS · API"], ["SANITIZE", "SanitizeNode is always first"], ["UNDERSTAND", "Extraction · normalization · deduplication"], ["DECIDE", "EvidenceDecisionNode is the single decision boundary"]],
};

export default async function Home({ params }: { params: Promise<{ locale: string }> }) {
  const value = (await params).locale; if (!locales.includes(value as never)) notFound();
  const locale = localeOf(value); const t = copy[locale]; const ru = locale === "ru";
  return <><ProjectHeader locale={locale} /><main id="main" className="page-wrap">
    <section className="project-hero"><div className="wordmark-frame"><div className="wordmark">JOB_FTCH</div><p>VACANCY · SIGNAL · PIPELINE</p></div><h1 lang={locale}><span>{t.hero.titleAccent}</span> {t.hero.title}</h1><p className="hero-copy">{t.hero.body}</p><div className="hero-actions"><a className="button button-primary" href="/docs">{t.hero.docs} <ArrowRight size={18} /></a><a className="button button-secondary" href="https://github.com/letya999/job_ftch" target="_blank" rel="noreferrer"><Github size={17} /> {t.hero.github}</a></div><div className="terminal-window" aria-label={ru ? "Прохождение вакансии через pipeline" : "Vacancy pipeline run"}><div className="terminal-bar"><span /><span /><span /><b>~/dev/job_ftch</b></div><div className="terminal-lines"><p><i>❯</i> job-ftch run --tenant <strong>ai_jobs</strong></p><p><em>✓</em> source observations collected</p><p><em>✓</em> sanitized · extracted · normalized</p><p><em>✓</em> evidence decision completed</p><p><em>✓</em> published to <strong>telegram://ai_engineer_jobs</strong></p></div></div></section>
    <section className="track-section"><div className="track-marker"><span />{t.sections.project}</div><div className="track-body split-copy"><h2>{t.sections.projectTitle}<br /><span>{t.sections.projectSubtitle}</span></h2><p>{t.sections.projectBody}</p></div></section>
    <section className="track-section"><div className="track-marker"><span />{t.sections.pipeline}</div><div className="track-body grid-box pipeline-grid">{stages[locale].map(([name, body]) => <div key={name}><strong>{name}</strong><p>{body}</p></div>)}</div></section>
    <section className="track-section"><div className="track-marker"><span />{t.sections.architecture}</div><div className="track-body architecture-flow"><div className="arch-adapters"><small>{ru ? "ВХОДНЫЕ АДАПТЕРЫ" : "INBOUND ADAPTERS"}</small><b>Telegram · Career sites · RSS · API · MCP</b></div><span className="arch-arrow">↓</span><div className="arch-port"><small>{ru ? "ВХОДНЫЕ ПОРТЫ" : "INBOUND PORTS"}</small><b>Source · Runtime commands · Tenant configuration</b></div><span className="arch-arrow">↓</span><div className="arch-core"><small>{ru ? "НЕЗАВИСИМОЕ ЯДРО" : "INDEPENDENT CORE"}</small><strong>Domain</strong><b>RawItem → JobRecord</b><strong>Application</strong><b>Pipeline · TenantRunner · EvidenceDecision</b></div><span className="arch-arrow">↓</span><div className="arch-port"><small>{ru ? "ВЫХОДНЫЕ ПОРТЫ" : "OUTBOUND PORTS"}</small><b>Store · Sink · LLM · Search · Vector</b></div><span className="arch-arrow">↓</span><div className="arch-adapters"><small>{ru ? "ВЫХОДНЫЕ АДАПТЕРЫ" : "OUTBOUND ADAPTERS"}</small><b>Telegram bot · MCP · SQLite/Postgres · Qdrant</b></div></div></section>
    <section className="track-section"><div className="track-marker"><span />{t.sections.example}</div><div className="track-body action-table"><Link href={`/${locale}/jobs`}><span>{t.sections.jobs}</span><b>{t.sections.jobsBody}</b><ArrowRight size={18} /></Link><Link href={`/${locale}/sources`}><span>{t.sections.sources}</span><b>{t.sections.sourcesBody}</b><ArrowRight size={18} /></Link></div></section>
  </main><ProjectFooter locale={locale} /></>;
}
