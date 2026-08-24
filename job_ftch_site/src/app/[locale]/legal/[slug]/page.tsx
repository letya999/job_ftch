import { notFound } from "next/navigation";
import { ProjectFooter } from "@/components/organisms/project-footer";
import { ProjectHeader } from "@/components/organisms/project-header";
import { localeOf, locales } from "@/lib/i18n";
import { LEGAL_UPDATED_AT, LEGAL_VERSION, legalDocuments } from "@/lib/legal";

export default async function LegalPage({ params }: { params: Promise<{ locale: string; slug: string }> }) {
  const { locale: value, slug } = await params; if (!locales.includes(value as never)) notFound();
  const locale = localeOf(value); const document = legalDocuments[locale][slug]; if (!document) notFound();
  return <><ProjectHeader locale={locale} /><main className="legal-page legal-document-page page-wrap"><h1>{document.title}</h1><dl className="legal-metadata"><div><dt>{locale === "ru" ? "Версия" : "Version"}</dt><dd>{LEGAL_VERSION}</dd></div><div><dt>{locale === "ru" ? "Дата изменения" : "Last updated"}</dt><dd><time dateTime={LEGAL_UPDATED_AT}>{locale === "ru" ? "24 августа 2026 года" : "24 August 2026"}</time></dd></div></dl><p>{document.summary}</p>{document.sections.map((section) => <section key={section.title}><h2>{section.title}</h2>{section.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</section>)}<p className="legal-download"><a href={`/legal/${locale}/${slug}.pdf`} download>{locale === "ru" ? "Скачать документ (PDF)" : "Download document (PDF)"}</a></p></main><ProjectFooter locale={locale} /></>;
}
