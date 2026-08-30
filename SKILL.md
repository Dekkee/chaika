---
name: chaika
description: Личный кабинет клиники Чайка (chaika.com) — история визитов, протоколы, чеки, акты — и подача на возмещение ДМС в АльфаСтрахование (med.alfastrah.ru). Use when the user says "/chaika", "мои визиты", "скачай чеки/акты/протокол (от врача/из клиники)", "подай на возмещение", "подай в страховую / по ДМС", "возмещение за визит/приём", or mentions Чайка / chaika.com / АльфаСтрахование / alfastrah. Подкоманды visits | docs <дата> | details <entry-id> | refund.
argument-hint: [visits|docs <дата>|details <entry-id>|refund]
allowed-tools: Bash(curl:*), Bash(mkdir:*), Bash(python3:*), Bash(rm:*), Bash(find:*), Bash(ls:*), Bash(cat:*), Bash("/Applications/Google Chrome.app":*), Bash(google-chrome:*), Bash(google-chrome-stable:*), Bash(chromium:*), Bash(chromium-browser:*), Read, Write
---

# Клиника Чайка — парсинг личного кабинета

## Конфигурация

Cookies для авторизации хранятся в `~/.chaika_cookies`.
EHR ID хранится в `~/.chaika_ehr_id`.

Если файлы отсутствуют, попроси пользователя:

1. Залогиниться на chaika.com
2. Открыть DevTools → Network → скопировать любой запрос как cURL
3. Из cURL извлечь cookies (значения `remember` и `session`) и EHR ID (UUID из URL `/account/<ehr-id>/...`)

Затем сохрани:
```
echo 'bookingV4.tenant=chaika; app_locale=ru; remember=<VALUE>; session=<VALUE>' > ~/.chaika_cookies
echo '<ehr-id>' > ~/.chaika_ehr_id
```

**Если в cURL нет `remember`/`session`** (новый фронт, 2026-08): авторизация — заголовком
`authorization: JWT <token>` из того же cURL. Токен живёт **~15 минут** — работай сразу, файл
не сохраняй; куки достаточно минимальной строки `bookingV4.tenant=chaika; app_locale=ru`.
EHR ID по-прежнему в URL. У каждого пациента (члена семьи) **свой аккаунт и свой EHR** —
JWT одного аккаунта чужую медкарту не отдаёт, эндпоинта «вся семья» у Чайки нет.

## API

Base URL: `https://chaika.com/api`

Общие заголовки для всех запросов:
```
-H 'accept: application/json'
-H 'x-lang: ru'
-b "$(cat ~/.chaika_cookies)"
-H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'
```

### Эндпоинты

| Эндпоинт | Версия | Описание |
|----------|--------|----------|
| `GET v1/account/{ehr}/medical-card-history?page={N}` | v1 | История медкарты (пагинация, 30/стр) |
| `GET v1/account/{ehr}/entries/{entry-id}` | v1 | Детали записи (протокол, услуги, ссылки на PDF) |
| `GET v1/account/{ehr}/entries` | v1 | Список всех записей |
| `GET v1/account/{ehr}/drugs` | v1 | Назначения лекарств |
| `GET v1/account/{ehr}/recommendations` | v1 | Рекомендации врачей |
| `GET v3/account/transactions/completed?ehrId={ehr}` | v3 | Завершённые транзакции (чеки, акты) |
| `GET v3/account/transactions/{tx-id}` | v3 | Детали транзакции (цены, клиника) |

## Действия

### `$action` = `visits` (или пусто)

Получить историю визитов. Забрать все страницы (поле `pages` в ответе).

Ответ — JSON с полями: `page`, `pages`, `limit`, `total`, `items[]`.

Каждый item имеет `_type`:
- **`entry`** — запись о визите. Данные внутри `item.entry`: `createdAt`, `author.fullName`, `author.specialities[].name`, `entryType.title`, `medicalCase.name`, `clinic.name`
- **`recommendation`** — рекомендация врача. Поля: `start`/`createdAt`, `doctor.fullName`, `doctor.specialities[].name`, `medicalCase.name`, `comment`
- **`drug`** — назначение. Поля: `start`/`createdAt`, `doctor.fullName`, `doctor.specialities[].name`, `name`, `comment`

Выведи результат в виде таблицы, сгруппированной по дате визита. Для каждой даты покажи:
- Дату
- Врача и специальность
- Тип визита (консультация, вакцинация, анализы и т.д.)
- Повод / медицинский кейс

### `$action` = `docs <дата>` (например `docs 17.05.2026`)

Скачать все документы по визитам за указанную дату в `~/Downloads/chaika/`.

#### Структура папок

Для каждого приёма за дату создаётся отдельная папка:
```
~/Downloads/chaika/
  ГГГГ-ММ-ДД Тип приёма — Фамилия И.О./
    Протокол приёма.pdf
    Акт оказанных услуг.pdf
    Кассовый чек.pdf           ← если orangedata
    Кассовый чек (ссылка).txt  ← если taxcom (капча не позволяет скачать)
```

Имя папки формируется из:
- Даты в формате ГГГГ-ММ-ДД
- `entryType.title` из entry (например "Консультация терапевта", "Вакцинация", "Анализы")
- Фамилия и инициалы врача из `author` (Коростылева О.С.)

#### Алгоритм

1. Получить `medical-card-history` (все страницы) и найти все записи (`_type: "entry"`) за указанную дату по `item.entry.createdAt`
2. Для каждой записи запросить `v1/account/{ehr}/entries/{entry-id}` — получить `downloadReportUrl` и `renderedServices`
3. Получить `v3/account/transactions/completed?ehrId={ehr}` и сопоставить транзакции с записями через `renderedServices[].id` (он совпадает в entries и transactions)
4. Создать папку и скачать документы:

**Протокол приёма:**
```bash
curl -sL "https://chaika.com{entry.downloadReportUrl}" -b "$COOKIES" -H "user-agent: ..." -o "Протокол приёма.pdf"
```

**Акт оказанных услуг:**
```bash
curl -sL "{transaction.actUrl}" -b "$COOKIES" -H "user-agent: ..." -o "Акт оказанных услуг.pdf"
```

**Кассовый чек** — зависит от провайдера:
- **orangedata.ru** → конвертировать в PDF через Chrome headless. Бинарь резолвим
  кросс-платформенно (macOS и Linux/WSL):
  ```bash
  CHROME="$(command -v google-chrome || command -v google-chrome-stable \
    || command -v chromium || command -v chromium-browser \
    || echo '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')"
  "$CHROME" \
    --headless --disable-gpu --no-sandbox \
    --print-to-pdf="Кассовый чек.pdf" \
    --print-to-pdf-no-header \
    --virtual-time-budget=5000 \
    "{slipUrls[0]}"
  ```
  Если ни один бинарь не найден (нет Chrome/Chromium) — не падай: сохрани ссылку
  `echo "{slipUrls[0]}" > "Кассовый чек (ссылка).txt"` и сообщи пользователю.
- **taxcom.ru** → страница защищена капчей, автоматическое скачивание невозможно. Сохранить ссылку:
  ```bash
  echo "{slipUrls[0]}" > "Кассовый чек (ссылка).txt"
  ```
  Сообщить пользователю, что чек нужно скачать вручную по ссылке.

5. Вывести дерево скачанных файлов.

#### Структура ответа entry detail

```json
{
  "entry": {
    "downloadReportUrl": "/api/v1/mis/entries/{id}/pdf?token=...",
    "downloadFilesUrl": "/api/v1/mis/entries/{id}/download-files?token=...",
    "publicUrl": "https://chaika.com/print/entry/{id}?token=...",
    "renderedServices": [{"id": "...", "status": "paid", "service": {"name": "..."}, "serviceName": "..."}],
    "fields": [{"name": "field_name", "value": "protocol text"}],
    "author": {"fullName": "...", "lastName": "...", "firstName": "...", "patronymic": "..."},
    "entryType": {"title": "Консультация терапевта"},
    "createdAt": "2026-05-17T06:44:00+00:00"
  },
  "files": [],
  "radiologyUrls": []
}
```

#### Структура ответа transactions/completed

```json
{
  "items": [{
    "id": "...",
    "amount": 150000,
    "currency": "RUB",
    "renderedServices": [{"id": "...", "service": {"name": "..."}, "price": 570000, "amountToPay": 150000, "organization": {"name": "Клиника в Метрополисе"}}],
    "slipUrls": ["https://receipt.taxcom.ru/..."],
    "actUrl": "https://chaika.com/api/v3/transactions/act/{id}/pdf?token=..."
  }]
}
```

Суммы в копейках (150000 = 1500₽). `price` — цена по прайсу, `amountToPay` — к оплате (со скидкой подписки).

### `$action` = `details <entry-id>`

Получить детали конкретной записи: `GET v1/account/{ehr}/entries/{entry-id}`.

Вывести:
- Дату, врача, тип визита
- Протокол (поля из `fields[]` с непустыми `value`)
- Оказанные услуги (`renderedServices`)
- Ссылки для скачивания

## АльфаСтрахование — подача на возмещение ДМС (`$action` = `refund`)

Подать заявления на возмещение в `med.alfastrah.ru` по документам, скачанным через `docs`.

### Конфигурация (перс. данные хранятся ВНЕ скилла)

- `~/.alfastrah_profile.json` — персональные данные (паспорт, банк, ИНН/СНИЛС, ФИО, номер полиса, путь к скану паспорта/полиса — можно **списком** путей, все прикладываются). Шаблон с именами полей — `alfastrah_profile.template.json` в этом скилле. Права 600 (`chmod 600`). **В скилл реальные значения не коммитить.**
- `~/.alfastrah_cookies` — сессия. Сними `Copy as cURL` **XHR-запроса** (DevTools → Network → фильтр **Fetch/XHR** → любой запрос к `med.alfastrah.ru/api/...`), сохрани всю строку кук целиком. **Навигационный (document) запрос НЕ годится**: в нём только одноразовый `AuthorizationToken`, который браузер уже потратил при логине (API ответит «Ошибка получения токена от сервиса авторизации»); живой `accessToken` есть только в XHR-куках. `accessToken` (JWT) живёт **~5 мин и НЕ продлевается** — снимай свежие куки прямо перед запуском.

**Если файлов нет — не падай молча, запроси у пользователя:**

- **Нет `~/.alfastrah_profile.json`** — предложи два пути на выбор:
  1. **Прислать HAR или `Copy as cURL` прошлой подачи возмещения** (POST `/api/policy/dms/{policy}/claim`) — тогда собери профиль автоматически: `email, phone, passport, bank, documents{inn,snils}` из тела запроса, `insured{insuredName,birthdate}` и `policy.number` из `claimInfo`. `policy.claimsListId` возьми из URL `GET .../dms/{id}/claims`, если он есть в HAR.
  2. **Заполнить поля вручную** — пройди по `alfastrah_profile.template.json` и спроси значения по группам (паспорт, банк, ИНН/СНИЛС, ФИО, номер полиса).
  Затем запиши `~/.alfastrah_profile.json` (через python `json.dump`) и выставь права 600 (`os.chmod(path, 0o600)`).
  Часть полей можно снять с портала по свежим кукам, не спрашивая пользователя: `GET /api/policy/dms/{policy}` отдаёт `insuredName, birthdate, phone, email, employeePolicyId` (данные владельца полиса). Паспортные данные можно прочитать со скана паспорта (vision), если он есть на диске; банк/ИНН/СНИЛС портал не отдаёт — только спрашивать. **НЕ жди предзаполнения от `GET /policy/dms/{policy}/claim`** — этот эндпоинт возвращает только метаданные формы (категории/валюты/соглашение), а не сохранённые данные пользователя.
- **Нет `~/.alfastrah_cookies`** — попроси свежий `Copy as cURL` XHR-запроса (см. выше) и сохрани всю строку кук целиком (нужно каждый запуск — токен 5 мин).

### ⚠️ API-подача МЕРТВА (2026-08-22) — подавать через форму ЛК

Скриптовая подача (`submit_alfastrah.py <claims.json>`) **больше не работает**: `POST
/api/policy/dms/{policy}/claim` всё ещё заводит черновик и возвращает id, но финализирующий
`PUT .../claim/{id}` отвечает **пустым 200** вместо JSON — заявление на портале НЕ создаётся
(и дубля не оставляет: черновик в списке не всплывает). Портал переехал на новый флоу
(форма тянет `GET /api/policy/dms/{claimsListId}/claims/prefill` — паспорт/банк/СНИЛС/ИНН
предзаполняются сервером). Скрипт остаётся только ради `--sync`.

**Рабочий путь — форма ЛК** `/policies/dms/{policy}/return-dms?step=application`, 3 шага
(Оформление → Просмотр → Подтверждающие документы). Вести можно браузер-автоматизацией;
финальный submit и достоверность данных подтверждает пользователь. Грабли формы:

- «Размер понесенных расходов» — маскированное поле, цифры набираются справа (как копейки):
  для 1500 ₽ печатать `150000`;
- СНИЛС из прифилла приходит **без двух последних цифр** и краснеет валидацией — дописать;
- «ФИО застрахованного» — выпадашка со всеми членами семьи (полис и дата рождения
  подставляются сами); для подачи за родственника его паспорт грузить в слот «Другие документы»;
- слоты документов на шаге 3: паспорт заявителя / кассовый чек / акт / мед. заключение
  (= протокол приёма) / другие; тип возмещения — «Возмещение стоимости медицинских услуг»;
- **№ заявления виден только на шаге «Просмотр»** (`Заявление №<N>.pdf`) — запиши его там;
- **список заявлений индексируется с задержкой**: в день подачи `claims?count=ALL` ещё отдаёт
  старый список, хотя модалка «Ваше заявление зарегистрировано» была. Наутро всё на месте —
  не паникуй и не подавай повторно.

Проверка/статусы (это API живо): `GET /api/policy/dms/{claimsListId}/claims?count=ALL&type=REFUND`
— поле `id` не отдаётся, номер в `title` «Заявление №…». `claimsListId` ≠ номер полиса (в профиле
отдельным полем): это `employeePolicyId`/`policyId` из `GET /api/policy/dms/{policy}`. Там же
список застрахованных семьи: `data.claimOptions.persons[]` (`{insuredName, birthdate, policyNumber}`).

TLS: цепочка `med.alfastrah.ru` — Russian Trusted CA (Минцифры). curl/python вне браузера
падают с untrusted root — нужен бандл (системный ca-bundle + корневой серт) через
`--cacert` / `SSL_CERT_FILE`; в системное хранилище не добавлять.

### Алгоритм

1. Уточни у пользователя визиты к подаче (покажи дату/сумму/повод). Сумму бери из transactions (action `docs`). Категорию по умолчанию `"Амбулаторно-поликлиническая помощь/Outpatient treatment"`; **стоматология — отдельная категория** (`Стоматология/Dental treatment`), уточни. Суммы без кук Чайки — из «Акт оказанных услуг.pdf» (строка «Итого»); pdftotext может отсутствовать — фолбэки: `pypdf`, `pdfjs-dist` через node.
2. Подача — через форму ЛК (см. выше), по одному заявлению на визит. Перед финальным submit каждого — **подтверждение пользователя**: это РЕАЛЬНЫЕ страховые заявления.
3. После каждой подачи запиши `Заявление №<N>.txt` в папку визита и строку в таблицу `Заявления.csv` со статусом `Отправлено` (формат — docstring скрипта; защита от дубля — по наличию `Заявление №*.txt` в папке).
4. (Опц.) Обнови статусы до актуальных: `--sync` (дёргает список с портала и переписывает столбец «Статус» в таблице). Цепочка: **Отправлено → На рассмотрении → Оплачено** (рассмотрение до 15 кал. дней).

```bash
# обновить статусы в таблице до live (требует свежих кук)
python3 "<каталог скилла>/submit_alfastrah.py" --sync
```

## Обработка ошибок

- 401/403 — cookies протухли, попроси обновить `~/.chaika_cookies`
- 404 — проверь EHR ID
- Все `null` значения обрабатывай безопасно (`or {}` / `or []`)
- Токены в URL скачивания временные — если протух, запроси entry/transactions заново для свежих токенов
