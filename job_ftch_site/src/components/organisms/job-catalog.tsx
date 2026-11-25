"use client";

import { ArrowUpRight, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { copy, type Locale } from "@/lib/i18n";

export type Compensation = { currency: string; min_amount?: number; max_amount?: number; period?: string; gross?: boolean };
export type Job = { id: string; title: string; company: string; location: string; workMode: string; seniority: string; profile?: string; roleFamily?: string; country?: string; city?: string; postingLanguages?: string[]; publicationTags?: string[]; tags: string[]; source: string; postedAt: string; compensation?: Compensation; url: string };
const PAGE_SIZE = 20;
const facets = [
  ["seniority", "Грейд", "Grade"], ["profile", "Профиль", "Role"],
  ["country", "Страна", "Country"], ["city", "Город", "City"],
  ["postingLanguages", "Язык объявления", "Posting language"], ["workMode", "Формат", "Work mode"],
] as const;
type Facet = (typeof facets)[number][0];
const russianFacetLabels: Record<string, string> = {
  developer: "Разработчик", analyst: "Аналитик", manager: "Менеджер", data_ml: "Данные и ML",
  architect: "Архитектор", security: "Безопасность", engineer: "Инженер", designer: "Дизайнер",
  devops: "DevOps", qa: "Тестирование", research: "Исследования", legal: "Юрист",
  operations: "Операции", product: "Продукт", onsite: "Офис", hybrid: "Гибрид", remote: "Удалённо",
  ru: "Русский", en: "Английский", kk: "Казахский", uk: "Украинский", uz: "Узбекский",
};
function values(job: Job, facet: Facet): string[] {
  const value = job[facet];
  return Array.isArray(value) ? value : value && value !== "unknown" ? [value] : [];
}

function salary(value: Compensation | undefined, locale: Locale) {
  if (!value) return locale === "ru" ? "Зарплата не указана" : "Salary not specified";
  const money = new Intl.NumberFormat(locale === "ru" ? "ru-RU" : "en-US", { style: "currency", currency: value.currency, maximumFractionDigits: 0 });
  const range = value.min_amount && value.max_amount ? `${money.format(value.min_amount)}–${money.format(value.max_amount)}` : value.min_amount ? `${locale === "ru" ? "от" : "from"} ${money.format(value.min_amount)}` : `${locale === "ru" ? "до" : "up to"} ${money.format(value.max_amount || 0)}`;
  const periods: Record<string, string> = { month: locale === "ru" ? "в месяц" : "per month", year: locale === "ru" ? "в год" : "per year", hour: locale === "ru" ? "в час" : "per hour", week: locale === "ru" ? "в неделю" : "per week", day: locale === "ru" ? "в день" : "per day", project: locale === "ru" ? "за проект" : "per project" };
  return `${range}${periods[value.period || ""] ? ` · ${periods[value.period || ""]}` : ""}${value.gross === true ? ` · ${locale === "ru" ? "до налогов" : "gross"}` : ""}`;
}

export function JobCatalog({ locale, initialJobs }: { locale: Locale; initialJobs: Job[] }) {
  const t = copy[locale].catalog;
  const jobs = initialJobs; const [query, setQuery] = useState(""); const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Partial<Record<Facet, string>>>({});
  const options = useMemo(() => Object.fromEntries(facets.map(([key]) => [
    key, [...new Set(jobs.flatMap((job) => values(job, key)))].sort((a, b) => a.localeCompare(b, locale)),
  ])) as Record<Facet, string[]>, [jobs, locale]);
  const visible = useMemo(() => jobs.filter((job) =>
    facets.every(([key]) => !filters[key] || values(job, key).includes(filters[key]!)) &&
    Object.values(job).flat().join(" ").toLowerCase().includes(query.toLowerCase())
  ), [jobs, query, filters]);
  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  useEffect(() => { setPage(1); }, [query, filters]);
  useEffect(() => { if (page > pages) setPage(pages); }, [page, pages]);
  const pageJobs = visible.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  return <><div className="catalog-toolbar"><label><Search size={18} /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t.jobSearch} aria-label={t.jobSearch} /></label><span>{visible.length} / {jobs.length}</span></div>
    <div className="catalog-facets">{facets.map(([key, ru, en]) => <label key={key}>
      {locale === "ru" ? ru : en}
      <select value={filters[key] || ""} onChange={(event) => setFilters({...filters, [key]: event.target.value})}>
        <option value="">{locale === "ru" ? "Все" : "All"}</option>
        {options[key].map((value) => <option key={value} value={value}>{locale === "ru" ? russianFacetLabels[value] ?? value : value}</option>)}
      </select>
    </label>)}</div>
    {visible.length === 0 && <p className="catalog-state">{jobs.length ? t.noResults : t.noJobs}</p>}<div className="catalog-list">{pageJobs.map((job) => <a className="job-row" key={job.id || job.url} href={job.url || "https://t.me/ai_engineer_jobs"} target="_blank" rel="noreferrer"><div className="job-main"><h2>{job.title || (locale === "ru" ? "Без названия" : "Untitled")}</h2><p>{job.company || (locale === "ru" ? "Компания не указана" : "Company not specified")}</p></div><div className="job-facts"><span>{job.location || (job.workMode === "remote" ? (locale === "ru" ? "Удалённо" : "Remote") : (locale === "ru" ? "География не указана" : "Location not specified"))}</span><span>{salary(job.compensation, locale)}</span></div><div className="job-meta"><time dateTime={job.postedAt}>{job.postedAt ? new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "medium" }).format(new Date(job.postedAt)) : (locale === "ru" ? "Дата не указана" : "Date not specified")}</time><span>{job.publicationTags?.join(" ")}</span><span>{job.tags?.slice(0, 3).join(" · ") || job.source || "—"}</span></div><ArrowUpRight size={18} /></a>)}</div>{visible.length > PAGE_SIZE && <nav className="pagination" aria-label={locale === "ru" ? "Страницы вакансий" : "Vacancy pages"}><button disabled={page === 1} onClick={() => setPage(page - 1)} aria-label={locale === "ru" ? "Предыдущая страница" : "Previous page"}>←</button>{Array.from({ length: pages }, (_, index) => index + 1).map((value) => <button key={value} className={value === page ? "active" : ""} aria-current={value === page ? "page" : undefined} onClick={() => setPage(value)}>{value}</button>)}<button disabled={page === pages} onClick={() => setPage(page + 1)} aria-label={locale === "ru" ? "Следующая страница" : "Next page"}>→</button></nav>}</>;
}
