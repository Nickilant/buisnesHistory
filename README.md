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
CORS_ALLOW_ORIGINS=*

FRONTEND_URL=http://localhost:8080
FRONTEND_API_URL=/api
CASE_NUMBER_FIELDS=UF_CRM_1708426613594,UF_CRM_CASE_NUMBER
```

`CORS_ALLOW_ORIGINS` можно оставить `*` для локальной разработки. Для более строгого режима укажите список через запятую, например: `http://localhost:8080,http://127.0.0.1:8080`.

2. Запустите:

```bash
docker compose up --build
```

3. Проверка:
- Frontend: http://localhost:8080
- API health: http://localhost:8000/health
- Updater health: http://localhost:8001/health

## Как использовать как Bitrix24 embedded widget

Важно: в Bitrix URL запуска приложения указывайте backend-route `https://<ваш-домен>/api/bitrix/widget`, а не `https://<ваш-домен>/` (frontend). Иначе при POST-открытии Bitrix можно получить 405.

1. В Bitrix app укажите backend URL для install/uninstall/widget.
2. Bitrix вызывает:
   - `/bitrix/install` при установке,
   - `/bitrix/uninstall` при удалении,
   - `/bitrix/widget` при открытии placement.
3. `/bitrix/widget` делает redirect на frontend, прокидывая query-параметры.
4. Frontend выполняет авто-логин через `/auth/bitrix-auto`, получает JWT и работает в iframe.
5. Для кастомного поля номера дела задайте `CASE_NUMBER_FIELDS` (через запятую), например: `UF_CRM_1708426613594`.

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

## Развертывание на CentOS Stream 9 сервере (Docker + домен)

Ниже минимально-практичный сценарий для **CentOS Stream 9** с уже купленным доменом.

### 1) Подготовка DNS

У регистратора домена создайте записи:
- `A` запись `@` → `PUBLIC_SERVER_IP`
- `A` запись `www` → `PUBLIC_SERVER_IP` (опционально)

Проверка:
```bash
dig +short your-domain.com
dig +short www.your-domain.com
```

### 2) Подготовка сервера

```bash
sudo dnf -y update
sudo dnf -y install curl git ca-certificates dnf-plugins-core

# Docker Engine (официальный repo Docker)
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker

sudo usermod -aG docker $USER
newgrp docker

# Docker Compose plugin
docker compose version
```

### 3) Клонирование проекта

```bash
git clone <YOUR_REPO_URL> /opt/casebook
cd /opt/casebook
```

### 4) Продакшн `.env`

Создайте файл `.env` в корне проекта (важные отличия от локальной разработки):

```env
POSTGRES_DB=casebook
POSTGRES_USER=app
POSTGRES_PASSWORD=strong_password_here
DATABASE_URL=postgresql+psycopg2://app:strong_password_here@postgres:5432/casebook

CASEBOOK_API_URL=https://api3.casebook.ru/arbitrage/tracking/events/documents
CASEBOOK_API_KEY=YOUR_CASEBOOK_KEY
CASEBOOK_API_VERSION=2
CASEBOOK_AUTH_SCHEME=auto
PAGE_SIZE=100
SCHEDULER_HOUR_MSK=11
SCHEDULER_MINUTE_MSK=59

JWT_SECRET=very-long-random-secret
JWT_ALGORITHM=HS256
JWT_EXP_MINUTES=720
ALLOW_LOCAL_DEV_AUTH=false

CORS_ALLOW_ORIGINS=https://your-domain.com,https://www.your-domain.com
FRONTEND_URL=https://your-domain.com
FRONTEND_API_URL=/api
CASE_NUMBER_FIELDS=UF_CRM_1708426613594,UF_CRM_CASE_NUMBER
```

### 5) Публикация сервисов

```bash
docker compose up -d --build
```

Проверка контейнеров:
```bash
docker compose ps
docker compose logs -f api
```

### 6) Reverse proxy (Nginx) + HTTPS (Let's Encrypt)

Обычно в проде удобнее отдавать трафик так:
- `https://your-domain.com/` → `frontend-service:80`
- `https://your-domain.com/api/` → `api-service:8000`

Установите Nginx и Certbot на хосте:
```bash
sudo dnf -y install epel-release
sudo dnf -y install nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx
```


Если включен SELinux (по умолчанию в CentOS 9), разрешите Nginx проксировать на локальные порты Docker:
```bash
sudo setsebool -P httpd_can_network_connect 1
```

Пример конфига `/etc/nginx/conf.d/casebook.conf`:

```nginx
server {
    server_name your-domain.com www.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/ {
        rewrite ^/api/(.*)$ /$1 break;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активируйте сайт и выпустите сертификат:
```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

### 7) Firewall (firewalld)

```bash
sudo systemctl enable --now firewalld
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### 8) Обновления релиза

```bash
cd /opt/casebook
git pull
docker compose up -d --build
```

### 9) Что проверить после деплоя

- `https://your-domain.com` открывает UI.
- `https://your-domain.com/api/health` возвращает `{"status":"ok"}`.
- В `.env` обязательно `ALLOW_LOCAL_DEV_AUTH=false` в проде.
- В Bitrix app URL виджета указывать на backend route `/bitrix/widget` вашего домена.
- Если фронт всё равно стучится в `localhost:8000`, проверьте значение `FRONTEND_API_URL` в контейнере `frontend` и очистите кэш браузера (файл `/config.js` мог закэшироваться).
