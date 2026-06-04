# Autoalerter

## 1) Что это за проект
`autoalerter` — это небольшой CLI-скрипт для автоматической обработки мониторинговых событий (`event=1` / `event=0`) с интеграциями в:
- **Jira** — загрузка метаданных сервиса, создание инцидента, проверка статуса issue;
- **Kontur Talk (KTalk)** — создание/обновление обсуждения, упоминания пользователей в треде, инвайты в комнату;
- **resolver API** — получение `ktalk_mention_id` по `ad_login`;
- **PostgreSQL** — хранение состояния инцидентов (`event_balance`, thread, jira key, время закрытия).

Скрипт запускается как отдельный процесс (без очередей и фоновых воркеров) и делает ровно один проход обработки входного события.

---

## 2) Структура проекта
- `autoalerter.py` — **orchestration**: parse/validate/decide/execute, ветки open/close, anti-flap, dry-run по инвайтам.
- `jira_client.py` — только Jira-HTTP и разбор Jira-данных.
- `ktalk_messenger.py` — отправка сообщений, создание треда, упоминания в треде.
- `ktalk_invites.py` — участники комнаты, инвайты, dry-run представление списка инвайтов.
- `db.py` — PostgreSQL и SQL-запросы.
- `cnf.py` — явная конфигурация (без env).

---

## 3) Общая логика работы

### `event=1` (open)
1. Парсинг CLI-аргументов.
2. Валидация payload (`insightId`).
3. Загрузка Jira-метаданных сервиса (`get_jira_data`).
4. Если сервис неактуален — завершение без действий.
5. Если активный инцидент уже есть в БД:
   - увеличить `event_balance`;
   - отправить сообщение в существующий `thread_root_event_id`;
   - не создавать Jira и новый тред.
6. Если активного инцидента нет:
   - проверить anti-flap условия (по времени/дням + недавнее закрытие);
   - при срабатывании anti-flap:
     - не создавать новый Jira;
     - не создавать новый тред;
     - реактивировать кейс новой записью в БД с reuse `thread_root_event_id` и `jira_issue_key`;
     - отправить сообщение в существующий тред.
   - если anti-flap не сработал:
     - создать Jira-инцидент;
     - создать обсуждение в KTalk (root + первое сообщение в треде);
     - если `JIRA_THREAD_LINK_TRANSITION_ENABLED = True`:
       - сформировать web-ссылку на KTalk-тред;
       - выполнить Jira transition `541` для созданного issue;
       - заполнить поле `customfield_41400` ссылкой на KTalk-тред;
       - успешный ответ Jira: `204 No Content`;
       - при ошибке валидации Jira вернет `400` с ошибкой `customfield_41400`;
     - если `JIRA_THREAD_LINK_TRANSITION_ENABLED = False`, пропустить построение ссылки и Jira transition;
     - резолвить получателей через resolver API;
     - разделить на `in_room` / `out_of_room`;
     - упомянуть `in_room`;
     - пригласить `out_of_room` (или dry-run);
     - записать новый инцидент в БД.

### `event=0` (close)
1. Найти последний активный `thread_root_event_id` и `jira_issue_key`.
2. Уменьшить `event_balance`.
3. Отправить сообщение в тред.
4. Если `event_balance > 0` — ничего больше не делать.
5. Если `event_balance == 0`:
   - проверить статус Jira issue;
   - отправить финальное сообщение в тред;
   - закрыть инциденты в БД (`event_balance=0`) и сохранить `close_event_at`.

---

## 4) Dry-run режим инвайтов

### Что делает
Флаг `KTALK_INVITES_DRY_RUN` управляет только инвайтами:
- `False` (по умолчанию): реальные POST `/invite` выполняются;
- `True`: реальные инвайты не отправляются, вместо этого публикуется сообщение в комнату со списком пользователей, которых скрипт **пригласил бы**.

### Что не меняется в dry-run
- mentions в треде работают как обычно;
- resolver API и split по участию в комнате работают как обычно.

### Пример dry-run сообщения
```text
Dry-run режим инвайтов включен. Были бы приглашены:
- Иван Иванов @ivanov:matrix-9.ktalk.ru
- Петр Петров @petrov:matrix-9.ktalk.ru
```

Если список пуст:
```text
Dry-run режим инвайтов включен. Пользователей для приглашения нет.
```

---

## 5) Антифлап-логика

Антифлап нужен для сценариев, когда после `event=0` снова приходит `event=1` в коротком окне.

### Когда anti-flap может сработать
1. Anti-flap включен конфигом.
2. Текущее время попадает в расписание anti-flap:
   - ночное окно (например, `21:00 -> 09:00`, корректно через полночь),
   - **или** день недели из `FLAP_REOPEN_WEEKDAYS`.
3. Есть последний закрытый инцидент с `close_event_at`.
4. С момента закрытия прошло не больше `FLAP_REOPEN_HOURS`.
5. У последнего закрытого инцидента есть `thread_root_event_id` и `jira_issue_key`.

Если все условия выполняются:
- создаётся запись реактивации в БД с reuse старых `thread_root_event_id` и `jira_issue_key`;
- Jira и новый тред **не** создаются;
- сообщение отправляется в существующий тред.

### Важная деталь по weekday
Проверка дней недели делается через Python `datetime.weekday()`:
- `0 = Monday`
- `1 = Tuesday`
- `2 = Wednesday`
- `3 = Thursday`
- `4 = Friday`
- `5 = Saturday`
- `6 = Sunday`

Для выходных задавайте `(5, 6)`.

---

## 6) Конфигурация (`cnf.py`)

### Jira
- `JIRA_TOKEN`
- `JIRA_SERVICE_URL`
- `JIRA_CREATE_INC_URL`
- `JIRA_ISSUE_STATUS_URL`
- `JIRA_ISSUE_BROWSE_URL`
- `JIRA_THREAD_LINK_TRANSITION_ENABLED` — включает Jira transition после создания KTalk-треда
- `JIRA_THREAD_LINK_TRANSITION_URL` — URL POST transition с `{issueKey}`
- `JIRA_THREAD_LINK_TRANSITION_ID` — id transition, по умолчанию `541`
- `JIRA_THREAD_LINK_CUSTOM_FIELD` — поле ссылки на тред, по умолчанию `customfield_41400`
- `JIRA_THREAD_LINK_TRANSITION_STRICT` — режим обработки ошибки Jira transition:
  - `False` — ошибка transition логируется, основной flow продолжается до mentions/invites и записи в БД;
  - `True` — HTTP-ошибка transition пробрасывается исключением и останавливает flow.

### KTalk Bot API
- `KTALK_BASE_URL`
- `KTALK_BOT_USER`
- `KTALK_JWT_TOKEN`
- `KTALK_ROOM_ID`
- `KTALK_SEND_MESSAGE_RETRIES` — сколько попыток делать для небезопасной отправки `send_message`; по умолчанию `1`, чтобы не дублировать сообщение при неоднозначном HTTP 5xx/timeout
- `KTALK_SAFE_REQUEST_RETRIES` — сколько попыток делать для безопасных KTalk HTTP-вызовов, например GET-запросов
- `KTALK_REQUEST_RETRIES` — обратная совместимость для старых импортов; новые места должны использовать настройки выше
- `KTALK_RETRY_DELAY_SECONDS` — задержка между попытками (например, `5` секунд)
- `KTALK_THREAD_LINK_REQUIRED_PREFIX` — обязательный prefix ссылки для Jira-валидации
- `KTALK_THREAD_WEB_URL_TEMPLATE` — шаблон web-ссылки на KTalk-тред

### KTalk Bearer API
- `KTALK_HOST`
- `KTALK_TALK_HOST`
- `KTALK_BEARER_TOKEN`

### Resolver
- `RECIPIENT_RESOLVER_URL` (формат: `.../resolve?ad_login=ivanov&ad_login=petrov`)

### Runtime
- `REQUEST_TIMEOUT`
- `VERIFY_SSL`
- `LOG_FILE`

### Invite behavior
- `KTALK_INVITES_DRY_RUN = False`

### Flap-reopen behavior
- `FLAP_REOPEN_WINDOW_ENABLED = True`
- `FLAP_REOPEN_APPLY_NIGHT_WINDOW = True`
- `FLAP_REOPEN_TIME_START_HOUR = 21`
- `FLAP_REOPEN_TIME_END_HOUR = 9`
- `FLAP_REOPEN_WEEKDAYS = (5, 6)`
- `FLAP_REOPEN_HOURS = 3`
- `TIMEZONE_NAME = "Europe/Moscow"`

### Mandatory recipients
- `MANDATORY_RECIPIENTS = (...)`

---

## 7) PostgreSQL

Скрипт работает с таблицей `trmetrics.availconf.conf`.

Для anti-flap нужен timestamp последнего закрытия. Минимальная миграция:

```sql
ALTER TABLE trmetrics.availconf.conf
ADD COLUMN IF NOT EXISTS close_event_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS conf_insight_close_event_at_idx
ON trmetrics.availconf.conf (insight_id, close_event_at DESC);
```

### Зачем поле `close_event_at`
- фиксирует момент, когда кейс окончательно закрыт (`event_balance` стал 0);
- используется при последующем `event=1` для решения, можно ли reopen в старый тред/Jira в рамках anti-flap окна.

---

## 8) Запуск

### Пример команды
```bash
python autoalerter.py \
  --event 1 \
  --insightId TZ-12345 \
  --groups "SG/ServiceA" \
  --triggerTime "2026.04.17 12:34:56" \
  --trigName "High error rate" \
  --message "Сервис недоступен"
```

### Аргументы
- `--event`: `0` или `1`
- `--insightId`: идентификатор вида `TZ-12345`
- `--groups`: строка групп мониторинга, используется для извлечения short name
- `--triggerTime`: время триггера в формате `YYYY.MM.DD HH:MM:SS`
- `--trigName`: имя триггера
- `--message`: текст сообщения

---

## 9) Логирование и отладка

Логи пишутся в файл `LOG_FILE` (по умолчанию `/tmp/autoalerter.log`).

### Что логируется
- границы шагов сценария (`Step: ...`);
- решения ветвления (`Decision: ...`);
- resolver summary (`requested/resolved/not_found/without_mention`);
- anti-flap решение (активен график или нет, возраст последнего close, reuse thread/jira);
- retry по KTalk (ошибка попытки, ожидание перед retry, успешная повторная попытка, итоговая ошибка после всех попыток);
- итоги KTalk:
  - `KTalk mention summary | mentioned_count=...`
  - `KTalk invite summary | invited_count=...`
  - `KTalk invite dry-run summary | would_invite_count=... actual_invited_count=0`

### Типовая диагностика
- **Jira ошибки**: проверить `JIRA_TOKEN`, URL и SSL настройки.
- **Resolver ошибки**: проверить `RECIPIENT_RESOLVER_URL`, формат ответа JSON.
- **KTalk invite ошибки**: проверить `KTALK_BEARER_TOKEN`, `KTALK_HOST`, `KTALK_TALK_HOST`, room id.
- **KTalk retry**: если видите `retry in 5 seconds`, первая попытка не удалась, но процесс автоматически делает следующую попытку с задержкой из `KTALK_RETRY_DELAY_SECONDS`.
- **DB ошибки**: проверить доступ к PostgreSQL и наличие `close_event_at`.

---

## 10) Примеры сценариев

### A. Обычный `event=1`, активного кейса нет
- создаётся Jira,
- создаётся KTalk discussion,
- mentions + invites,
- запись в БД с `event_balance=1`.

### B. Повторный `event=1` при активном кейсе
- `event_balance` увеличивается,
- сообщение идёт в существующий тред,
- новый Jira/тред не создаются.

### C. `event=0`
- `event_balance` уменьшается,
- сообщение идёт в тред,
- если стало `0` — финализация, закрытие в БД с `close_event_at`.

### D. `event=1` в anti-flap окне после недавнего `event=0`
- если сейчас ночь/выходной (по конфигу) и прошло <= `FLAP_REOPEN_HOURS`,
- Jira/тред не создаются,
- кейс реактивируется в БД,
- сообщение пишется в старый тред.

### E. Dry-run инвайтов
- пользователи `out_of_room` вычисляются,
- реальные invite не отправляются,
- в комнату публикуется список "кого бы пригласили".
