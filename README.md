# YTM MCP

Небольшой read-only MCP-сервер для Яндекс Тег Менеджера.

Он получает данные через официальный YTM API, поддерживает несколько Яндекс-аккаунтов и сам выбирает токен с доступом к нужному контейнеру.

Доступные инструменты:

`list_containers`, `list_tags`, `get_tag`, `list_triggers`, `get_trigger`, `list_variables`, `get_variable`, `list_templates`, `search_entities`, `get_container_structure`.

## Запуск

Нужны Docker, Docker Compose, домен с HTTPS и OAuth-токен Яндекса с доступом `ytm:read`.

Заполните `.env`. Для каждого секрета используйте отдельное случайное значение:

```bash
openssl rand -hex 32
```

Аккаунты Яндекса задаются одной строкой. Основной аккаунт может быть только один:

```dotenv
YTM_ACCOUNTS_JSON=[{"name":"owner","token":"your-yandex-token","primary":true},{"name":"agency","token":"another-yandex-token","primary":false}]
```

YTM API не возвращает список всех контейнеров, поэтому известные ID нужно указать вручную:

```dotenv
YTM_CONTAINER_IDS=123456,789012
```

Запустите сервер:

```bash
docker compose up -d --build
```

Проверить его можно так:

```bash
docker compose ps
curl https://ytm.example.com/health
```

## Подключение к ChatGPT

Создайте подключение со следующими параметрами:

- URL: `https://ytm.example.com/mcp`
- аутентификация: OAuth
- регистрация: пользовательский OAuth-клиент
- Client ID: значение `MCP_OAUTH_CLIENT_ID`
- Client Secret: значение `MCP_OAUTH_CLIENT_SECRET`
- метод аутентификации token endpoint: `client_secret_post`
- scope: `ytm:read`
- OIDC: выключен

Callback URL из ChatGPT скопируйте в `MCP_OAUTH_REDIRECT_URI`, затем выполните:

```bash
docker compose up -d --force-recreate
```

При первом подключении сервер попросит `MCP_API_KEY`.