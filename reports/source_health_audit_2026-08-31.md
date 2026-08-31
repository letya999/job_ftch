# Аудит 87 production-источников за 5 дней

Окно: 2026-08-25 22:30 — 2026-08-30 22:30 UTC
(2026-08-26 01:30 — 2026-08-31 01:30 MSK).

Источники сверены по production registry, логам OpenObserve `job_ftch_bot`
и всем таблицам production PostgreSQL. Изменений на сервере не выполнялось.

## Итог

| Состояние | Количество |
|---|---:|
| Хорошо и стабильно дают данные | 32 |
| Дают данные, но работают нестабильно | 9 |
| Цикл успешен, но данных нет | 5 |
| Не работают | 25 |
| Отключены | 13 |
| Кандидаты, ещё не введены в работу | 3 |
| Всего | 87 |

Итого фактически дали данные за окно: **41/87**. Если считать только включённые
источники (`enabled` + `degraded`), данные дали **41/71**. Строгий healthy-набор:
**32/87**.

## Хорошо работают — 32

- `career_site:avito_careers`
- `career_site:career_habr_com_companies_rwb_vacancies`
- `career_site:career_habr_com_vacancies`
- `career_site:careers_just_ai_com`
- `career_site:careers_kaspersky_ru_ru_vacancies`
- `career_site:cloud_ru_career_vacancies`
- `career_site:getmatch_ru_vacancies`
- `career_site:habr_career`
- `career_site:hirify_me`
- `career_site:job_mts_ru`
- `career_site:job_mts_ru_vacancies`
- `career_site:kazahstan_gorodrabot_kz`
- `career_site:rabota_sber_ru`
- `career_site:www_avito_ru_company_job_vacancies`
- `career_site:www_enbek_kz_ru_search_vacancy`
- `career_site:www_epam_com_careers_locations_kazakhstan`
- `career_site:yandex_jobs`
- `telegram_channel:aivacancychannel`
- `telegram_channel:careerstation_pm`
- `telegram_channel:datasciencejobs`
- `telegram_channel:forproducts`
- `telegram_channel:gleb_pro_ai`
- `telegram_channel:ml_jobs_kz`
- `telegram_channel:neurodromo`
- `telegram_channel:not_boring_ds_jobs`
- `telegram_channel:opento_data`
- `telegram_channel:rabota_v_ii`
- `telegram_channel:remote_ai_jobs`
- `telegram_channel:vakansii_ai`
- `telegram_channel:workitkz`
- `telegram_group:ai_engineers_guild`
- `telegram_group:betterdatacommunity`

## Дают данные, но нестабильны — 9

- `career_site:hh_ru` — 267 snapshot; 4 monitor failure, 2 exhausted runs.
- `career_site:hirehi_ru` — 2 791 snapshot; один access-denied.
- `career_site:job_megafon_ru` — 159 snapshot; 112 access-denied на detail,
  ещё 97 browser access-denied.
- `career_site:rabota_kz` — 887 snapshot; 77 detail scrape failure,
  27 all-scrapers-failed.
- `career_site:career_forte_kz_ru` — только 21 snapshot в одном run,
  затем 3 monitor failure.
- `career_site:hh_kz` — 353 snapshot, но текущий registry status `degraded`.
- `career_site:hr_tochka_com_vacancies_it` — 62 snapshot в 4 run,
  10 monitor failure и 5 exhausted runs.
- `career_site:kolesa_group` — 48 snapshot, 2 monitor failure,
  текущий status `degraded`.
- `career_site:vk_careers` — 527 snapshot; 45 detail scrape failure,
  9 all-scrapers-failed, текущий status `degraded`.

## Формально работают, но ничего не дали — 5

- `career_site:btsdigital_kz_ru_career`
- `career_site:plata_careers_vacancy`
- `career_site:www_bcc_kz_career_vacancies` — 30 `fetch_complete`, но 0 snapshot.
- `telegram_channel:llm_jobs`
- `telegram_channel:n8njobs_ru`

Эти источники нельзя считать качественно работающими до подтверждения, что
нулевой результат действительно соответствует пустой выдаче.

## Не работают — 25

- `career_site:astanahub_com_vacancy`
- `career_site:career_ozon_ru`
- `career_site:career_x5_tech`
- `career_site:careers_higgsfield_kz`
- `career_site:careers_indrive_com_vacancies`
- `career_site:careers_t2_ru`
- `career_site:company_rt_ru_career_vacancy`
- `career_site:documentolog_com_en_vacancies`
- `career_site:halykbank_kz`
- `career_site:job_2gis_ru_vacancies`
- `career_site:job_alfabank_ru_vacancies_digital`
- `career_site:job_beeline_ru_vacancies`
- `career_site:job_kaspi_kz_search`
- `career_site:job_tele2_kz`
- `career_site:jobs_kcell_kz`
- `career_site:jumys_kaspi_kz_rabota_vakansii`
- `career_site:people_beeline_kz`
- `career_site:qyzmet_kz`
- `career_site:rabota_vtb_ru_career_it`
- `career_site:sber_rabota_ru`
- `career_site:superjob_ru`
- `career_site:tbank_it`
- `career_site:trudvsem_ru_vacancy_search`
- `career_site:www_gazprombank_tech_vacancies`
- `career_site:www_superjob_ru_vakansii`

У всех status `degraded`, причина `source_fetch_failed`, и нет snapshot за окно.
У `careers_higgsfield_kz` отдельно: 65 detail access-denied, 42 browser
access-denied и 12 `captcha_provider_blocked_unauthorized`.

## Отключены — 13

- `career_site:airi_net_ru_hr`
- `career_site:geekjob_ru_vacancies`
- `career_site:habr_career_ml`
- `career_site:hh_kz_ml`
- `career_site:hh_kz_search_vacancy`
- `career_site:hh_ru_ml`
- `career_site:hh_ru_search_vacancy`
- `career_site:kolesa_group_career_job`
- `career_site:superjob_ru_ml`
- `career_site:team_vk_company_vacancy`
- `career_site:www_tbank_ru_career_vacancies_it`
- `career_site:yandex_jobs_ml`
- `career_site:yandex_ru_jobs_vacancies`

## Кандидаты — 3

- `career_site:geekjob_ru`
- `telegram_channel:ai_vacancy`
- `telegram_channel:itjobsfeed`

В registry они ещё `candidate`, хотя в `jf_jobs` уже есть записи со старыми
вариантами имён `AI_vacancy` и `ITjobsFeed`. Это drift идентификаторов.

## OpenObserve: главные аномалии

- 30 завершённых tenant run за 5 дней — scheduler работает примерно раз в 4 часа.
- 22 067 `pipeline_item_dropped` — очень большой отсев; нужен отдельный разрез
  по причинам, чтобы отличить нормальную нерелевантность от потерь pipeline.
- 191 `monitor_run_failed`, 79 `career_site_fetch_exhausted_all_monitors`,
  232 transport error.
- 178 detail access-denied + 139 browser detail access-denied.
- 532 `hh.captcha_detected_detail`.
- 1 234 browser channel fallback и 358 stale driver reaped — заметный churn
  браузерного runtime.
- 113 incomplete snapshot были корректно не закоммичены; защита от удаления
  хороших предыдущих данных работает.
- 93 source drift detected.
- 16 extraction LLM failure и 6 relevance LLM failure.
- 5 source auto-paused и 5 source eviction-paused.

## PostgreSQL: полный инвентарный срез

- `jf_observations`: 75 541
- `jf_source_snapshots`: 42 924, из них 20 024 за окно, 55 source ID за всё время
- `jf_jobs`: 842; последняя запись обновлена 2026-08-30 20:12 UTC
- `jf_job_groups`: 712
- `jf_job_group_urls`: 789
- `jf_job_group_fingerprints`: 938
- `jf_dedup_claims`: 3 820
- `jf_source_assessments`: 208
- `jf_source_ingest_state`: 149; последнее обновление 2026-08-25 13:24 UTC
- `jf_kv`: 110 740
- `jf_set`: 116 536
- `jf_outbox`: 0
- `jf_migrations`: 0
- все 15 ontology-таблиц: 0

Проблемы данных:

1. Snapshot amplification: 20 024 строк за 5 дней. Несколько источников почти
   каждый run сохраняют полный лимит: Habr 3 000, Getmatch 2 984, HireHi 2 791,
   Sber 1 500, EPAM 1 450. Это похоже на повторное сохранение полного snapshot,
   а не только изменений.
2. Дубли источников: `job_mts_ru` и `job_mts_ru_vacancies` дали по 750 snapshot;
   `avito_careers` и `www_avito_ru_company_job_vacancies` также перекрываются;
   `habr_career` и `career_habr_com_vacancies` требуют проверки границ.
3. В `jf_jobs.source_name` осталось много legacy ID (`ru_hirify`, `ru_superjob`,
   `ru_avito`, `multi_djinni`, `AI_vacancy`, `ITjobsFeed`), не совпадающих с
   canonical registry. Из-за этого статистика jobs по текущим source ID неточна.
4. `jf_source_ingest_state` не обновлялся после 25 августа, хотя runs и snapshots
   продолжаются до 30 августа. Возможен stale state repository или обновляется
   только bootstrap-state.
5. `jf_migrations = 0` при существующей схеме; миграции либо не регистрируются,
   либо таблица не используется.
6. Все ontology-таблицы пустые, несмотря на рабочий production graph. Возможно,
   production использует другой ontology backend; это нужно явно подтвердить.
