# Casebook + Bitrix24 Widget (3 микросервиса + PostgreSQL)

Проект поднимает:
- **updater-service** — отдельный backend для ежедневной загрузки данных из Casebook API (и ручного запуска).
- **api-service** — backend для виджета (JWT, Bitrix install/uninstall/widget, выдача дел и истории).
- **frontend-service** — UI виджета в светлой пастельной стилистике.
- **PostgreSQL** — общая база данных.

## Архитектурное решение

Я **разделил backend на 2 сервиса**:
1. `updater` — отвечает только за ETL/синхронизацию, планировщик и идемпотентную запись.
2. `api` — отвечает только за UI/API/Bitrix интеграцию и авторизацию.

Это лучше, чем смешивать, потому что:
- можно независимо масштабировать (загрузчик и API имеют разную нагрузку);
- падение загрузчика не роняет пользовательский API;
- проще сопровождать (SRP).

## Что именно сохраняется в БД

### Таблица `cases`
- `id` — UUID (внутренний).
- `external_case_id` — `eventData.case.id` (уникально).
- `case_number` — `eventData.case.number`.

### Таблица `document_events`
- `id` — UUID.
- `case_id` — FK на `cases`.
- `document_external_id` — `document.id`.
- `event_type` — `added` или `edit`.
- `find_date` — `findDate`.
- `actual_date` — `document.actualDate`.
- `raw_item` — исходный JSON `items[n]`.
- `source_hash` — уникальный хэш для идемпотентности.

### Таблица `content_types` (one-to-many)
- `id` — UUID.
- `event_id` — FK на событие (через событие связан с делом).
- `content_type_external_id` — `contentTypes[].id`.
- `name` — склейка `document.type.name + ' : ' + contentTypes[].name`.

Пример: `Ходатайства : Ходатайство об ознакомлении с материалами дела`.

## Идемпотентность (без дублей)

Для каждого `item` считается `source_hash` по стабильным ключам:
- case_id,
- document_id,
- findDate,
- actualDate,
- eventType.

Если запись с таким `source_hash` уже есть:
- событие обновляется (raw/event/dates),
- `content_types` пересобираются для этого события,
- новая дублирующая строка **не создается**.

## Пагинация Casebook API

`updater` ходит по API до конца:
- первая страница: `size=100&dateFrom=...&dateTo=...`
- следующие: добавляет `offset=<next>`.
- если `next == null` (или отсутствует), цикл завершён.
- последняя страница может быть пустой — это корректно.

## Планировщик

По умолчанию: **каждый день в 11:59 Europe/Moscow**.

Параметры:
- `SCHEDULER_HOUR_MSK`
- `SCHEDULER_MINUTE_MSK`

## Ручной запуск обновления

`POST http://localhost:8001/sync/manual`

Возвращает статистику:
- `fetched`
- `inserted`
- `updated`
- `skipped`

## Backend endpoints (Bitrix требования)

Публичные:
- `GET|POST /bitrix/install`
- `GET|POST /bitrix/uninstall`
- `GET|POST /bitrix/widget`
- `GET /health`
- `POST /auth/bitrix-auto`
- `POST /auth/local` (локальный dev-вход)

Защищённые (`Authorization: Bearer <token>`):
- `GET /cases`
- `GET /cases/{caseId}/history`
- `POST /bitrix/rest/{method}`
- `POST /bitrix/token/refresh`

## Bitrix REST интеграция

- REST: `https://{domain}/rest/{method}` через endpoint-прокси `/bitrix/rest/{method}`.
- Refresh: `https://{domain}/oauth/token/` через `/bitrix/token/refresh`.

## Frontend функционал

- Список дел, отсортированный по номеру.
- Поиск по номеру.
- Раскрытие строки дела с историей документов.
- История отсортирована по `findDate`, затем `actualDate`.
- Типы `added/edit` переведены на русский (`Добавлено/Изменено`).
- Отображение строки дела:
  - `Номер дела | ссылка на дело`
- Ссылка: `https://kad.arbitr.ru/Card/$caseId`.
- Авто-логин, если в URL есть `member_id` + `user_id`.
- JWT хранится в `localStorage`.
- Compact режим для `PLACEMENT=CRM_DEAL_DETAIL*`.
- Чтение `deal_id` из `PLACEMENT_OPTIONS`.

## Запуск локально (Docker Compose)

1. Создайте `.env` (пример):

```env
POSTGRES_DB=casebook
POSTGRES_USER=app
POSTGRES_PASSWORD=app
DATABASE_URL=postgresql+psycopg2://app:app@postgres:5432/casebook

CASEBOOK_API_URL=https://api3.casebook.ru/arbitrage/tracking/events/documents
CASEBOOK_API_KEY=rFPi5qOWLDofJ6N2o4CrpY8f4HpskDMC
CASEBOOK_API_VERSION=2
CASEBOOK_AUTH_SCHEME=auto
PAGE_SIZE=100
SCHEDULER_HOUR_MSK=11
SCHEDULER_MINUTE_MSK=59

JWT_SECRET=super-secret-change-me
JWT_ALGORITHM=HS256
JWT_EXP_MINUTES=720
ALLOW_LOCAL_DEV_AUTH=true

FRONTEND_URL=http://localhost:8080
FRONTEND_API_URL=http://localhost:8000
```

2. Запустите:

```bash
docker compose up --build
```

3. Проверка:
- Frontend: http://localhost:8080
- API health: http://localhost:8000/health
- Updater health: http://localhost:8001/health

## Как использовать как Bitrix24 embedded widget

1. В Bitrix app укажите backend URL для install/uninstall/widget.
2. Bitrix вызывает:
   - `/bitrix/install` при установке,
   - `/bitrix/uninstall` при удалении,
   - `/bitrix/widget` при открытии placement.
3. `/bitrix/widget` делает redirect на frontend, прокидывая query-параметры.
4. Frontend выполняет авто-логин через `/auth/bitrix-auto`, получает JWT и работает в iframe.

## Безопасность

- Все секреты только через env.
- Все закрытые ручки требуют Bearer JWT.
- Публичными оставлены install/uninstall/widget/health/auth.
- `POST /auth/local` используйте только в локальной разработке (можно отключить через `ALLOW_LOCAL_DEV_AUTH=false`).

## Структура проекта

```text
.
├─ docker-compose.yml
├─ services/
│  ├─ updater/
│  │  ├─ Dockerfile
│  │  ├─ requirements.txt
│  │  └─ app/
│  │     ├─ config.py
│  │     ├─ db.py
│  │     ├─ main.py
│  │     └─ sync_service.py
│  └─ api/
│     ├─ Dockerfile
│     ├─ requirements.txt
│     └─ app/
│        ├─ auth.py
│        ├─ config.py
│        ├─ db.py
│        └─ main.py
└─ frontend/
   ├─ Dockerfile
   ├─ app.js
   ├─ index.html
   ├─ styles.css
   ├─ nginx.conf
   └─ entrypoint.sh
```

## Полезные команды

Ручной sync:
```bash
curl -X POST http://localhost:8001/sync/manual
```

Если получаете `401 Unauthorized` от Casebook:
- проверьте `CASEBOOK_API_KEY`;
- попробуйте `CASEBOOK_AUTH_SCHEME=apikey` или `CASEBOOK_AUTH_SCHEME=bearer` в `.env`.

Получить token авто-логина:
```bash
curl -X POST http://localhost:8000/auth/bitrix-auto \
  -H 'Content-Type: application/json' \
  -d '{"member_id":"m1","user_id":"1","domain":"example.bitrix24.ru"}'
```

Список дел:
```bash
curl http://localhost:8000/cases -H 'Authorization: Bearer <TOKEN>'
```
