# SlideOps backend

Backend генератора презентаций по пользовательскому brief и загруженному
PPTX-шаблону. Сервис создаёт три варианта презентации, отдаёт PNG-превью и
audit issues, позволяет перегенерировать отдельный слайд и экспортирует
PPTX/PDF/HTML.

## Документация

- [ARCHITECTURE.md](ARCHITECTURE.md) — пайплайн и границы слоёв;
- [MODELS.md](MODELS.md) — используемая LLM, лицензия, назначение и требования
  к inference;
- [AUDIT.md](AUDIT.md) — архитектура аудита, матрица проверок и покрытие
  тестами;
- [HACKATHON_BACKEND_PLAN.md](HACKATHON_BACKEND_PLAN.md) — целевой план и
  исторические решения; отдельные пункты могут опережать текущую реализацию.

## Демонстрационные результаты

Папка [results](results/) содержит зафиксированный комплект для проверки
требования ТЗ «3 шаблона × 3 варианта»: исходные PPTX-шаблоны и девять
сгенерированных на их основе презентаций. Это демонстрационные артефакты, а не
runtime storage приложения; рабочие jobs продолжают сохраняться в
`STORAGE_DIR`.

| Набор | Исходный шаблон | Характеристики шаблона | Три результата |
|---|---|---|---|
| 1 | `VK Tech шаблон (2).pptx` | 54 слайда, 39 layouts, 2 masters | `Шаблон_1_1_shmyaks.pptx`, `Шаблон_1_2_shmyaks.pptx`, `Шаблон_1_3_shmyaks.pptx` |
| 2 | `VK_WorkSpace_Клиентская_конференция_Шаблон_03 (2).pptx` | 29 слайдов, 15 layouts, 1 master | `Шаблон_2_1_shmyaks.pptx`, `Шаблон_2_2_shmyaks.pptx`, `Шаблон_2_3_shmyaks.pptx` |
| 3 | `Шаблон презентации VK Education (2).pptx` | 55 слайдов, 30 layouts, 2 masters | `Шаблон_3_1_shmyaks.pptx`, `Шаблон_3_2_shmyaks.pptx`, `Шаблон_3_3_shmyaks.pptx` |

В имени `Шаблон_<N>_<V>_shmyaks.pptx` число `N` обозначает исходный шаблон,
а `V` — один из трёх вариантов верстки. Каждая итоговая презентация основной
матрицы содержит 11 слайдов. При проверке следует открыть исходник и три
связанных результата, сравнить сохранение фирменных цветов, шрифтов,
композиционных паттернов и убедиться, что текст, таблицы, диаграммы и shapes в
PPTX остаются редактируемыми объектами.

Дополнительно в папке лежит отдельный демонстрационный прогон:
`kiberbezopasnost.pptx` и варианты `shmyaks-A.pptx`, `shmyaks-B.pptx`,
`shmyaks-C (2).pptx`. Он не входит в основную матрицу 3×3.

Размер каталога — около 180 MB. При обновлении результатов нужно сохранять
исходные шаблоны, заменять все три связанных варианта одновременно и перед
коммитом проверять, что каждый PPTX открывается без восстановления файла.

## Локальный запуск

```bash
cp .env.example .env
docker compose up --build
```

Frontend без внешнего Nginx: `http://127.0.0.1:5173`.
Swagger: `http://127.0.0.1:1494/docs`; API доступен одновременно по
`http://127.0.0.1:1494/...` и `http://127.0.0.1:1494/api/...`.

Compose поднимает frontend, API, Celery worker, PostgreSQL и Redis. Миграции
Alembic выполняются отдельным одноразовым сервисом до старта API и worker.
Сгенерированные PPTX/PDF/HTML/PNG лежат в общем persistent volume.
API привязан только к `127.0.0.1:1494`; внешний Nginx должен проксировать на
этот адрес.
Frontend привязан к `127.0.0.1:${FRONTEND_PORT:-5173}`. Во внешнем Nginx
`location /` направляется на frontend, а `location /api/` — на backend
`127.0.0.1:1494`. Backend принимает `/api` независимо от того, сохраняет или
срезает Nginx этот префикс.

CI запускает lint, тесты, цикл Alembic upgrade/downgrade и Docker build. В
`main` образ публикуется в GHCR с immutable tag равным commit SHA. Для deploy
нужно создать GitHub Environment `production`, добавить secrets
`DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER`, `DEPLOY_PATH`, `DEPLOY_SSH_KEY`,
`DEPLOY_KNOWN_HOSTS` и repository variable `ENABLE_DEPLOY=true`.

На сервере должны быть установлены Docker Engine и Compose v2. В `DEPLOY_PATH`
должен лежать серверный `.env`, а deploy-user должен иметь доступ к Docker и
запись в эту директорию. Workflow сам передаёт Compose-файл и deploy-скрипт по
SSH. Если GHCR package приватный, один раз авторизуйте Docker на сервере
токеном с `read:packages`:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u GITHUB_USER --password-stdin
```

Значение `DEPLOY_KNOWN_HOSTS` получите командой
`ssh-keyscan -H DEPLOY_HOST`, а fingerprint сверьте через доверенный канал с
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` на сервере. Workflow
последовательно запускает миграции и сервисы, ждёт их healthcheck и при ошибке
возвращает предыдущий образ приложения. Миграции БД автоматически не
откатываются, поэтому production-миграции должны быть обратно совместимыми.

Обязательные переменные:

- `LLM_BASE_URL` — OpenAI-compatible endpoint;
- `LLM_MODEL` — имя модели;
- `LLM_API_KEY` — ключ или `EMPTY` для локального vLLM;
- `LLM_MAX_CONCURRENCY` — предел одновременных запросов.

Референсная модель — `Qwen/Qwen2.5-32B-Instruct`; в конфигурации endpoint
по умолчанию используется alias `Qwen2.5-32B-Instruct`. Модель имеет открытые
веса и лицензию Apache-2.0. Она не входит в Docker-образ приложения: backend
обращается к внешнему OpenAI-compatible endpoint. Допустимые модели,
проверка лицензионных ограничений и требования к самостоятельному inference
описаны в [MODELS.md](MODELS.md).

## Контракт фронтенда

### 1. Анализ Template DNA

```http
POST /api/templates/analyze
Content-Type: multipart/form-data

template=<pptx>
```

Ответ содержит цвета, шрифты, число слайдов/layouts/masters, распознанные
layout types и предварительный `match_score`.

### 2. Запуск генерации

```http
POST /api/generate
Content-Type: multipart/form-data

template=<pptx>
brief=<text>
documents=<pdf или md>   # необязательно, повторяется, до 10 файлов
slide_count=12
purpose=project
language=ru
style=balanced
```

Ответ: `202 {"job_id":"..."}`.

Прогресс:

- polling: `GET /api/jobs/{job_id}`;
- SSE: `GET /api/jobs/{job_id}/events`.

Стадии: `analyzing_template`, `extracting_design_system`,
`planning_structure`, `matching_layouts`, `building_variants`, `auditing`,
`exporting`, `completed`.

### 3. Экран результата

```http
GET /api/jobs/{job_id}/result
```

Ответ содержит Template DNA, варианты Executive/Analytical/Pitch, показатели
варианта, audit score, список слайдов, preview URL, audit issues и export URL.
Координаты audit overlay доступны в `issue.bbox_normalized`.

### 4. Исправление слайда

Номер слайда в URL — позиция от `1`.

```http
POST /api/jobs/{job_id}/slides/{variant}/{slide_position}/revise
Content-Type: application/json

{
  "shorten_text": true,
  "make_action_title": false,
  "change_layout": false,
  "add_visual": false,
  "regenerate": false,
  "comment": "Заголовок должен сильнее подчёркивать вывод"
}
```

Backend меняет только выбранный semantic slide, пересобирает соответствующий
вариант и повторяет audit. Исправление асинхронное: ответ имеет вид
`202 {"job_id":"..."}`. Этот дочерний job отслеживается теми же polling/SSE
endpoint'ами, а его `output.parent_job_id` указывает на исходную презентацию.

Рекомендуется всегда передавать текущую версию варианта:

```json
{
  "base_revision": 1,
  "shorten_text": true,
  "comment": "Заголовок должен сильнее подчёркивать вывод"
}
```

При несовпадении `base_revision` backend возвращает `409`. Пока revision job
работает, у варианта `export_state=STALE`; после пересборки — `READY`.

История и возврат к старой версии:

- `GET /api/jobs/{job_id}/slides/{variant}/{position}/revisions`;
- `GET /api/jobs/{job_id}/slides/{variant}/{position}/revisions/{revision_id}/preview`;
- `POST /api/jobs/{job_id}/slides/{variant}/{position}/revisions/{revision_id}/activate`
  с телом `{"base_revision": 2}` — также возвращает дочерний job.

### 5. Экспорт

- `GET /api/jobs/{job_id}/files/{variant}/pptx`;
- `GET /api/jobs/{job_id}/files/{variant}/pdf`;
- `GET /api/jobs/{job_id}/files/{variant}/html`;
- `GET /api/jobs/{job_id}/download` — общий ZIP.

## Эксплуатация

- PostgreSQL — источник истины для jobs, результата pipeline и истории
  revisions; Redis используется как durable broker и для distributed lock.
- `GET /api/health` — liveness, `GET /api/health/ready` проверяет PostgreSQL и Redis.
- Для локального unit/integration запуска `TASK_QUEUE_ENABLED=false` оставляет
  совместимый in-process fallback. В Compose он всегда `true`.

## Текущие ограничения

- Artifact storage сейчас persistent Docker volume, не S3/MinIO. Для запуска
  нескольких хостов его нужно заменить общим object storage.
- HTML viewer использует embedded PNG previews. PPTX при этом состоит из
  нативных редактируемых объектов.
- Semantic audit работает по IR; VLM-аудит фактического PNG пока не подключён.
- Полная матрица реализованных, частичных и отсутствующих проверок приведена в
  [AUDIT.md](AUDIT.md).
