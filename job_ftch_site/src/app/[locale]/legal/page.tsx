import Link from "next/link";
import { notFound } from "next/navigation";
import { ProjectFooter } from "@/components/organisms/project-footer";
import { ProjectHeader } from "@/components/organisms/project-header";
import { localeOf, locales } from "@/lib/i18n";
import { legalDocuments } from "@/lib/legal";

export default async function LegalIndex({ params }: { params: Promise<{ locale: string }> }) { const value=(await params).locale; if(!locales.includes(value as never)) notFound(); const locale=localeOf(value); const documents=Object.entries(legalDocuments[locale]); return <><ProjectHeader locale={locale} /><main className="legal-page page-wrap"><p className="machine-kicker">job_ftch / legal</p><h1>{locale === "ru" ? "Правовые документы" : "Legal documents"}</h1><p className="legal-summary">{locale === "ru" ? "Каждый документ опубликован на отдельной странице и доступен для скачивания." : "Each document is published on its own page and is available to download."}</p><nav className="legal-index" aria-label={locale === "ru" ? "Содержание" : "Contents"}>{documents.map(([slug, document]) => <Link key={slug} href={`/${locale}/legal/${slug}`}><b>{document.title}</b><span>{document.summary}</span></Link>)}</nav></main><ProjectFooter locale={locale} /></>; }
