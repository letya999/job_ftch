# Plan: Create sources config for RU/KZ job boards and career sites

## Goal

Create a YAML config file for 20 specific RU/KZ job board and career site URLs
so the pipeline can extract job listings from them.

## File to CREATE: config/ai_job_boards_ru_kz.yaml

Content:

```yaml
sources:
  # --- Job boards / Habr Career ---
  - type: career_site
    url: https://career.habr.com/vacancies/data_scientist
    source_name: habr_ds
    monitor: dom
    limit: 50

  - type: career_site
    url: "https://career.habr.com/vacancies?skills[]=296"
    source_name: habr_ml_skill
    monitor: dom
    limit: 50

  # --- GetMatch ---
  - type: career_site
    url: https://getmatch.ru/vacancies/data-scientist-machine-learning
    source_name: getmatch_ds
    monitor: dom
    limit: 50

  # --- GeekJob ---
  - type: career_site
    url: https://geekjob.ru/vacancies
    source_name: geekjob
    monitor: dom
    limit: 50

  # --- Hirify ---
  - type: career_site
    url: https://hirify.me/jobs-in-russia
    source_name: hirify_russia
    monitor: dom
    limit: 50

  - type: career_site
    url: https://hirify.me/jobs-in-product-company
    source_name: hirify_product
    monitor: dom
    limit: 50

  # --- HH.ru ---
  - type: career_site
    url: "https://hh.ru/search/vacancy?text=machine+learning+engineer"
    source_name: hh_mle
    monitor: dom
    limit: 50

  - type: career_site
    url: "https://hh.ru/search/vacancy?text=LLM+engineer"
    source_name: hh_llm
    monitor: dom
    limit: 50

  # --- HH.kz ---
  - type: career_site
    url: https://hh.kz/vacancies/data-scientist
    source_name: hhkz_ds
    monitor: dom
    limit: 50

  - type: career_site
    url: https://hh.kz/vacancies/machine-learning-engineer
    source_name: hhkz_mle
    monitor: dom
    limit: 50

  # --- KZ job boards ---
  - type: career_site
    url: "https://qyzmet.kz/%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D0%B8/Data-scientist"
    source_name: qyzmet_ds
    monitor: dom
    limit: 50

  - type: career_site
    url: https://kazahstan.gorodrabot.kz/data_scientist
    source_name: gorodrabot_ds
    monitor: dom
    limit: 50

  - type: career_site
    url: "https://gderabota.ru/%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D0%B8/data-scientist"
    source_name: gderabota_ds
    monitor: dom
    limit: 50

  # --- Company career pages ---
  - type: career_site
    url: https://www.tbank.ru/career/it/vacancies/
    source_name: tbank_it
    monitor: auto
    limit: 50

  - type: career_site
    url: https://www.tbank.ru/career/it/ml/
    source_name: tbank_ml
    monitor: auto
    limit: 50

  - type: career_site
    url: https://career.avito.com/vacancies/
    source_name: avito_career
    monitor: auto
    limit: 50

  - type: career_site
    url: https://ozon.tech/vacancies/
    source_name: ozon_tech
    monitor: auto
    limit: 50

  - type: career_site
    url: https://h.careers/company/vk
    source_name: vk_careers
    monitor: dom
    limit: 50

  - type: career_site
    url: https://kolesa.group/career/job
    source_name: kolesa_career
    monitor: auto
    limit: 50

  - type: career_site
    url: https://people.beeline.kz/
    source_name: beeline_kz
    monitor: dom
    limit: 50
```

## Instructions

1. Read pyproject.toml and config/ directory first.
2. Create the file `config/ai_job_boards_ru_kz.yaml` with exactly the content above.
3. Note: Cyrillic URLs in qyzmet.kz and gderabota.ru are percent-encoded in the YAML.
   The original Cyrillic URLs from the user are:
   - qyzmet.kz: https://qyzmet.kz/вакансии/Data-scientist
   - gderabota.ru: https://gderabota.ru/вакансии/data-scientist
   Use the percent-encoded versions in the YAML for safety.
4. Do NOT modify any other files.
5. Do NOT run the pipeline.
