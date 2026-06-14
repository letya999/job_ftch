# AuthProvider (Провайдер учётных данных)

## Что это такое

AuthProvider — это изолированный слой абстракции (Protocol),
единственная задача которого звучит так: «Дай мне учётные
данные (credentials) для источника X».
Он принимает строковый идентификатор и возвращает словарь с
секретами: ключами API, токенами, паролями. 

## Зачем это нужно и ПОЧЕМУ так устроено

В мультитенантной системе (где система обслуживает несколько
независимых клиентов/пользователей) разные клиенты могут
использовать разные аккаунты.
Например, Тенант А использует один Telegram-аккаунт для
парсинга, а Тенант Б — другой.
Захардкодить все ключи в одном файле `.env` становится
невозможно.

Ещё более важная причина — безопасность.
Конфигурация источников (`SourceSpec`) хранится в обычных
YAML-файлах или базе данных.
Если записывать токены прямо в YAML, это приведёт к утечке
секретов при коммите в Git или дампе базы данных.
AuthProvider разделяет *декларацию* (какой аккаунт нужен) и
*разрешение* (какие у него реальные токены).

Ключевой принцип архитектуры: **Секреты НИКОГДА не попадают
в `SourceSpec` или YAML-конфиги.** YAML можно смело пушить в
публичный репозиторий.

## Как это работает изнутри

Protocol очень прост, он определен в `contracts.py`:

```python from typing import Protocol, runtime_checkable

@runtime_checkable class AuthProvider(Protocol):
    def resolve(self, source_id: str) -> dict[str, str]:
        """Resolve credentials for a source by its auth_source_id."""
```

В конфигурации `SourceSpec` мы не пишем ключи, мы пишем
только ссылку `auth_source_id`:

```yaml
# config.yaml (можно коммитить в git)
sources:
  - type: telegram_channel
    entity: ai_jobs_channel
    auth_source_id: telegram_account_1  # Это просто ключ!
```

В рантайме `Source` просит `AuthProvider` разрешить этот
ключ:

```python credentials = auth_provider.resolve("telegram_account_1")
# -> {"api_id": "12345", "api_hash": "abc...", "phone": "+7900..."}
```

## Встроенные реализации

В проекте есть несколько реализаций `AuthProvider` в
зависимости от потребностей развёртывания.

1. `EnvAuthProvider`

Читает секреты напрямую из переменных окружения (или `.env`
файла).
Самый простой вариант для self-hosted инсталляций. 
Он ищет переменные по шаблону
`JOB_FTCH_{SOURCE_ID}_{CREDENTIAL}`.
Например:
```bash
JOB_FTCH_TELEGRAM_ACCOUNT_1_API_ID=12345
JOB_FTCH_TELEGRAM_ACCOUNT_1_API_HASH=abc...

# Глобальные fallback переменные
JOB_FTCH_OPENAI_API_KEY=sk-...
```

2. `FileAuthProvider`

Читает секреты из локального YAML или JSON файла, который
обязательно должен быть добавлен в `.gitignore`.
Идеально подходит, когда нужно быстро настроить десятки
разных аккаунтов (например, прокси или Telegram-клиентов) и
переменные окружения становятся неудобными.

3. `VaultAuthProvider`

Продвинутая интеграция с HashiCorp Vault.
Предназначена для enterprise-сценариев.
Главный плюс: ключи могут автоматически ротироваться
Vault-ом, и при следующем вызове `resolve()` система получит
новые ключи без перезагрузки сервера.

## Как написать свою реализацию

Если вы используете облачный менеджер секретов (AWS Secrets
Manager, GCP Secret Manager), вы легко можете написать свой
провайдер:

```python from job_ftch.application.contracts import AuthProvider import boto3

class AWSSecretProvider(AuthProvider):
    def __init__(self):
        self.client = boto3.client('secretsmanager')

    def resolve(self, source_id: str) -> dict[str, str]:
        try:
            response = self.client.get_secret_value(SecretId=source_id)
            import json
            return json.loads(response['SecretString'])
        except Exception as e:
            # Важно: если ключ не найден, провайдер должен выбросить ошибку
            raise ValueError(f"Credentials not found for {source_id}") from e
```

## Типичные ошибки и что нельзя делать

1. **Писать пароли в SourceSpec.**

Никогда не добавляйте поле `api_key` напрямую в
`TelegramChannelSpec` или `CareerSiteSpec`.
Используйте `auth_source_id`.

2. **Загружать AuthProvider внутрь PipelineNode.**

Токены должны разрешаться на уровне Источников (Source) при
инициализации соединения, или в адаптерах.
Узлам обработки (Stage) секреты почти никогда не нужны,
кроме как для вызова LLM.

3. **Логировать результат resolve().**

Если вы поставите `print(auth.resolve(id))`, вы
скомпрометируете токены в логах (CloudWatch, DataDog).
Провайдер возвращает сырые чувствительные данные.

## Связи с другими сущностями

- [SourceSpec](source_spec.md) — содержит поле `auth_source_id`, которое служит ключом для AuthProvider.

- [Source](source.md) — запрашивает реальные ключи у провайдера перед стартом `fetch()`.

- [LLMProvider](llm_provider.md) — также часто требует API ключи, которые предоставляет AuthProvider.
