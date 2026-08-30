import { notFound } from "next/navigation";
import { ProjectFooter } from "@/components/organisms/project-footer";
import { ProjectHeader } from "@/components/organisms/project-header";
import { SourceCatalog } from "@/components/organisms/source-catalog";
import { copy, localeOf, locales } from "@/lib/i18n";
export default async function SourcesPage({ params }: { params: Promise<{ locale: string }> }) { const value=(await params).locale; if(!locales.includes(value as never)) notFound(); const locale=localeOf(value); const t=copy[locale].catalog; return <><ProjectHeader locale={locale} /><main id="main" className="page-wrap catalog-page"><header className="catalog-intro"><p>PUBLIC REGISTRY / AI_JOBS</p><h1>{t.sourceTitle}</h1><div><p>{t.sourceBody}</p></div></header><SourceCatalog locale={locale} /></main><ProjectFooter locale={locale} /></>; }
