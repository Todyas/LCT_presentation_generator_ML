# План backend-разработки: «Цифровой дизайнер презентаций»

Статус документа: рабочая концепция и план реализации  
Область ответственности: backend, инфраструктура, интеграционные контракты с ML и frontend  
Источники требований: техническое задание VK Tech, текущая реализация репозитория, согласованный продуктовый scope команды

## 1. Цель решения

Сервис получает пользовательский промпт и неизвестный заранее `.pptx`-шаблон, анализирует правила шаблона и за ограниченное время создаёт презентацию из 10–15 слайдов в трёх визуально различимых вариантах. Пользователь просматривает готовые слайды и может отдельным промптом исправить конкретный слайд, не запуская повторную генерацию всей презентации.

Ключевая инженерная формулировка:

> ML определяет, что показать и как интерпретировать содержание. Backend детерминированно решает, где и по каким правилам это расположить, хранит версии, выполняет аудит и экспортирует нативные файлы.

Решение должно работать с новым корпоративным шаблоном, которого не было при разработке, а не только с тремя выданными примерами.

## 2. Зафиксированный продуктовый scope

### 2.1 Страница 1 — создание презентации

Пользователь может:

- загрузить один `.pptx`-шаблон;
- ввести текстовый промпт;
- выбрать количество слайдов, по умолчанию 10–15;
- запустить генерацию;
- видеть этап и прогресс выполнения;
- получить понятную ошибку без потери созданного проекта.

### 2.2 Страница 2 — результат

Пользователь может:

- переключаться между вариантами A, B и C;
- просматривать PNG-превью всех слайдов;
- видеть состояние генерации и аудита каждого слайда;
- открыть конкретный слайд;
- написать промпт на исправление конкретного слайда;
- дождаться нового preview только этого слайда;
- вернуться к предыдущей ревизии слайда;
- скачать актуальный вариант в PPTX и PDF;
- скачать HTML после реализации обязательного HTML-экспорта.

### 2.3 Что намеренно не входит в текущий scope

- свободный drag-and-drop canvas;
- ручное изменение координат объектов;
- совместное редактирование;
- real-time multiplayer;
- мобильная версия;
- полноценная замена PowerPoint;
- обучение собственной layout-модели;
- микросервисное разбиение backend;
- text-to-image до прохождения в топ-10, если это не требуется для демонстрации.

### 2.4 Осознанное временное расхождение с ТЗ

На первой версии UI пользователь загружает только шаблон и вводит промпт. Отдельный content pack в интерфейсе пока отсутствует. При этом backend-модель и API должны допускать последующее добавление `ContentPack`, потому что импорт контент-пакета указан в ТЗ.

До сдачи команда должна принять одно из двух решений:

- добавить необязательную загрузку content pack в существующую первую страницу;
- явно задокументировать сокращение scope и риск потери баллов.

## 3. Основные архитектурные решения

### 3.1 Формат приложения

Используется модульный монолит в одном репозитории. API и worker запускаются отдельными процессами, но используют один Python-код и общие доменные модели.

```text
Internet
   |
 Nginx
   |-- /             -> Frontend
   |-- /api          -> FastAPI
   `-- /artifacts    -> downloads или signed URLs
                           |
              +------------+------------+
              |            |            |
          PostgreSQL     Redis       Artifact Storage
              |       queue/cache     PPTX/PDF/PNG/HTML
              |            |
              `-------- Worker --------'
                           |
                    Generation Pipeline
                           |
                       LLM / VLM
```

### 3.2 Компоненты

| Компонент | Ответственность |
|---|---|
| Nginx | TLS, routing, upload limits, rate limits, SSE proxying, static artifacts |
| Frontend | Две страницы, progress UI, previews, prompt исправления, downloads |
| FastAPI | Авторизация при необходимости, DTO, запуск jobs, чтение состояния, выдача результатов |
| Worker | Парсинг, ML-вызовы, composition, PPTX/PDF/HTML rendering, audit, repair |
| PostgreSQL | Источник истины для проектов, вариантов, слайдов, ревизий, jobs и аудита |
| Redis | Broker, distributed locks, short-lived cache, progress Pub/Sub |
| Artifact Storage | Исходные шаблоны, PPTX, PDF, PNG, HTML и изображения |
| LLM/VLM endpoint | Только семантические операции через версионированный ML Gateway |

### 3.3 Хранение файлов

Бинарные файлы не хранятся в PostgreSQL.

Допустимые реализации:

- основной вариант: S3-совместимое хранилище или MinIO;
- допустимый вариант для одного хоста: persistent volume с интерфейсом `ArtifactStorage`;
- запрещённый вариант: временная директория контейнера без volume.

Каждый artifact имеет:

- уникальный ключ;
- MIME type;
- размер;
- SHA-256;
- ссылку на deck/variant/slide revision;
- дату создания;
- политику удаления.

### 3.4 Очередь задач

FastAPI не выполняет генерацию через `BackgroundTasks`. Длительные операции уходят в Redis-backed worker, например Celery или Dramatiq.

PostgreSQL хранит окончательное состояние job. Redis не является источником истины.

Основные типы задач:

- `generate_deck`;
- `revise_slide`;
- `export_variant`;
- `reanalyze_template`, если требуется принудительное обновление кэша.

Внутренние стадии основной задачи могут оставаться обычными вызовами внутри worker, чтобы не усложнять orchestration.

### 3.5 Прогресс

`POST` запуска генерации сразу возвращает `202 Accepted`, `deck_id` и `job_id`.

Frontend получает прогресс через Server-Sent Events:

```text
queued
template_parsing
template_analyzed
deck_planning
variant_a_composing
variant_b_composing
variant_c_composing
rendering
auditing
exporting
completed
```

SSE выбран вместо WebSocket, потому что основной поток данных однонаправленный: сервер сообщает состояние клиенту.

## 4. Границы backend и ML

### 4.1 Backend отвечает за

- проверку и безопасное хранение загрузок;
- разбор PPTX/OOXML;
- извлечение точной геометрии;
- доменные модели и валидацию;
- хранение source/evidence IDs;
- deterministic layout filtering и scoring;
- привязку контента к slots;
- расчёт координат и text fit;
- создание нативного PPTX;
- PDF, PNG и HTML export;
- детерминированный аудит;
- lifecycle jobs;
- версии слайдов;
- повторную сборку презентации;
- retries, timeout, idempotency и observability.

### 4.2 ML отвечает за

- построение канонического плана презентации;
- генерацию текста в рамках заданных ограничений;
- семантическую классификацию неоднозначных layouts;
- выбор типа визуализации;
- сжатие текста;
- изменение конкретного слайда по пользовательскому промпту;
- семантический аудит;
- визуальный аудит по PNG при наличии VLM;
- генерацию изображения только в расширенной версии.

### 4.3 ML не имеет права

- записывать файлы напрямую;
- определять абсолютные координаты объектов как единственный источник истины;
- генерировать OOXML;
- менять status job;
- обращаться к PostgreSQL;
- принимать непроверенные решения о путях к файлам;
- обходить Pydantic-валидацию;
- самостоятельно запускать следующий этап pipeline.

### 4.4 Контракт ML Gateway

Все обращения к моделям проходят через единый gateway, который отвечает за:

- model name и endpoint;
- версию prompt/skill;
- structured JSON schema;
- timeout;
- retry;
- concurrency limit;
- логирование latency и token usage, если provider их возвращает;
- сохранение версии prompt и модели в job metadata;
- нормализацию ошибок;
- запрет невалидного JSON.

## 5. Доменные модели

### 5.1 Template

```text
Template
- id
- original_filename
- artifact_key
- sha256
- status
- analyzer_version
- analysis_json: TemplateIR
- created_at
```

`TemplateIR` должен включать:

- slide size;
- masters и layouts;
- стабильный concrete `layout_id`;
- placeholder types и resolved geometry;
- theme colors;
- реально используемые цвета;
- font families;
- typography scale;
- backgrounds;
- safe margins и базовую сетку;
- repeating elements: logo/footer/slide number;
- реальные sample slides и их связь с layout;
- capacity constraints;
- semantic layout roles;
- parser warnings и unsupported features.

### 5.2 Deck

```text
Deck
- id
- template_id
- prompt
- requested_slide_count
- status
- canonical_plan_json: DeckPlan
- created_at
- updated_at
```

### 5.3 DeckPlan

Канонический план создаётся один раз и используется всеми вариантами.

```text
DeckPlan
- goal
- audience
- language
- narrative
- slides[]
    - stable_slide_key
    - purpose
    - key_message
    - content_hint
    - visual_intent
    - evidence_ids[]
```

### 5.4 DeckVariant

```text
DeckVariant
- id
- deck_id
- code: A | B | C
- policy
- status
- rendered_deck_ir_json
- current_export_version
- pptx_artifact_key
- pdf_artifact_key
- html_artifact_key
```

Политики вариантов:

- A — Executive: крупные тезисы и KPI, минимум текста;
- B — Analytical: графики, таблицы, сравнения;
- C — Structural: процессы, схемы, этапы и структурированные блоки.

Факты и основные выводы должны оставаться согласованными между вариантами.

### 5.5 SemanticSlidePlan

Текущий `SlideIR` по смыслу является semantic plan:

- заголовок;
- key message;
- набор semantic components;
- visual intent;
- evidence IDs;
- предпочтительный layout role.

В нём ещё нет окончательной геометрии.

### 5.6 RenderedSlideIR

```text
RenderedSlideIR
- slide_id
- revision
- concrete_layout_id
- elements[]
    - id
    - type
    - semantic_role
    - frame: x/y/w/h
    - style_token
    - content
    - evidence_ids[]
    - source_slot_id
- warnings[]
```

Это единый источник правды для:

- PPTX renderer;
- HTML renderer;
- preview;
- geometry audit;
- audit overlay;
- slide revision;
- повторной сборки deck.

### 5.7 SlideRevision

```text
SlideRevision
- id
- slide_id
- revision_number
- correction_prompt
- semantic_plan_json
- rendered_ir_json
- preview_artifact_key
- audit_run_id
- created_at
```

Старые ревизии не перезаписываются.

### 5.8 GenerationJob

```text
GenerationJob
- id
- deck_id
- job_type
- status
- stage
- progress
- idempotency_key
- worker_id
- attempt
- error_code
- error_message
- timing_json
- created_at
- started_at
- finished_at
```

## 6. End-to-end pipeline

### Этап 1. Ingestion

Вход:

- `.pptx` template;
- prompt;
- slide count;
- в будущем optional content pack.

Действия:

1. Ограничить размер загрузки на Nginx и API.
2. Проверить расширение, MIME и ZIP signature.
3. Проверить наличие `ppt/presentation.xml`.
4. Защититься от zip bomb и path traversal.
5. Рассчитать SHA-256.
6. Сохранить оригинал неизменным.
7. Найти кэш анализа по hash и analyzer version.
8. Создать Deck и GenerationJob в одной транзакции.
9. Поставить job в очередь с idempotency key.

Definition of Done:

- [ ] Невалидный PPTX отклоняется до постановки job.
- [ ] Повторный запрос с тем же idempotency key не создаёт вторую генерацию.
- [ ] Файл переживает рестарт API и worker.
- [ ] Размер и hash сохранены.
- [ ] Пользователь получает `202`, а не ждёт генерацию.

### Этап 2. Template Analysis

Действия:

1. Распарсить masters, layouts, relationships и theme.
2. Разрешить унаследованные свойства master → layout → placeholder.
3. Собрать concrete layout IDs.
4. Извлечь typography, colors, backgrounds и recurring shapes.
5. Проанализировать реальные слайды-примеры, если они присутствуют.
6. Классифицировать layouts эвристиками.
7. Отправить только неоднозначные layouts в VLM, если VLM доступна.
8. Сохранить warnings для неподдерживаемых объектов.
9. Кэшировать `TemplateIR` по `(sha256, analyzer_version)`.

Definition of Done:

- [ ] Анализируются все masters, а не только первый.
- [ ] Layout связан с правильным master.
- [ ] Geometry находится в пределах слайда или помечена warning.
- [ ] Извлекаются минимум цвета, шрифты, layouts и slots.
- [ ] Новый шаблон не требует ручного mapping по имени файла.
- [ ] Нет hardcode для трёх тестовых шаблонов.
- [ ] Парсер имеет snapshot/fixture tests на трёх шаблонах.
- [ ] Повторный анализ идентичного шаблона использует кэш.

### Этап 3. Canonical Deck Planning

Действия:

1. Получить prompt, доступные layout roles и slide count.
2. Сформировать один `DeckPlan`.
3. Проверить язык.
4. Проверить количество слайдов.
5. Проверить отсутствие placeholder text.
6. Проверить факты и числа относительно входа.
7. Сохранить модель и версию prompt.

Definition of Done:

- [ ] План содержит заданное число слайдов.
- [ ] У каждого слайда один key message.
- [ ] Нет неизвестных layout roles.
- [ ] Ответ валиден по JSON Schema.
- [ ] Retry ограничен.
- [ ] Неуспешный LLM-вызов переводит job в понятное состояние.
- [ ] Один и тот же DeckPlan является основой A/B/C.

### Этап 4. Variant Expansion

Действия:

1. Применить A/B/C policy к каждому пункту DeckPlan.
2. Получить `SemanticSlidePlan` для каждого варианта.
3. Не допустить расхождения ключевых фактов.
4. Разрешить различия layout, плотности и визуализации.

Definition of Done:

- [ ] Все три варианта имеют одинаковое число смысловых слайдов либо различие явно допускается policy.
- [ ] Варианты визуально различимы.
- [ ] Варианты используют один template.
- [ ] Ни один вариант не добавляет факты, которых нет в canonical plan.
- [ ] Ось различий описана в документации.

### Этап 5. Layout Matching

Для каждого слайда:

1. Отфильтровать layouts по обязательным slot types.
2. Проверить capacity и количество компонентов.
3. Проверить visual area и aspect ratio.
4. Рассчитать score.
5. Выбрать concrete layout ID.
6. Сохранить кандидатов и причины выбора для диагностики.

Пример score:

```text
score =
  slot_compatibility
  semantic_role_match
  content_capacity
  visual_area_match
  variant_policy_match
- overflow_risk
- fallback_penalty
```

Definition of Done:

- [ ] LLM не выбирает координаты.
- [ ] Результат детерминирован при одинаковом входе.
- [ ] Fallback явно помечается warning.
- [ ] Первый слайд не использует случайный content layout.
- [ ] Контентные слайды не используют cover layout без причины.
- [ ] Выбор layout можно объяснить в debug metadata.

### Этап 6. Composition

Действия:

1. Назначить components конкретным slots.
2. Рассчитать frames.
3. Применить template style tokens.
4. Выполнить text fit.
5. При overflow попробовать другой layout или text compression.
6. Создать `RenderedSlideIR`.

Definition of Done:

- [ ] Каждый элемент имеет стабильный ID.
- [ ] Каждый элемент имеет frame и source slot.
- [ ] Координаты нормализованы или однозначно переводятся в EMU.
- [ ] Неиспользованные placeholders не показываются.
- [ ] Нет скрытого размещения, существующего только внутри renderer.
- [ ] RenderedSlideIR сохраняется до создания PPTX.

### Этап 7. Native PPTX Rendering

Действия:

1. Открыть копию исходного template.
2. Удалить исходные демонстрационные слайды, сохранив masters/layouts/theme.
3. Создать слайды на concrete layouts.
4. Создать нативные объекты.
5. Сохранить PPTX.
6. Повторно открыть PPTX библиотекой для smoke validation.

Маппинг:

- text → PowerPoint text/placeholder;
- table → PowerPoint table;
- chart → PowerPoint chart;
- image → image shape;
- diagram → shapes/connectors;
- icon → SVG/PNG asset или native shape;
- slide chrome → master/layout, а не ручное дублирование без необходимости.

Definition of Done:

- [ ] PPTX открывается без repair warning.
- [ ] Текст редактируется.
- [ ] Таблицы редактируются.
- [ ] Диаграммы остаются диаграммами.
- [ ] Слайд не является одной растровой картинкой.
- [ ] Используется layout из исходного template.
- [ ] В файл не попадают alt-text placeholders вместо изображения.

### Этап 8. PDF, PNG и HTML

Действия:

1. Конвертировать PPTX в PDF через LibreOffice с отдельным profile dir.
2. Получить PNG previews.
3. Сформировать HTML из `RenderedDeckIR`.
4. Сохранить artifacts и обновить variant atomically.

Definition of Done:

- [ ] Параллельные LibreOffice jobs не используют один profile.
- [ ] Есть timeout и принудительное завершение зависшего процесса.
- [ ] PNG соответствует актуальной revision.
- [ ] HTML содержит selectable text и отдельные элементы.
- [ ] Ошибка PDF не уничтожает успешный PPTX.
- [ ] Старые artifacts не выдаются как актуальные после revision.

### Этап 9. Audit

Детерминированные проверки выполняются по `RenderedSlideIR` и фактическому PPTX. Контекстуальные проверки выполняются по содержанию и, при наличии VLM, по PNG.

Definition of Done:

- [ ] AuditReport хранится в PostgreSQL.
- [ ] Issue имеет slide ID, severity, code, message и bbox, если применимо.
- [ ] Issue сообщает, доступно ли исправление.
- [ ] API отдаёт полный audit, а не только `passed`.
- [ ] Frontend может подсветить issue на preview.
- [ ] Проверки разделены на deterministic и contextual.
- [ ] Версия audit rules сохранена.

### Этап 10. Исправление отдельного слайда

Поток:

```text
correction prompt
  + current SemanticSlidePlan
  + canonical DeckPlan
  + TemplateIR summary
  + current audit issues
        |
        v
new SemanticSlidePlan
        |
Layout Matcher + Composer
        |
new RenderedSlideIR
        |
single-slide preview + audit
        |
new immutable SlideRevision
```

После принятия revision актуальный вариант полностью пересобирается из актуальных `RenderedSlideIR`. Это надёжнее изменения OOXML существующего файла на месте.

Definition of Done:

- [ ] Изменяется только выбранный slide.
- [ ] Остальные semantic plans и revisions не меняются.
- [ ] Старую revision можно восстановить.
- [ ] Одновременные изменения одного slide защищены lock или optimistic version check.
- [ ] Новый preview не смешивается со старым кэшем.
- [ ] После изменения PPTX/PDF/HTML помечаются stale.
- [ ] Экспорт пересобирается из актуальных revisions.
- [ ] Correction prompt также проходит moderation/length validation.

## 7. Минимальный API

### Создание

```text
POST /api/v1/decks
Content-Type: multipart/form-data

template
prompt
slide_count
idempotency_key
```

Ответ:

```json
{
  "deck_id": "uuid",
  "job_id": "uuid",
  "status": "queued"
}
```

### Jobs и progress

```text
GET /api/v1/jobs/{job_id}
GET /api/v1/jobs/{job_id}/events
```

### Результат

```text
GET /api/v1/decks/{deck_id}
GET /api/v1/decks/{deck_id}/variants
GET /api/v1/variants/{variant_id}/slides
GET /api/v1/slides/{slide_id}
```

### Исправление слайда

```text
POST /api/v1/slides/{slide_id}/revisions

{
  "prompt": "Сделай заголовок короче и замени таблицу графиком",
  "base_revision": 2
}
```

### Revision control

```text
GET  /api/v1/slides/{slide_id}/revisions
POST /api/v1/slides/{slide_id}/revisions/{revision_id}/activate
```

### Audit

```text
GET  /api/v1/variants/{variant_id}/audit
GET  /api/v1/slides/{slide_id}/audit
POST /api/v1/slides/{slide_id}/audit-issues/{issue_id}/fix
```

### Export

```text
GET /api/v1/variants/{variant_id}/export/pptx
GET /api/v1/variants/{variant_id}/export/pdf
GET /api/v1/variants/{variant_id}/export/html
```

## 8. State machines

### 8.1 GenerationJob

```text
QUEUED
  -> RUNNING
      -> SUCCEEDED
      -> PARTIAL
      -> FAILED
      -> TIMED_OUT
      -> CANCELLED
```

`PARTIAL` используется, если часть вариантов готова, а часть упала. Job не должен называться успешным только потому, что orchestration function вернула объект с ошибками внутри вариантов.

### 8.2 DeckVariant

```text
PENDING
  -> COMPOSING
  -> RENDERING
  -> AUDITING
  -> READY
  -> STALE
  -> FAILED
```

`STALE` означает, что revision слайда уже изменилась, но export ещё не пересобран.

## 9. Требования к аудиту

### 9.1 Вёрстка

- [ ] Элемент не выходит за границы слайда.
- [ ] Блоки не пересекаются с учётом допустимых декоративных пересечений.
- [ ] Текст помещается в рамку.
- [ ] Текст не обрезан краем слайда.
- [ ] Элементы выровнены по направляющим/сетке.
- [ ] Контент не заходит в safe margins.
- [ ] Изображение сохраняет aspect ratio.

### 9.2 Соответствие шаблону

- [ ] Шрифты входят в извлечённую систему шаблона или fallback явно зафиксирован.
- [ ] Используется не больше двух гарнитур без обоснования.
- [ ] Кегль входит в typography scale или допустимое отклонение.
- [ ] Цвет входит в палитру шаблона.
- [ ] Слайд использует layout исходного template.
- [ ] Logo/footer/slide number не сдвинуты.
- [ ] Контраст текста к фактическому фону не ниже 4.5:1 для обычного текста.

### 9.3 Плотность

- [ ] Не больше 6 bullets.
- [ ] Bullet не длиннее 15 слов.
- [ ] Таблица не больше 7 строк и 5 колонок.
- [ ] Диаграмма содержит не больше 5 series.
- [ ] Заполнение слайда находится примерно между 25% и 75% либо отклонение обосновано типом layout.

### 9.4 Целостность

- [ ] PPTX открывается без восстановления.
- [ ] Нет `Lorem ipsum`, `XXX`, `TODO`, `заглушка`, `ТБД`, `вставьте текст`.
- [ ] Нет пустого слайда.
- [ ] Нет слайда только с заголовком, кроме явно допустимого cover/section layout.
- [ ] Слайд не экспортирован одной картинкой.
- [ ] Диаграммы имеют необходимые labels/units/legend.
- [ ] Нет дублирующихся слайдов.

### 9.5 Контекстуальный аудит

- [ ] Заголовок содержит вывод.
- [ ] Содержание соответствует заголовку.
- [ ] Слайд можно пересказать одним предложением.
- [ ] Все факты и цифры имеют источник.
- [ ] Картинки и иконки относятся к теме.
- [ ] Нет prompt fragments и реплик разработчиков.
- [ ] Нет явных опечаток.
- [ ] Колода использует один язык.
- [ ] Таблица и legend работают на основную мысль.
- [ ] Соседние слайды связаны по логике.

## 10. Матрица требований хакатона

| Требование | Backend-реализация | Доказательство на демо | Статус |
|---|---|---|---|
| Неизвестный PPTX | Template Analyzer без template-specific hardcode | Живой запуск на новом шаблоне | Частично |
| Декомпозиция шаблона | TemplateIR, tokens, layouts, typography, components | Экран/JSON анализа | Частично |
| Структура до верстки | Canonical DeckPlan | Сохранённый JSON и стадия pipeline | Частично |
| 10–15 слайдов | API parameter + schema validation | Генерация 10–15 слайдов | Готово частично |
| Меньше 5 минут | Timings, parallel work, cache | Логи полного запуска | Не доказано |
| Три варианта | Один DeckPlan + A/B/C policies | Три вкладки одного deck | Требует изменения |
| Визуально разные варианты | Разные layout/visual policies | Side-by-side preview | Не доказано |
| Графики | Native PowerPoint chart | Редактирование в PowerPoint | Реализовано базово |
| Таблицы | Native PowerPoint table | Редактирование в PowerPoint | Реализовано базово |
| Диаграммы/SmartArt | Native shapes/connectors | Process slide | Не реализовано |
| Пиктограммы | Asset/icon pipeline | Иконка как отдельный объект | Не реализовано |
| Изображения | Настоящий image asset | Image shape в PPTX | Не реализовано |
| Детерминированный аудит | Geometry/style/density/integrity | Issues с bbox | Частично |
| Контекстуальный аудит | LLM/VLM checks | Issues на preview | Частично |
| Выбор исправлений | Issue/fix API | Исправление выбранного issue | Не реализовано |
| Исправление слайда промптом | Immutable SlideRevision | До/после одного слайда | Не реализовано |
| PPTX export | Native renderer | Скачать и открыть | Реализовано базово |
| PDF export | LibreOffice worker | Скачать PDF | Реализовано базово |
| HTML export | Renderer из RenderedDeckIR | Открыть HTML | Не реализовано |
| Prompt versioning | YAML skills | Версия в repo/job metadata | Реализовано частично |
| Reproducible setup | Containers, migrations, config | Deploy с чистого сервера | Частично |
| Девять результатов | 3 templates × 3 variants | Набор artifacts | Не доказано |
| README | Setup/env/limits | Репозиторий | Отсутствует |
| ARCHITECTURE | Pipeline и границы | Репозиторий | Есть, требует обновления |
| MODELS | Модели/лицензии/ресурсы | Репозиторий | Отсутствует |
| AUDIT | Правила и coverage | Репозиторий | Отсутствует |

## 11. Инфраструктура

### 11.1 Nginx checklist

- [ ] TLS и автоматическое обновление сертификата.
- [ ] `/` проксируется на frontend.
- [ ] `/api/` проксируется на FastAPI.
- [ ] SSE работает с отключённой proxy buffering.
- [ ] `client_max_body_size` соответствует максимальному PPTX.
- [ ] Для обычных API установлены разумные timeouts.
- [ ] Генерация не удерживает HTTP request пять минут.
- [ ] Downloads не проходят через Python целиком, если можно использовать Nginx/X-Accel или signed URL.
- [ ] Rate limit на создание deck и slide revision.
- [ ] Security headers.
- [ ] Корректные forwarded headers.

### 11.2 PostgreSQL checklist

- [ ] Миграции выполняются отдельно до запуска новой версии API.
- [ ] Есть backup policy.
- [ ] Есть индексы на foreign keys, statuses, created_at и idempotency key.
- [ ] JSONB используется для IR, но основные статусы и связи остаются колонками.
- [ ] Job и deck создаются транзакционно.
- [ ] Активация revision выполняется транзакционно.
- [ ] Нет хранения больших бинарных файлов.

### 11.3 Redis checklist

- [ ] Настроена максимальная память и eviction policy.
- [ ] Очередь не использует volatile cache keys как единственный источник задач.
- [ ] Job может быть восстановлен после рестарта worker.
- [ ] Locks имеют TTL.
- [ ] Повторная доставка задачи безопасна.
- [ ] Progress events дублируются окончательным состоянием в PostgreSQL.

### 11.4 Worker checklist

- [ ] Ограничена concurrency LLM.
- [ ] Ограничена concurrency LibreOffice.
- [ ] У каждого subprocess timeout.
- [ ] Worker корректно завершает текущую задачу при deploy либо задача возвращается в очередь.
- [ ] Есть heartbeat.
- [ ] Есть retry policy по типам ошибок.
- [ ] Ошибки в данных не ретраятся бесконечно.
- [ ] Все задачи идемпотентны либо защищены idempotency key.

## 12. CI/CD

### 12.1 Pull request pipeline

```text
format/lint
  -> unit tests
  -> integration tests
  -> security/static checks
  -> Docker build
```

Checklist:

- [ ] Ruff/lint.
- [ ] Unit tests.
- [ ] Integration tests без внешнего LLM.
- [ ] Проверка миграций.
- [ ] Docker image build.
- [ ] Проверка отсутствия secrets.
- [ ] Dependency vulnerability scan, если укладывается в сроки.
- [ ] Test report сохраняется artifact.

### 12.2 Main/deploy pipeline

```text
tests
  -> build immutable images
  -> push registry
  -> deploy over SSH/runner
  -> migrate database
  -> update API/worker/frontend
  -> readiness checks
  -> smoke test
```

Checklist:

- [ ] Image имеет tag commit SHA.
- [ ] Deploy не использует `latest` как единственную версию.
- [ ] Конфиги и secrets не запекаются в image.
- [ ] Миграция совместима с предыдущей версией приложения.
- [ ] Есть rollback на предыдущий image.
- [ ] После deploy проверяются API, worker, PostgreSQL, Redis и LibreOffice.
- [ ] Smoke test создаёт или проверяет тестовый job.

## 13. Наблюдаемость

Структурные logs должны содержать:

- request ID;
- job ID;
- deck ID;
- variant ID;
- slide ID;
- pipeline stage;
- duration;
- model и prompt version;
- retry number;
- error code.

Метрики:

- общее время deck generation;
- время template parsing;
- LLM latency и failure rate;
- время на один slide;
- LibreOffice latency/failures;
- доля успешных A/B/C variants;
- число audit issues;
- число slide revisions;
- длина очереди;
- активные workers;
- cache hit template analysis.

Минимальные endpoints:

- `/health/live` — процесс отвечает;
- `/health/ready` — обязательные зависимости доступны;
- worker heartbeat;
- `/metrics`, если используется Prometheus.

## 14. Безопасность

- [ ] Ограничен размер upload.
- [ ] Проверяется ZIP central directory до полной распаковки.
- [ ] Есть лимит количества файлов и uncompressed size.
- [ ] Запрещён path traversal.
- [ ] Исходное имя файла не используется как storage path.
- [ ] LibreOffice запускается без shell interpolation.
- [ ] Worker работает без root.
- [ ] Артефакты одного пользователя нельзя получить перебором UUID, если появится авторизация.
- [ ] Download имеет content disposition и корректный MIME.
- [ ] Prompt и документы не пишутся полностью в production logs.
- [ ] API keys находятся только в secrets.
- [ ] Временные файлы удаляются после задачи.
- [ ] Есть retention policy для пользовательских данных.

## 15. План реализации

### Фаза 0. Зафиксировать контракты

Результат:

- согласованные `TemplateIR`, `DeckPlan`, `SemanticSlidePlan`, `RenderedSlideIR`;
- A/B/C policies;
- API schema;
- state machines;
- перечень поддерживаемых компонентов.

Checklist:

- [ ] Backend, ML и frontend используют одинаковые названия сущностей.
- [ ] JSON schemas лежат в коде и тестируются.
- [ ] Решено, добавляется ли content pack до сдачи.
- [ ] Определён storage backend.
- [ ] Выбран worker framework.

### Фаза 1. Production skeleton

Результат:

- PostgreSQL;
- migrations;
- Redis;
- worker;
- storage abstraction;
- jobs и SSE;
- Nginx local/staging config.

Checklist:

- [ ] Job переживает рестарт API.
- [ ] Worker может продолжить или безопасно повторить задачу.
- [ ] Frontend получает progress.
- [ ] Artifact доступен после завершения job.

### Фаза 2. Перенос текущего pipeline в worker

Результат:

- существующая генерация вызывается из persistent job;
- результаты сохраняются как Deck/Variant/Slide;
- текущие PPTX/PDF/PNG доступны через новый API.

Checklist:

- [ ] Нет FastAPI `BackgroundTasks` для генерации.
- [ ] Variant failure корректно даёт `PARTIAL`.
- [ ] Timings сохраняются.
- [ ] Повторная задача не создаёт дубликаты artifacts.

### Фаза 3. Канонический DeckPlan и варианты

Результат:

- один план;
- три policies;
- три набора semantic slides;
- подтверждённая визуальная разница.

Checklist:

- [ ] A/B/C не расходятся по фактам.
- [ ] Варианты сравниваются автоматическими признаками и визуально.
- [ ] Ось различий описана в ARCHITECTURE.

### Фаза 4. RenderedSlideIR и composition

Результат:

- concrete layout matching;
- frames/styles сохраняются;
- renderer больше не принимает ключевые layout-решения скрыто.

Checklist:

- [ ] Один IR используется preview/audit/export.
- [ ] Layout selection объясним.
- [ ] Fallbacks видны в diagnostics.

### Фаза 5. Slide revisions

Результат:

- prompt исправления;
- revision history;
- optimistic locking;
- single-slide preview;
- stale/rebuild export.

Checklist:

- [ ] Исправление не регенерирует narrative всей колоды.
- [ ] Можно вернуться к revision N-1.
- [ ] Старый export не выдаётся как актуальный.

### Фаза 6. Полный audit и обязательные exports

Результат:

- audit issues с bbox;
- selective fix;
- HTML;
- настоящие image assets;
- исправленные лимиты и integrity checks.

Checklist:

- [ ] Закрыт Appendix 1 либо каждое исключение обосновано.
- [ ] Пользователь видит и выбирает issues.
- [ ] Есть PPTX/PDF/HTML.
- [ ] PPTX нативный.

### Фаза 7. CI/CD и hardening

Результат:

- staging/production deploy;
- monitoring;
- backup;
- load and timeout tests;
- документация.

Checklist:

- [ ] Clean deploy проходит по README.
- [ ] Новый commit автоматически разворачивается после checks.
- [ ] Есть rollback.
- [ ] Полная генерация стабильно укладывается в 5 минут.

### Фаза 8. Репетиция сдачи

Результат:

- три шаблона;
- по три варианта;
- неизвестный контрольный шаблон;
- записанный сценарий семиминутного демо;
- подготовленные ответы по ограничениям.

Checklist:

- [ ] Получены 9 требуемых презентаций.
- [ ] Все PPTX открываются без repair warning.
- [ ] На каждом результате есть audit report.
- [ ] Продемонстрировано исправление одного слайда.
- [ ] Продемонстрирован нативный объект в PowerPoint.
- [ ] Показана работа на неизвестном шаблоне.
- [ ] Демо укладывается в 7 минут.

## 16. Adversarial review

Ниже решение рассматривается с позиции эксперта, который пытается доказать, что оно не выполняет кейс.

### Атака 1. «Вы заточились под три шаблона»

Как атакуют:

- загружают шаблон с несколькими masters;
- layout names имеют вид `Custom Layout 17`;
- placeholders наследуют geometry от master;
- реальный дизайн находится на sample slides, а не в theme XML.

Почему текущая версия уязвима:

- классификация сильно зависит от имён и типов placeholders;
- унаследованная geometry может пропускаться;
- реальные слайды почти не используются для извлечения дизайн-системы.

Защита:

- stable IDs master/layout;
- inheritance resolution;
- анализ sample slides;
- geometry-based classification;
- VLM только для неоднозначной семантики;
- тест на минимум один unseen template.

Gate перед сдачей:

- [ ] Команда генерирует deck на шаблоне, который не использовался при разработке.

### Атака 2. «Три варианта — это три случайных ответа LLM»

Как атакуют:

- сравнивают факты и порядок выводов;
- находят разные цифры и противоречащие тезисы;
- варианты отличаются текстом, но визуально одинаковы.

Защита:

- canonical DeckPlan;
- фиксированные A/B/C policies;
- provenance facts;
- automated comparison layout distribution и component mix;
- одинаковый template и общий набор key messages.

Gate:

- [ ] Для каждого deck сохранено доказательство общей основы и визуальных различий.

### Атака 3. «PPTX не нативный»

Как атакуют:

- открывают файл в PowerPoint;
- пытаются изменить текст, данные диаграммы и ячейку таблицы;
- проверяют, не является ли слайд одной картинкой.

Защита:

- native objects;
- roundtrip tests;
- demo редактирования;
- integrity audit shape types.

Gate:

- [ ] На защите редактируется текст, таблица и chart data.

### Атака 4. «Аудит декоративный»

Как атакуют:

- намеренно создают overflow;
- помещают светлый текст на светлый нестандартный фон;
- загружают слайд с `вставьте текст`;
- спрашивают, почему issue нельзя выбрать и исправить.

Уязвимости текущей реализации:

- contrast считается относительно одного theme background;
- slide index для contrast может быть неверным;
- полный AuditReport не выдаётся клиенту;
- repair loop отсутствует;
- проверяется не весь набор shapes.

Защита:

- audit фактического PPTX;
- background per element/slide;
- bbox и issue IDs;
- selective fix API;
- regression fixtures с намеренно плохими файлами.

Gate:

- [ ] В live demo проблема обнаруживается, отображается и исправляется.

### Атака 5. «Исправление одного слайда меняет всю презентацию»

Как атакуют:

- сохраняют screenshots всех слайдов;
- исправляют один;
- сравнивают hashes/revisions остальных;
- отправляют два одновременных correction prompts.

Защита:

- immutable SlideRevision;
- base revision в запросе;
- optimistic lock;
- layout/composition только выбранного слайда;
- deck export собирается из актуальных revisions.

Gate:

- [ ] После correction у остальных слайдов не меняются semantic/rendered IR hashes.

### Атака 6. «После рестарта всё исчезает»

Как атакуют:

- перезапускают API во время job;
- перезапускают worker;
- очищают Redis;
- открывают готовый deck через сутки.

Защита:

- PostgreSQL source of truth;
- persistent artifact storage;
- acknowledgements after durable writes;
- идемпотентные stages;
- reconciliation зависших jobs.

Gate:

- [ ] Пройден restart test API и worker.

### Атака 7. «Двойной клик создаёт две дорогие генерации»

Как атакуют:

- повторяют POST;
- браузер ретраит запрос;
- очередь доставляет task дважды.

Защита:

- idempotency key;
- unique DB constraint;
- stage-level artifact keys;
- at-least-once safe worker.

Gate:

- [ ] Десять одинаковых запросов создают одну logical job.

### Атака 8. «Пять минут соблюдаются только в идеальном случае»

Как атакуют:

- шаблон содержит много layouts и sample slides;
- LLM отвечает медленно;
- один slide дважды не проходит schema validation;
- три LibreOffice процесса конкурируют за CPU/RAM.

Защита:

- общий deadline и per-stage budgets;
- кэш TemplateIR;
- bounded concurrency;
- один canonical plan;
- batched calls там, где это оправдано;
- graceful partial result;
- timings и load test на целевом сервере.

Gate:

- [ ] P95 полной генерации на целевом хосте меньше 5 минут.

### Атака 9. «Не поддерживается настоящий корпоративный дизайн»

Как атакуют:

- сравнивают typography и spacing с sample slides;
- проверяют logo/footer;
- используют нестандартные fonts;
- проверяют цвета диаграмм.

Защита:

- реальные style tokens, не только theme defaults;
- font availability report;
- fallback warning;
- component styles из sample slides;
- template conformance audit.

Gate:

- [ ] Для каждого template сформирован отчёт извлечённых tokens и fallback warnings.

### Атака 10. «Изображений фактически нет»

Как атакуют:

- выбирают content, явно требующий изображения;
- обнаруживают серый placeholder с alt text;
- проверяют image shape в PPTX.

Защита:

- asset model;
- вставка исходных content images или разрешённых visual assets;
- crop/aspect ratio handling;
- attribution/licensing policy;
- text-to-image только как дополнительный provider.

Gate:

- [ ] Хотя бы один demo deck содержит настоящее изображение как отдельный PPTX object.

### Атака 11. «HTML — это просто набор картинок»

Как атакуют:

- выделяют текст;
- проверяют DOM;
- отключают изображения.

Защита:

- HTML renderer из RenderedDeckIR;
- text как DOM text;
- charts как SVG/canvas или отдельные элементы;
- PNG используется только как preview/fallback.

Gate:

- [ ] Текст в HTML выделяется и находится поиском браузера.

### Атака 12. «Генерация падает из-за одного слайда»

Как атакуют:

- заставляют один slide получить невалидный JSON;
- проверяют состояние остальных вариантов;
- проверяют, не помечена ли job как DONE при фактическом провале.

Защита:

- ограниченные retries;
- fallback content/layout;
- состояния PARTIAL и FAILED;
- видимые ошибки по variant/slide;
- сохранение уже готовых результатов.

Gate:

- [ ] Fault injection одного slide не уничтожает готовые варианты.

### Атака 13. «Контент галлюцинирует»

Как атакуют:

- дают короткий prompt без чисел;
- ищут выдуманные проценты и суммы;
- исправлением просят добавить неподтверждённый факт.

Защита:

- evidence IDs;
- запрет ungrounded numbers;
- deterministic extraction чисел;
- semantic audit;
- correction prompt не отменяет grounding rules.

Gate:

- [ ] Каждое число можно связать с входом или помеченной derived calculation.

### Атака 14. «Ваш audit сам содержит ошибки»

Как атакуют:

- смотрят false positives на декоративных overlaps;
- проверяют контраст на цветном фоне;
- сравнивают номера слайдов issue и preview;
- проверяют лимит таблицы из ТЗ.

Защита:

- audit fixtures;
- явные exclusions для декоративных групп;
- реальный background sampling/style resolution;
- единая индексация slide ID вместо смешения 0/1-based indices;
- лимит таблицы: максимум 7 строк и 5 колонок.

Gate:

- [ ] Набор synthetic bad slides даёт ожидаемые issue codes и bbox.

### Атака 15. «CI/CD есть только на схеме»

Как атакуют:

- просят развернуть чистую версию;
- проверяют migrations и rollback;
- смотрят, переживают ли artifacts deploy.

Защита:

- реальный staging deploy;
- immutable image tags;
- persistent volumes;
- health/readiness checks;
- documented rollback.

Gate:

- [ ] Новый сервер разворачивается по README без ручного редактирования контейнера.

## 17. Главные риски и решения

| Риск | Вероятность | Влияние | Решение |
|---|---:|---:|---|
| Неизвестный шаблон не классифицируется | Высокая | Критическое | Rich TemplateIR, fallback layouts, unseen-template test |
| Слишком большой scope | Высокая | Критическое | Две страницы, без canvas editor и микросервисов |
| Три варианта противоречат друг другу | Высокая | Высокое | Canonical DeckPlan |
| Нет настоящих изображений | Высокая | Высокое | Asset pipeline до сдачи |
| Audit не соответствует приложению ТЗ | Высокая | Высокое | Coverage matrix и bad-slide fixtures |
| Генерация дольше 5 минут | Средняя | Критическое | Cache, budgets, bounded concurrency, P95 test |
| Job теряется при deploy | Средняя | Высокое | PostgreSQL + durable queue + idempotency |
| LibreOffice зависает | Средняя | Высокое | Timeout, isolated profile, process cleanup |
| Отсутствующий corporate font меняет layout | Высокая | Высокое | Font report, fallback warning, bundled allowed fonts |
| Slide correction ломает export consistency | Средняя | Высокое | Revisions и STALE state |
| HTML не готов к сроку | Средняя | Высокое | Простой absolute-position renderer из RenderedIR |
| Недостаточная документация | Высокая | Среднее | README/MODELS/AUDIT как deliverables, не финальная уборка |

## 18. Критические решения, которые нельзя отложить

До дальнейшей активной разработки команда должна зафиксировать:

- [ ] Форматы четырёх IR-контрактов.
- [ ] Конкретные A/B/C policies.
- [ ] Celery или Dramatiq.
- [ ] PostgreSQL schema и migration tool.
- [ ] Локальный volume или S3/MinIO.
- [ ] Будет ли content pack в сдаваемой версии.
- [ ] Каким способом получаются реальные изображения/иконки.
- [ ] Какая VLM используется и обязательна ли она для MVP.
- [ ] Максимальный размер template.
- [ ] Политика partial success.
- [ ] Порог общего timeout и бюджеты стадий.

## 19. Definition of Done всего проекта

Функциональность:

- [ ] Первая страница принимает prompt и PPTX.
- [ ] Вторая страница показывает три варианта.
- [ ] Конкретный слайд исправляется промптом.
- [ ] Можно вернуться к старой revision.
- [ ] Есть PPTX, PDF и HTML.
- [ ] Пользователь видит audit issues и выбирает исправления.

Соответствие хакатону:

- [ ] 10–15 слайдов или выбранное число.
- [ ] Три визуально различимых варианта.
- [ ] Генерация укладывается в 5 минут на целевом сервере.
- [ ] Решение работает на неизвестном шаблоне.
- [ ] PPTX состоит из нативных объектов.
- [ ] Есть графики, таблицы, diagram/shapes, icons и изображения.
- [ ] Есть deterministic и contextual audit.
- [ ] Получено девять презентаций для промежуточной демонстрации.

Эксплуатация:

- [ ] PostgreSQL, Redis и artifact storage persistent.
- [ ] Nginx настроен.
- [ ] CI/CD реально разворачивает приложение.
- [ ] Jobs переживают рестарты.
- [ ] Есть healthchecks, logs и timings.
- [ ] Есть backup и rollback.

Документация:

- [ ] `README.md`: setup, environment, запуск, ограничения.
- [ ] `ARCHITECTURE.md`: актуальная архитектура и границы слоёв.
- [ ] `MODELS.md`: модели, лицензии, ссылки и системные требования.
- [ ] `AUDIT.md`: правила, deterministic/contextual classification и test coverage.
- [ ] A/B/C axis описана и обоснована.
- [ ] Known limitations перечислены честно.

Демонстрация:

- [ ] Сценарий укладывается в 7 минут.
- [ ] Показана загрузка неизвестного шаблона.
- [ ] Показаны три варианта.
- [ ] Показано исправление одного слайда.
- [ ] Показан audit issue и его исправление.
- [ ] PPTX открыт в PowerPoint/LibreOffice и один объект отредактирован.
- [ ] Показаны architecture и audit boundaries.

## 20. Рекомендуемый сценарий семиминутного демо

1. **Проблема, 30 секунд.** Контент генерируется легко, корпоративный дизайн воспроизводится плохо.
2. **Подход, 45 секунд.** TemplateIR + canonical DeckPlan + deterministic composition.
3. **Загрузка, 30 секунд.** Prompt и неизвестный PPTX.
4. **Progress, 30 секунд.** Показать реальные стадии worker.
5. **Три варианта, 60 секунд.** Объяснить A/B/C axis.
6. **Исправление слайда, 60 секунд.** Prompt → новая revision → новый preview.
7. **Audit, 60 секунд.** Показать deterministic и contextual issue, bbox и fix.
8. **Native PPTX, 45 секунд.** Скачать и отредактировать текст/chart/table.
9. **Архитектура, 45 секунд.** API, worker, PG, Redis, storage, ML boundary.
10. **Вывод, 30 секунд.** Новый шаблон, воспроизводимость, менее 5 минут.

Оставшееся время — резерв на задержку интерфейса и вопросы.
