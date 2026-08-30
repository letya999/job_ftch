export const locales = ["ru", "en"] as const;
export type Locale = (typeof locales)[number];

export function localeOf(value: string): Locale {
  return value === "en" ? "en" : "ru";
}

export const copy = {
  ru: {
    nav: { jobs: "ВАКАНСИИ", sources: "ИСТОЧНИКИ", docs: "ДОКУМЕНТАЦИЯ" },
    hero: {
      titleAccent: "Open-source",
      title: "pipeline для любых вакансий.",
      body: "Собирает сигналы из Telegram, карьерных сайтов, RSS и API, превращает их в структурированные вакансии и публикует только подтверждённый результат.",
      docs: "Читать документацию",
      github: "Открыть GitHub",
    },
    sections: {
      project: "ПРОЕКТ",
      projectTitle: "Не очередной scraper.",
      projectSubtitle: "Инженерное ядро для разных вакансий, источников и выходов.",
      projectBody: "job_ftch — асинхронный library-first pipeline. Домен и application layer не зависят от Telegram, сайтов или конкретной БД. Новые источники, хранилища и выходы подключаются адаптерами без переписывания ядра.",
      pipeline: "PIPELINE",
      architecture: "ГЕКСАГОНАЛЬНАЯ АРХИТЕКТУРА",
      example: "ПРИМЕР РАБОТЫ",
      jobs: "ВАКАНСИИ",
      jobsBody: "Текущие вакансии AI-инженеров и AI-разработчиков",
      sources: "ИСТОЧНИКИ",
      sourcesBody: "Полный публичный реестр источников и их типов",
    },
    catalog: {
      jobsTitle: "Текущие вакансии.",
      jobsBody: "Актуальные структурированные карточки вакансий с датой, географией, зарплатой и ссылкой на первоисточник.",
      jobSearch: "роль, компания, стек или локация",
      sourceTitle: "Все текущие источники.",
      sourceBody: "Полный публичный реестр tenant ai_jobs: Telegram, карьерные сайты, RSS и API. Для каждого источника показаны название, описание, тип и полный URL.",
      sourceSearch: "название или тип источника",
      allTypes: "ВСЕ ТИПЫ",
      loadingJobs: "Загружаем вакансии…",
      loadingSources: "Загружаем реестр источников…",
      noJobs: "В каталоге пока нет вакансий.",
      noResults: "По этому запросу ничего не найдено.",
      errorJobs: "Каталог временно недоступен. Откройте Telegram-канал или повторите позже.",
      errorSources: "Реестр временно недоступен. Повторите позже.",
    },
    projection: { label: "Представление сайта", human: "ЧЕЛОВЕК", machine: "МАШИНА" },
  },
  en: {
    nav: { jobs: "VACANCIES", sources: "SOURCES", docs: "DOCUMENTATION" },
    hero: {
      titleAccent: "Open-source",
      title: "pipeline for any vacancy.",
      body: "Collects signals from Telegram, career sites, RSS and APIs, turns them into structured vacancies and publishes only confirmed results.",
      docs: "Read the docs",
      github: "Open GitHub",
    },
    sections: {
      project: "PROJECT",
      projectTitle: "Not another scraper.",
      projectSubtitle: "An engineering core for different vacancies, sources and outputs.",
      projectBody: "job_ftch is an async library-first pipeline. Its domain and application layers do not depend on Telegram, websites or a specific database. New sources, stores and outputs connect through adapters without rewriting the core.",
      pipeline: "PIPELINE",
      architecture: "HEXAGONAL ARCHITECTURE",
      example: "LIVE EXAMPLE",
      jobs: "VACANCIES",
      jobsBody: "Current AI engineering and AI development vacancies",
      sources: "SOURCES",
      sourcesBody: "The complete public source registry with types",
    },
    catalog: {
      jobsTitle: "Current vacancies.",
      jobsBody: "Current structured vacancy cards with dates, geography, compensation and direct source links.",
      jobSearch: "role, company, stack or location",
      sourceTitle: "All current sources.",
      sourceBody: "The complete public registry for tenant ai_jobs: Telegram, career sites, RSS and APIs, including name, description, type and full URL.",
      sourceSearch: "source name or type",
      allTypes: "ALL TYPES",
      loadingJobs: "Loading vacancies…",
      loadingSources: "Loading the source registry…",
      noJobs: "The catalog has no vacancies yet.",
      noResults: "No results match this query.",
      errorJobs: "The catalog is temporarily unavailable. Open Telegram or retry later.",
      errorSources: "The registry is temporarily unavailable. Retry later.",
    },
    projection: { label: "Site projection", human: "HUMAN", machine: "MACHINE" },
  },
} as const;
