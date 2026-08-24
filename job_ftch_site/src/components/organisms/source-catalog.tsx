"use client";

import { ExternalLink, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { copy, type Locale } from "@/lib/i18n";

type Source = { source_id: string; kind: string; public_name: string; display_name?: string; description?: string; public_url: string | null };

const kindLabels: Record<string, { ru: string; en: string }> = {
  career_site: { ru: "Карьерный сайт", en: "Career site" },
  telegram_channel: { ru: "Telegram-канал", en: "Telegram channel" },
  telegram_group: { ru: "Telegram-группа", en: "Telegram group" },
  rss: { ru: "RSS-лента", en: "RSS feed" },
  api: { ru: "API", en: "API" },
};

export function SourceCatalog({ locale }: { locale: Locale }) {
  const t = copy[locale].catalog;
  const [sources, setSources] = useState<Source[]>([]); const [query, setQuery] = useState(""); const [kind, setKind] = useState("all"); const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  useEffect(() => { fetch("/api/v1/sources").then(async (response) => { if (!response.ok) throw new Error("registry unavailable"); const payload = await response.json() as { data?: Source[] }; setSources(payload.data ?? []); setState("ready"); }).catch(() => setState("error")); }, []);
  const kinds = useMemo(() => Array.from(new Set(sources.map((source) => source.kind))).sort(), [sources]);
  const visible = useMemo(() => sources.filter((source) => (kind === "all" || source.kind === kind) && `${source.display_name} ${source.description} ${source.public_url} ${source.kind}`.toLowerCase().includes(query.toLowerCase())), [kind, query, sources]);
  const kindName = (value: string) => kindLabels[value]?.[locale] ?? value.replaceAll("_", " ");
  return <><div className="catalog-toolbar source-toolbar"><label><Search size={18} /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t.sourceSearch} aria-label={t.sourceSearch} /></label><select value={kind} onChange={(event) => setKind(event.target.value)} aria-label={t.allTypes}><option value="all">{t.allTypes}</option>{kinds.map((value) => <option key={value} value={value}>{kindName(value)}</option>)}</select><span>{visible.length} / {sources.length}</span></div>{state === "loading" && <p className="catalog-state">{t.loadingSources}</p>}{state === "error" && <p className="catalog-state error">{t.errorSources}</p>}<div className="source-list"><div className="source-row source-head"><span>{locale === "ru" ? "ИСТОЧНИК" : "SOURCE"}</span><span>{locale === "ru" ? "ОПИСАНИЕ" : "DESCRIPTION"}</span><span>{locale === "ru" ? "ТИП" : "TYPE"}</span><span>{locale === "ru" ? "URL" : "URL"}</span></div>{visible.map((source) => <article className="source-row" key={source.source_id}><span><b>{source.display_name || source.public_name || source.source_id}</b></span><span>{source.description || (locale === "ru" ? "Источник вакансий" : "Vacancy source")}</span><span>{kindName(source.kind)}</span><span>{source.public_url ? <a href={source.public_url} target="_blank" rel="noreferrer" aria-label={`${locale === "ru" ? "Открыть" : "Open"} ${source.display_name || source.public_name}`}><span>{source.public_url}</span><ExternalLink size={15} /></a> : "—"}</span></article>)}</div></>;
}
