# Архитектура: «Цифровой дизайнер презентаций» (SlideOps / «Шмякс»)

**Актуально на:** 2026-09-28, коммит `8c92eec` (feat: frontend v2).
**Назначение документа:** карта проекта *как он есть сейчас* — для передачи как контекст другому разработчику/агенту. Описывает реальный код, а не план.

Связанные документы:
- [HACKATHON_BACKEND_PLAN.md](HACKATHON_BACKEND_PLAN.md) — целевой план бэкендера (местами опережает реализацию, см. §13).
- [README.md](README.md) — запуск, деплой, контракт фронта.
- [docs/archive/ADR_v1_sprints.md](docs/archive/ADR_v1_sprints.md) — исходный ADR и спринт-промпты (исторический, **не описывает текущий код**).

---

## 0. TL;DR

- Пользователь загружает корпоративный `.pptx` + бриф → сервис генерирует **3 варианта** презентации (A Executive, B Analytical, C Pitch) из **нативных** объектов PowerPoint, проводит аудит, отдаёт PNG-превью, экспорт PPTX/PDF/HTML и позволяет **перегенерировать отдельный слайд** с историей ревизий.
- Модульный монолит: **FastAPI** (API) + **Celery worker** (генерация) на одном коде; **PostgreSQL** — источник истины (jobs, результаты, ревизии), **Redis** — брокер и distributed lock; файлы — в общем docker volume.
- Фронт: **React 19 + Vite + TS**, две страницы (старт / результат), отдаётся nginx-контейнером, который же проксирует `/api/`.
- LLM: любой OpenAI-совместимый endpoint (vLLM/TGI/OpenRouter). Промпты — версионированные YAML в `skills/`.
- Тесты: 106 pass / 1 fail (известный, §12) / 1 skip. Ruff чистый. У фронта тестов нет.
- **Главные проблемы сейчас** (§12): фронт и бэк разошлись в контракте (варианты, аудит), гонка при параллельных правках слайдов одного варианта, фронт в dev-режиме по умолчанию ходит в прод.

---

## 1. Топология развёртывания

```text
Internet
   │
 внешний Nginx (на хосте, TLS)
   ├── /       → 127.0.0.1:5173  frontend  (nginx:1.27, SPA из dist/)
   └── /api/   → 127.0.0.1:1494  app       (uvicorn, FastAPI)
                                  │
   frontend-контейнер тоже проксирует /api/ → app:1494 (для локального запуска без внешнего Nginx)

 docker compose:
   postgres:17   ← источник истины (generation_jobs, slide_revisions)
   redis:7       ← Celery broker/backend + Redis lock для правок слайдов
   migrate       ← одноразовый: alembic upgrade head
   init-storage  ← одноразовый: chown volume на uid 10001
   app           ← FastAPI :1494, healthcheck /health/ready
   worker        ← celery -A app.worker.celery_app worker --concurrency=2
   frontend      ← nginx :80 (наружу 127.0.0.1:5173)
   volume generated_storage → /app/storage (общий для app и worker)
```

Бэкенд монтирует роутер **дважды**: без префикса и с `/api` ([app/api/main.py](app/api/main.py)) — работает независимо от того, срезает ли внешний Nginx префикс.

### Два режима исполнения

| | Queue mode (compose, прод) | In-process fallback (локально/тесты) |
|---|---|---|
| `TASK_QUEUE_ENABLED` | `true` | `false` (дефолт) |
| Где выполняется генерация | Celery worker | FastAPI `BackgroundTasks` в процессе API |
| БД | PostgreSQL, схема через Alembic | `sqlite+pysqlite:///:memory:` + `StaticPool`, схема через `create_all` |
| Лок на правку слайда | Redis lock | нет (no-op) |

Переключатель — [app/config.py](app/config.py) + [routes.py:112 `_enqueue_job`](app/api/routes.py:112).

---

## 2. Карта репозитория

```text
app/
  config.py                 Settings (pydantic-settings, читает .env) — все переменные в §10
  api/
    main.py                 FastAPI app, CORS, router смонтирован на "/" и "/api"
    routes.py               ВСЕ эндпоинты (§6), сборка payload для /result, метрики варианта
    schemas.py              DTO: JobStatusResponse, VariantResultDTO, SlideRevisionRequest (+ as_instructions), ...
  pipeline/
    jobs.py                 SQLAlchemy-модели (JobRow, SlideRevisionRow), JobStore, SlideRevisionStore,
                            сериализация ResultPackage ↔ result_json
    service.py              run_generation_job / run_revision_job — «сервисный слой» между очередью и orchestrator:
                            статусы, прогресс, сохранение ревизий, Redis-лок
    orchestrator.py         generate_deck (3 варианта параллельно), revise_variant_slide,
                            rebuild_variant_with_slide; VariantResult / ResultPackage dataclasses
  worker/
    celery_app.py           Celery config (acks_late, prefetch=1, time limits = pipeline_timeout + 60/120)
    tasks.py                presentation.generate_deck, presentation.revise_slide → asyncio.run(service.*)
  core/
    parser/                 .pptx → TemplateManifest (§7.1)
    agents/                 LLM-агенты + детерминированные проверки (§7.2)
    builder/                TemplateManifest + PresentationIR → нативный .pptx (§7.4)
    auditor/                детерминированный + семантический аудит (§7.5)
    exporter/               PDF (LibreOffice), PNG-превью (pdf2image), HTML-вьювер (§7.6)
  models/
    template_manifest.py    TemplateManifest, LayoutManifest, LayoutSlot(inferred), BrandProfile, LayoutType
    outline.py              Outline / OutlineItem (план колоды)
    presentation_ir.py      PresentationIR / SlideIR / 8 типов компонентов (§7.3)
    audit_report.py         AuditReport / AuditIssue / IssueType / Severity
skills/                     версионированные промпты (YAML); грузится максимальная версия (§8)
alembic/ + alembic.ini      миграции; одна ревизия 0001_jobs_and_revisions
frontend/                   React SPA (§9)
scripts/
  deploy.sh                 выкладка на сервер с откатом на предыдущий образ
  generate_from_samples.py  CLI: samples/input/{*.pptx,*.pdf} → samples/output/generated/ через реальный LLM
tests/                      unit + integration (pytest), фикстуры шаблонов
.github/workflows/          ci.yml (lint/test/миграции/docker build), release.yml (GHCR + SSH deploy)
Dockerfile                  python:3.13-slim + LibreOffice Impress + poppler, uid 10001, порт 1494
docker-compose.yml          см. §1
main.py                     dev-запуск uvicorn на :8000 с reload (НЕ совпадает с 1494 в Docker)
docs/archive/               исторический ADR
samples/                    локальные входы/выходы для CLI-скрипта (в .gitignore)
```

---

## 3. Сквозной поток: генерация колоды

```text
POST /generate (multipart: template, brief, slide_count 10..15, purpose, language, style)
  │  routes.generate: валидация zip/ppt/presentation.xml, ≤ max_upload_bytes
  │  JobStore.create(GENERATE_DECK) → storage/{job_id}/template.pptx
  │  _enqueue_job → Celery generate_deck_task  |  BackgroundTasks
  ▼
service.run_generation_job(job_id)
  │  settings.n_slides_min = n_slides_max = job.slide_count   (точное число слайдов)
  │  brief += "Контекст презентации: назначение=…; язык=…; стиль=…"
  ▼
orchestrator.generate_deck  (asyncio.wait_for, pipeline_timeout_seconds=300)
  │ progress: analyzing_template 5 → extracting_design_system 20
  │ TemplateParser.parse(template)  — кэш .cache/{sha256}.v3.manifest.json
  │
  ├─ _build_variant("A") ┐
  ├─ _build_variant("B") ├ asyncio.gather — параллельно
  └─ _build_variant("C") ┘
       planning_structure 30   build_outline (narrative_architect, до 3 попыток, §7.2)
       matching_layouts 45     fill_slide × N (slot_filler, под llm_semaphore)
                               любой сбой слайда → build_fallback_slide (детерминированный, без LLM)
                               apply_visual_policy (bullets → comparison/process/icon_list)
       building_variants 60    PptxBuilder.build → storage/{job_id}/built_{V}.pptx + bbox_map
       auditing 80             run_full_audit (§7.5)
       exporting 90            convert_to_pdf → render_previews → export_html_viewer
                               (best-effort: при сбое LibreOffice остаётся только pptx)
  ▼
service: ≥1 успешный вариант → _save_initial_revisions (revision 1 для каждого слайда)
         статус DONE (все 3) | PARTIAL (1–2) | FAILED (0), progress 100
         result_json = весь ResultPackage (manifest + IR + outline + audit + пути к файлам)
```

Прогресс монотонный ([service.py:79](app/pipeline/service.py:79)): все три варианта шлют одинаковые стадии, отображается самый «быстрый».

## 4. Сквозной поток: правка слайда и ревизии

```text
POST /jobs/{id}/slides/{V}/{pos}/revise  {base_revision, comment, shorten_text, make_action_title,
                                          change_layout, add_visual, regenerate}
  │ проверка: вариант без ошибки, pos в диапазоне, base_revision == текущей (иначе 409)
  │ JobStore.create(REVISE_SLIDE, parent_job_id, variant, slide_position,
  │                 revision_request={instructions: request.as_instructions(), base_revision})
  │ parent.variant.export_state = "STALE"   → файлы варианта отдают 409 до завершения
  ▼
service.run_revision_job  (под Redis lock "slide-revision:{parent}:{V}:{pos}")
  │ идемпотентность: если по source_job_id ревизия уже сохранена → только снять STALE
  │ REVISE_SLIDE:       slide_reviser (LLM) → новый SlideIR
  │ ACTIVATE_REVISION:  SlideIR из slide_revisions.semantic_ir
  ▼
orchestrator.rebuild_variant_with_slide
  │ заменяет ОДИН слайд в IR (соседи не трогаются), полная пересборка варианта:
  │ build → audit → pdf → previews → html;   revision = старый + 1;  export_state = READY
  ▼
SlideRevisionStore.save (предыдущая активная ревизия слайда → is_active=false)
parent.result.variants[V] = новый VariantResult;  child: DONE, output={revision, revision_id, ...}
При ошибке: parent export_state возвращается в READY, child FAILED.
```

`POST .../revisions/{revision_id}/activate` — тот же путь с `JobType.ACTIVATE_REVISION` (откат слайда к старой ревизии без LLM).

---

## 5. Модель данных

### 5.1 Таблицы (Alembic `0001_jobs_and_revisions`, ORM в [jobs.py](app/pipeline/jobs.py))

**`generation_jobs`** — и колоды, и дочерние задачи правок в одной таблице.

| Поле | Смысл |
|---|---|
| `job_id` PK | UUID |
| `job_type` | `GENERATE_DECK` / `REVISE_SLIDE` / `ACTIVATE_REVISION` |
| `status` | `PENDING` → `RUNNING` → `DONE` / `PARTIAL` / `FAILED` |
| `stage`, `progress` | для SSE/поллинга (0..100) |
| `result_json` | JSON(B) сериализованного `ResultPackage` (только у GENERATE_DECK) |
| `output_json` | результат дочерней задачи: `{parent_job_id, variant, slide_position, revision, revision_id}` |
| `brief`, `purpose`, `slide_count`, `language`, `style`, `template_filename`, `template_path` | вход генерации |
| `parent_job_id` FK→self (CASCADE), `variant`, `slide_position`, `revision_request` | для дочерних задач |
| `task_id` | Celery task id |

**`slide_revisions`** — история ревизий слайда. Уникальность `(deck_job_id, variant, slide_position, revision_number)`, `source_job_id` UNIQUE (идемпотентность). Хранит `semantic_ir` (SlideIR JSON), `preview_path`, `audit_json`, `is_active`.

### 5.2 Формат `result_json`

```json
{
  "template_manifest": { ...TemplateManifest... },
  "variants": {
    "A": {
      "variant": "A",
      "pptx_path": "/app/storage/{job}/built_A.pptx",
      "pdf_path": "...built_A.pdf" | null,
      "html_path": "...variant_A.html" | null,
      "preview_paths": ["...<uuid>-01.png", ...],
      "audit_report": { ...AuditReport... },
      "presentation_ir": { ...PresentationIR... },
      "outline": { ...Outline... },
      "revision": 1,
      "export_state": "READY" | "STALE",
      "error": null
    }, "B": {...}, "C": {...}
  }
}
```

`JobStore.get` каждый раз десериализует **весь** документ обратно в dataclasses/pydantic.

### 5.3 Файлы в `storage/{job_id}/`

`template.pptx`, `built_{A|B|C}.pptx` (перезаписывается при каждой правке), `built_{V}.pdf`, `<uuid>-NN.png` (уникальные на каждый рендер — превью старых ревизий сохраняются), `variant_{V}.html`.

---

## 6. API (все пути доступны и как `/...`, и как `/api/...`)

| Метод | Путь | Что делает | Коды |
|---|---|---|---|
| GET | `/health` | liveness | 200 |
| GET | `/health/ready` | `SELECT 1` + Redis ping (в queue mode) | 200 / 503 |
| POST | `/templates/analyze` | multipart `template` → Template DNA (цвета, шрифты, layout_types, match_score, brand_profile, slide/layout/master count) | 200 / 413 / 422 |
| POST | `/generate` | multipart `template, brief(10..5000), slide_count(10..15), purpose, language, style` → `{job_id}` | 202 / 413 / 422 / 503 |
| GET | `/jobs` | список всех задач (включая дочерние) | 200 |
| GET | `/jobs/{id}` | статус, stage, progress, `variants[]` (VariantResultDTO), `output` | 200 / 404 |
| GET | `/jobs/{id}/events` | SSE: `{job_id,status,stage,progress,error}` при изменении, опрос БД каждые 0.5 с | 200 / 404 |
| DELETE | `/jobs/{id}` | удалить колоду + дочерние + ревизии + папку storage | 204 / 404 / 409 (RUNNING) |
| GET | `/jobs/{id}/result` | богатый payload для экрана результата: `template_dna`, `variants[]` с `code, name, audience, description, revision, export_state, status, audit_score, metrics{text_density, conclusions, data, visuals}, slides[]{position, slide_index, title, layout_type, component_types, preview_url, audit_status, issues[] (+bbox_normalized)}, exports{}` | 200 / 404 |
| GET | `/jobs/{id}/files/{V}/{pptx\|pdf\|html}` | скачать файл варианта | 200 / 404 / 409 (STALE) |
| GET | `/jobs/{id}/previews/{V}/{pos}` | PNG превью слайда (pos 1-based) | 200 / 404 |
| GET | `/jobs/{id}/audit/{V}` | сырой `AuditReport` | 200 / 404 |
| GET | `/jobs/{id}/download` | ZIP всех не-STALE вариантов (pptx/pdf/html/audit json) | 200 / 404 |
| POST | `/jobs/{id}/slides/{V}/{pos}/revise` | JSON `SlideRevisionRequest` → `{job_id}` дочерней задачи | 202 / 404 / 409 / 503 |
| GET | `/jobs/{id}/slides/{V}/{pos}/revisions` | `{items: [{revision_id, revision, correction_prompt, preview_url, is_active, created_at}]}` | 200 / 404 |
| GET | `/jobs/{id}/slides/{V}/{pos}/revisions/{rid}/preview` | PNG превью конкретной ревизии | 200 / 404 |
| POST | `/jobs/{id}/slides/{V}/{pos}/revisions/{rid}/activate` | `{base_revision}` → `{job_id}` | 202 / 404 / 409 |

Детали: `SlideRevisionRequest.as_instructions()` ([schemas.py:59](app/api/schemas.py:59)) превращает чекбоксы в русские инструкции для LLM. `_VARIANT_META` ([routes.py:55](app/api/routes.py:55)): A=Executive/«Для руководства», B=Analytical/«Для проектной защиты», C=Pitch/«Для выступления». `audit_score = 100 − 15·critical − 5·warning` считается только в `/result`.

Авторизации, rate limiting и отмены задач нет.

---

## 7. ML-ядро (`app/core`)

### 7.1 Parser — [template_parser.py](app/core/parser/template_parser.py)
- Обходит **все** slide masters (не только первый), для каждого layout собирает слоты с геометрией (EMU + нормализованная), разрешая наследование от мастера.
- `_inferred_slots` — превращает обычные (не-placeholder) текстовые блоки/таблицы/графики в слоты (`LayoutSlot.inferred=True`): (1) из самих layout'ов, (2) из готовых слайдов-примеров шаблона — их геометрия вливается в «родительский» layout, контент не копируется (idx = `номер_слайда·1000+…`). Классификация layout делается **до** шага (2), поэтому слоты из примеров на `layout_type` не влияют.
- `_extract_brand_profile` — кегли заголовка/тела и цвета, реально использованные в шаблоне (`BrandProfile`).
- Тема (цвета/шрифты) — из `ppt/theme/theme1.xml` с fallback.
- `layout_classifier.classify_layout` — сначала по имени (RU/EN алиасы), потом по составу слотов.
- Кэш: `.cache/{sha256}.v3.manifest.json` (относительно CWD; при недоступности пишет warning и работает без кэша).
- `TemplateManifest.find_layout_or_fallback(type, slide_index)` — `slide_index==0` считается обложкой; остальные слайды никогда не попадают на `TITLE_SLIDE`-layout, при отсутствии нужного типа — `CONTENT_1COL` → `CONTENT_2COL` → layout с наибольшей контентной площадью.

### 7.2 Агенты — `app/core/agents`

| Модуль | Роль |
|---|---|
| [llm_client.py](app/core/agents/llm_client.py) | `complete_structured`: JSON Schema в system prompt **и** `extra_body.guided_json` (vLLM). Работает и на не-vLLM бэкендах (OpenRouter). Срезает ```json-ограды. |
| [prompt_registry.py](app/core/agents/prompt_registry.py) | грузит `skills/{id}/v{max}.yaml` |
| [narrative_architect.py](app/core/agents/narrative_architect.py) | бриф → `Outline`. До **3 попыток** с фидбэком. Отклоняет: числа, которых нет в брифе; почти дублирующиеся key_message; меньше 3 разных композиций; нет `COMPARISON`; нет `PROCESS_TIMELINE`; 3×`CONTENT_1COL` подряд. Если идеала нет — берёт лучший «заземлённый» вариант. Типы layout для LLM = типы шаблона ∪ все семантические. |
| [slot_filler.py](app/core/agents/slot_filler.py) | `OutlineItem` → `SlideIR`. Разрешённые компоненты зависят от **запрошенного** (семантического) layout, а не от того, что нашлось в шаблоне. Проверки: заземлённость чисел, качество композиции (KPI ≥3 карточек, CHART → chart, 2COL → 2 блока или визуал и т.д.). `build_fallback_slide` — детерминированный слайд из key_message/content_hint без LLM. |
| [grounding.py](app/core/agents/grounding.py) | `ungrounded_numbers` (числа ≥10 и все проценты обязаны быть в брифе), `near_duplicate_pairs` (Jaccard по 7-символьным префиксам слов, порог 0.78) |
| [visual_policy.py](app/core/agents/visual_policy.py) | пост-обработка IR без LLM: 2 bullet-блока на COMPARISON → `ComparisonData` («До»/«После»), bullets на PROCESS_TIMELINE → `ProcessData`, 3-й подряд чисто-текстовый слайд → `IconListData` |
| [slide_reviser.py](app/core/agents/slide_reviser.py) | правка одного `SlideIR` по инструкциям; те же проверки чисел; если просили визуал, а вернулся только текст — ретрай |

Варианты ([narrative_architect.py:14](app/core/agents/narrative_architect.py:14)): **A** Executive, **B** Analytical, **C** Pitch. *(В исходном ADR C был «Structural/Process» — ось изменена.)*

### 7.3 IR — [presentation_ir.py](app/models/presentation_ir.py)

`PresentationIR{variant, template_source_hash, slides[10..15]}` → `SlideIR{slide_index, layout_type, title{text≤120, is_action_title}, components[], speaker_notes}`.

Компоненты (discriminated union по `type`): `bullet_block` (≤6 пунктов, ≤15 слов), `metric_card`, `chart` (bar/column/line/pie, серии = длине категорий), `table` (≤7×5), `image` (плейсхолдер), `comparison` (2 колонки по ≤5), `process` (3–6 шагов), `icon_list` (2–6, 8 иконок). Заглушки (`todo`, `lorem ipsum`, `xxx`, …) запрещены в заголовке.

### 7.4 Builder — [pptx_builder.py](app/core/builder/pptx_builder.py), [shape_factory.py](app/core/builder/shape_factory.py)
- Удаляет слайды шаблона (через XML), для каждого SlideIR берёт layout через `find_layout_or_fallback`, рендерит заголовок и компоненты **нативными** объектами (таблицы, графики `CategoryChartData`, автофигуры).
- Группировка: подряд идущие `MetricCard` → одна горизонтальная сетка; пара `BulletBlock` на 2COL/COMPARISON → две колонки в одном контейнере.
- Компоненты привязываются к слоту по типу плейсхолдера; без слота — fallback-геометрия из контентной области layout (не на всю ширину слайда).
- Неиспользованные плейсхолдеры удаляются (нет «призрачного» текста шаблона).
- Цвет текста подбирается под фон (`_contrasting_text_hex`), кегли — от `BrandProfile`, автоподбор размера — PIL-симуляция переноса ([autofit.py](app/core/builder/autofit.py)).
- Выход: `storage/{job}/built_{V}.pptx` + `bbox_map[slide_index][shape_id]` для аудита.

### 7.5 Auditor — [audit_runner.py](app/core/auditor/audit_runner.py)

| Проверка | Файл | Severity |
|---|---|---|
| AABB-коллизии, вылет за слайд | geometry_audit.py | CRITICAL |
| WCAG контраст текста к реальному фону фигуры/слайда | contrast_audit.py | CRITICAL |
| Плотность (буллеты/слова/таблица) по IR | density_audit.py | CRITICAL |
| Заглушки по реальному тексту .pptx | density_audit.py | CRITICAL |
| Пустой контентный слайд / 3 текстовых подряд / <3 типов компонентов | visual_audit.py | CRITICAL / WARNING |
| LLM-чек-лист (action title, выдуманные числа, смешение языков) | semantic_audit.py | WARNING; при сбое LLM → 1 × INFO `AUDIT_DEGRADED` |

`AuditReport.passed` = нет неисправленных CRITICAL. Авто-исправления по `auto_fixable` **нет** (поле есть, цикла нет).

### 7.6 Exporter
- [pdf_exporter.py](app/core/exporter/pdf_exporter.py): `soffice --headless` с уникальным `UserInstallation` на вызов (параллельные конвертации не конфликтуют), таймаут 60 с.
- [preview_renderer.py](app/core/exporter/preview_renderer.py): pdf2image → PNG 96 dpi; любая ошибка → `[]`.
- [html_exporter.py](app/core/exporter/html_exporter.py): самодостаточный HTML со **встроенными PNG** (растровый вьювер; нативность обеспечивается только PPTX). Создаётся только если есть превью, т.е. работает LibreOffice.

---

## 8. Промпты (`skills/`)

Грузится максимальная версия: `narrative_architect/v3`, `slot_filler/v3`, `slide_reviser/v1`, `semantic_audit/v1`. Старые версии лежат рядом. Формат: `id, version, model_params, system_prompt, user_template, output_schema_ref`. Пиннинга версии через конфиг нет — новая `vN.yaml` сразу становится активной.

---

## 9. Frontend (`frontend/`)

Стек: React 19, react-router 7, Vite 6, TypeScript 5.7. Без UI-библиотек и state-менеджеров. Сборка `tsc --noEmit && vite build`, отдаётся nginx ([nginx.conf](frontend/nginx.conf): SPA fallback, `/healthz`, прокси `/api/` → `app:1494` с отключённой буферизацией под SSE).

| Файл | Роль |
|---|---|
| [App.tsx](frontend/src/App.tsx) | маршруты `/` (старт) и `/result/:jobId` |
| [state.tsx](frontend/src/state.tsx) | сессия (jobId, бриф, назначение, DNA) в `sessionStorage` |
| [api.ts](frontend/src/api.ts) | все вызовы API, SSE-ридер, `waitForJob` (SSE + параллельный поллинг `/jobs/{id}` каждые 1.5 с), `applySlideChange` (правка с одним авто-ретраем при 409) |
| [normalize.ts](frontend/src/normalize.ts) | «толерантный» разбор ответов бэка (пробует несколько имён ключей) |
| [ui.ts](frontend/src/ui.ts) | назначения (feature/product/project/initiative), подписи вариантов, лейблы стадий |
| [pages/StartPage.tsx](frontend/src/pages/StartPage.tsx) | загрузка .pptx → `/templates/analyze` → Template DNA; бриф, назначение, 10–15 слайдов; язык/стиль захардкожены RU/Balanced |
| [pages/ResultPage.tsx](frontend/src/pages/ResultPage.tsx) | переключатель вариантов, лента слайдов, превью, панель DNA, панель аудита, экспорт PPTX/PDF/HTML, «Перегенерировать слайд», «Поправить слайд» |
| [components/FixDialog.tsx](frontend/src/components/FixDialog.tsx) | чекбоксы правок + комментарий |
| [components/SlidePreview.tsx](frontend/src/components/SlidePreview.tsx) | грузит PNG превью через fetch → blob URL |
| [components/JobProgress.tsx](frontend/src/components/JobProgress.tsx) | модалка прогресса |

Используемые эндпоинты: `/templates/analyze`, `/generate`, `/jobs/{id}`, `/jobs/{id}/events`, `/jobs/{id}/result`, `/jobs/{id}/audit/{V}`, `/jobs/{id}/previews/{V}/{pos}`, `/jobs/{id}/files/{V}/{kind}`, `/jobs/{id}/slides/{V}/{pos}/revise`.
**Не используются** (реализованы в api.ts или на бэке, но UI их не вызывает): история ревизий (`listRevisionIds`, `activateRevision`), `/download`, `/jobs`, `DELETE /jobs/{id}`, `checkHealth`.

---

## 10. Конфигурация (env → `Settings`)

| Переменная | Дефолт в коде | Compose/прод |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8001/v1` | `http://host.docker.internal:8001/v1` или OpenRouter |
| `LLM_MODEL` | `Qwen2.5-32B-Instruct` | из `.env` |
| `LLM_API_KEY` | `EMPTY` | из `.env` |
| `LLM_MAX_CONCURRENCY` | 4 | 4 (семафор **на одну задачу**; worker concurrency=2 → до 8 параллельных LLM-запросов) |
| `N_SLIDES_MIN/MAX` | 10/15 | переопределяется `slide_count` задачи |
| `SLOT_FILLER_MAX_RETRIES` | 2 | — |
| `PIPELINE_TIMEOUT_SECONDS` | 300 | Celery soft/hard limit = +60/+120 |
| `PDF_EXPORT_TIMEOUT_SECONDS` | 60 | — |
| `STORAGE_DIR` | `storage` | `/app/storage` |
| `SKILLS_DIR` | `skills` | — |
| `MAX_UPLOAD_BYTES` | 20 000 000 | — |
| `CORS_ALLOW_ORIGINS` | `["*"]` | `["*"]` |
| `DATABASE_URL` | sqlite in-memory | `postgresql+psycopg://…@postgres:5432/slideops` |
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://redis:6379/0` |
| `TASK_QUEUE_ENABLED` | `false` | `true` |
| `VITE_API_BASE` (build-arg фронта) | — | `/api` |
| `FRONTEND_PORT`, `BACKEND_IMAGE`, `FRONTEND_IMAGE`, `IMAGE_TAG`, `POSTGRES_*` | — | compose |

`.env` в `.gitignore` (не закоммичен). `.env.example` — шаблон под compose.

---

## 11. CI/CD

- [ci.yml](.github/workflows/ci.yml) (PR и не-main пуши): `uv sync --frozen` → `ruff check app tests alembic` → `pytest -m "not docker"` → alembic upgrade/downgrade/upgrade на SQLite → `docker compose config` → сборка обоих образов (сборка фронта = его единственная проверка типов).
- [release.yml](.github/workflows/release.yml) (push в main): CI → пуш образов в GHCR с тегом = SHA и `main` → если `vars.ENABLE_DEPLOY == 'true'`: scp compose + deploy.sh по SSH и `scripts/deploy.sh <backend> <frontend> <sha>`.
- [deploy.sh](scripts/deploy.sh): pull → `compose up --wait` → при ошибке откат app/worker/frontend на предыдущий образ. Миграции не откатываются (должны быть обратно совместимы).

---

## 12. Известные проблемы и риски (приоритет сверху вниз)

Каждый пункт проверен по коду на `8c92eec`.

### P0 — ломает основной сценарий демо

**12.1 Фронт не сопоставляет варианты из `/jobs/{id}` и `/jobs/{id}/result`.**
`/jobs/{id}` отдаёт `variant: "A"|"B"|"C"`, `/result` отдаёт `code: "A"`, `name: "Executive"`. [normalize.ts:165](frontend/src/normalize.ts:165) берёт id из `["variant","id","name","key"]` → получает `"Executive"`. В [ResultPage.tsx:47-49](frontend/src/pages/ResultPage.tsx:47) `decks.find(d => d.id === "A")` не находит колоду → лента слайдов показывает заглушки «Слайд 01…» вместо реальных заголовков/типов, пропадает rationale. Подписи вариантов тоже ломаются: [ui.ts:29](frontend/src/ui.ts:29) ключи `executive/analytical/pitch`, а приходит `"A"` → кнопки показывают «A / Вариант генерации».
*Фикс:* в `variantFrom` первым брать `code`; в `ui.ts` ключевать по `A/B/C` (или брать `name/audience` прямо из `/result`).

**12.2 Аудит на фронте не привязывается к слайдам и теряет данные.**
Бэк отдаёт `AuditIssue{issue_type, severity, slide_index, message, …}`. [normalize.ts:195-208](frontend/src/normalize.ts:195) ищет `slide/position/…` (нет → `null`), `title/code/…` (нет → берёт message), `type/source/…` (нет → `"audit"`), `INFO` мапит в `warning`. Итог: точки статуса у всех слайдов «ok», клик по замечанию ничего не открывает, `AUDIT_DEGRADED` считается предупреждением, `score` всегда `null` → «Audit —», «до/после» в FixDialog никогда не показывается.
При этом `/result` **уже** отдаёт готовые per-slide `issues`, `audit_status`, `audit_score`, `metrics` — фронт их выбрасывает в `normalizeResult`.
*Фикс:* брать аудит и метрики из `/result` (он индексирует по `position`), либо мапить `slide_index` → `position` через `slides[].slide_index`; `INFO` не показывать как warning.

**12.3 Локальный dev фронта ходит в прод.** [api.ts:16](frontend/src/api.ts:16): при пустом `VITE_API_BASE` fallback `https://lct.shmyaks.ru/api/`. В [vite.config.ts](frontend/vite.config.ts) нет dev-proxy, а Vite читает `.env` из `frontend/`, не из корня. `npm run dev` без настроек генерирует на проде.
*Фикс:* fallback `/api` + `server.proxy['/api'] → http://127.0.0.1:1494` (или `:8000` для `main.py`).

### P1 — гонки и целостность данных

**12.4 Параллельные правки разных слайдов одного варианта теряют друг друга.** Redis-лок по ключу `slide-revision:{parent}:{V}:{pos}` ([service.py:137](app/pipeline/service.py:137)) — **на слайд**, а не на вариант. Две правки слайдов 3 и 5 варианта A: обе читают ревизию N, каждая пересобирает вариант со своим изменением, обе пишут один и тот же `built_A.pptx` ([pptx_builder.py:245](app/core/builder/pptx_builder.py:245)) и `parent.result` целиком — побеждает последняя, первая правка молча пропадает (при этом в `slide_revisions` она числится активной). В fallback-режиме лока нет вообще.
*Фикс:* лок на `{parent}:{V}`; проверка `base_revision` на стороне воркера уже есть — сделать её обязательной; писать pptx в уникальный путь на ревизию.

**12.5 Вечный `STALE`.** Если воркер умер жёстко (OOM/SIGKILL) и задача не передоставлена, `export_state` варианта остаётся `STALE` навсегда → `/files` отдают 409, `/download` вариант пропускает. Восстановления/таймаута нет.

**12.6 Удаление колоды во время правки.** `DELETE /jobs/{id}` проверяет только статус родителя ([routes.py:310](app/api/routes.py:310)); каскадно удаляет RUNNING-дочку; воркер потом падает на `store.update` (KeyError) внутри `except`.

**12.7 Нет таймаута на правку.** `generate_deck` обёрнут в `asyncio.wait_for`, а `rebuild_variant_with_slide`/`revise_variant_slide` ([orchestrator.py:240](app/pipeline/orchestrator.py:240), [:280](app/pipeline/orchestrator.py:280)) — нет. В queue mode спасает только hard limit Celery, в fallback-режиме — ничего.

### P2 — корректность аудита и контента

**12.8 Разные «номера слайда» в аудите.** Геометрия/плотность/visual используют `SlideIR.slide_index` (приходит от LLM в outline), контраст — позицию слайда в .pptx ([audit_runner.py:72](app/core/auditor/audit_runner.py:72)), семантический аудит — то, что вернул LLM. Если LLM нумерует outline с 1, контрастные замечания привязываются к соседнему слайду. Также `find_layout_or_fallback` считает обложкой только `slide_index == 0`.
*Фикс:* нормализовать `slide_index = позиция−1` после `build_outline`.

**12.9 `visual_policy` навязывает рамку «До/После».** [visual_policy.py:23-25](app/core/agents/visual_policy.py:23): любые два bullet-блока на COMPARISON становятся «До»/«После», даже если это «Риски/Возможности». Плюс это русский текст при `language != ru`.

**12.10 Параметры `language`, `style`, `purpose` — только текст в конце брифа** ([service.py:84](app/pipeline/service.py:84)). Отдельно в промпты/агентов не передаются; фронт всегда шлёт `ru`/`balanced`.

### P3 — производительность и эксплуатация

**12.11 Дорогой поллинг.** SSE-цикл каждые 0.5 с вызывает `job_store.get`, который десериализует **весь** `result_json` (manifest + 3 IR + outline + аудит) ([routes.py:272-294](app/api/routes.py:272)). Фронт параллельно с SSE ещё и поллит `/jobs/{id}` каждые 1.5 с ([api.ts:268-279](frontend/src/api.ts:268)). На каждого открытого клиента.
*Фикс:* SSE читать только колонки status/stage/progress/error; на фронте поллинг только как fallback при падении SSE.

**12.12 Каждый `JobStore.update` перезаписывает весь `result_json`**, включая обновление прогресса (get + полный put).

**12.13 `/templates/analyze` парсит .pptx в процессе API** (через `asyncio.to_thread`), а не в воркере; файл читается целиком до проверки размера (так же в `/generate`).

**12.14 Регистр варианта.** `/previews`, `/revise`, `/revisions` делают `variant.upper()`, а `/files`, `/audit` — нет ([routes.py:359](app/api/routes.py:359), [:735](app/api/routes.py:735)).

**12.15 `preview_url` и `exports` в `/result` без префикса `/api`** ([routes.py:431](app/api/routes.py:431), [:500](app/api/routes.py:500)) — за внешним Nginx они уйдут во фронт-SPA. Сейчас фронт их не использует, но любой новый код, взявший их как есть, сломается.

**12.16 Порты:** `main.py` поднимает dev-сервер на `:8000`, Docker/compose/nginx — `:1494`.

### Тесты

- `tests/unit/test_config.py::test_default_when_no_env_var_set` падает, если в корне есть `.env` с другим `LLM_MODEL`: pydantic-settings читает `.env`, а тест удаляет только переменную окружения. *Фикс:* `Settings(_env_file=None)` в этом тесте.
- У фронта нет ни тестов, ни линтера; контрактные проблемы 12.1–12.2 никакой тест не ловит. Интеграционные тесты API не проверяют форму `/result` против того, что ожидает `normalize.ts`.

---

## 13. План бэкендера vs реальность

| В [HACKATHON_BACKEND_PLAN.md](HACKATHON_BACKEND_PLAN.md) | Сейчас в коде |
|---|---|
| `ArtifactStorage` с ключом, MIME, SHA-256, политикой удаления; S3/MinIO | сырые пути в `result_json`, один docker volume, без очистки |
| Задачи `generate_deck`, `revise_slide`, `export_variant`, `reanalyze_template` | только первые две |
| Стадии прогресса `template_parsing … variant_a_composing …` | `analyzing_template, extracting_design_system, planning_structure, matching_layouts, building_variants, auditing, exporting` (общие на все варианты) |
| `deck_id` отдельно от `job_id`, доменные сущности Deck/DeckPlan/DeckVariant | всё в `generation_jobs.result_json` |
| Nginx: upload limits, rate limits | не настроено в репо (внешний Nginx вне репозитория) |
| Content pack (импорт контента по ТЗ) | не реализован; решение «добавить или задокументировать сокращение scope» не принято (§2.4 плана) |
| Repair-цикл аудита | нет |
| VLM-аудит по PNG | нет (README это признаёт) |

## 14. Отличия от исходного ADR ([docs/archive](docs/archive/ADR_v1_sprints.md))

- Вариант C: «Structural/Process» → **Pitch**; процессные/сравнительные слайды теперь обязательны во всех вариантах через проверки outline.
- Слайд больше не выбрасывается при сбое LLM — вместо него детерминированный fallback-слайд.
- Добавлены компоненты `comparison`, `process`, `icon_list`; `BrandProfile`; inferred-слоты; обход всех мастеров.
- In-memory `JobStore` → SQLAlchemy + PostgreSQL + Alembic; `BackgroundTasks` → Celery (с fallback).
- Появились: ревизии слайдов, SSE, HTML-экспорт, `/templates/analyze`, `/result`, фронт, CI/CD.
- PDF-экспорт стал best-effort (сбой LibreOffice не роняет вариант).
