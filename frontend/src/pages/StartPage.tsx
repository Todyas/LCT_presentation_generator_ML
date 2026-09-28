import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { analyzeTemplate, startGeneration, waitForJob } from "../api";
import { JobProgress } from "../components/JobProgress";
import type { TemplateDna } from "../normalize";
import { useSession } from "../state";
import { EXAMPLE_BRIEF, PURPOSES, formatBytes, type PurposeId } from "../ui";

const SLIDE_MIN = 10;
const SLIDE_MAX = 15;

export function StartPage() {
  const navigate = useNavigate();
  const { session, setSession } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [fileSize, setFileSize] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [dna, setDna] = useState<TemplateDna | null>(null);
  const [parsing, setParsing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [fileError, setFileError] = useState("");
  const [brief, setBrief] = useState(session?.brief ?? "");
  const [purpose, setPurpose] = useState<PurposeId>(session?.purpose ?? "product");
  const [slideCount, setSlideCount] = useState(
    session?.slideCount != null && session.slideCount >= SLIDE_MIN && session.slideCount <= SLIDE_MAX
      ? String(session.slideCount)
      : "12",
  );
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState("");
  const [stage, setStage] = useState("queued");
  const [progress, setProgress] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const purposeMeta = PURPOSES.find((item) => item.id === purpose) ?? PURPOSES[1];
  const parsedSlideCount = Number(slideCount);
  const slideCountError = slideCountErrorFor(slideCount);
  const canRun = Boolean(file) && brief.trim().length >= 10 && !slideCountError && !parsing && !running;

  useEffect(() => () => abortRef.current?.abort(), []);

  function rememberStage(nextStage?: string, nextProgress?: number) {
    if (nextStage) {
      setStage(nextStage);
      setLog((current) => (current.at(-1) === nextStage ? current : [...current, nextStage]));
    }
    if (typeof nextProgress === "number" && Number.isFinite(nextProgress)) setProgress(nextProgress);
  }

  async function acceptFile(next: File) {
    if (!next.name.toLowerCase().endsWith(".pptx")) {
      setFileError("Нужен файл в формате .pptx");
      return;
    }
    setFileError("");
    setAnalyzeError("");
    setFile(next);
    setTemplateName(next.name);
    setFileSize(formatBytes(next.size));
    setDna(null);
    setParsing(true);
    try {
      setDna(await analyzeTemplate(next));
    } catch (error) {
      setAnalyzeError(error instanceof Error ? error.message : "Не удалось разобрать шаблон");
    } finally {
      setParsing(false);
    }
  }

  function clearFile() {
    setFile(null);
    setDna(null);
    setAnalyzeError("");
    setFileError("");
    setTemplateName("");
    setFileSize("");
  }

  async function generate() {
    if (!file || !canRun) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setRunError("");
    setStage("queued");
    setProgress(0);
    setLog(["queued"]);
    try {
      const jobId = await startGeneration({
        file,
        brief: brief.trim(),
        slideCount: parsedSlideCount,
        purpose,
      });
      setSession({
        jobId,
        templateName,
        fileSize,
        brief: brief.trim(),
        purpose,
        slideCount: parsedSlideCount,
        dna,
      });
      const job = await waitForJob(
        jobId,
        (update) => {
          if (update.event) rememberStage(update.event.stage, update.event.progress);
          if (update.job) rememberStage(update.job.stage, update.job.progress);
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (job.status === "FAILED") {
        setRunError(job.error || "Генерация завершилась с ошибкой");
        return;
      }
      navigate(`/result/${jobId}`);
    } catch (error) {
      if (controller.signal.aborted) return;
      setRunError(error instanceof Error ? error.message : "Не удалось запустить генерацию");
    }
  }

  return (
    <div className="start">
      <header className="topbar">
        <div className="brand">
          <div>
            <strong>Шмякс</strong>
          </div>
        </div>
      </header>

      <main className="start__main">
        <div className="start__intro">
          <h1>Новая генерация</h1>
          <p>Шаблон, бриф и назначение. Сервис снимет Template DNA и соберёт варианты по живому пайплайну.</p>
        </div>

        <div className="start__grid">
          <section className="stack">
            <article className="card">
              <div className="card__head">
                <h2>Шаблон</h2>
                <span className="card__hint">PPTX</span>
              </div>
              {file || templateName ? (
                <div className="file-card">
                  <div className="file-card__icon" aria-hidden="true">
                    PPTX
                  </div>
                  <div className="file-card__body">
                    <strong>{templateName}</strong>
                    <p>
                      {dna?.slides != null ? `${dna.slides} slides` : "slides —"}
                      {" · "}
                      {dna?.layouts != null ? `${dna.layouts} layouts` : "layouts —"}
                      {" · "}
                      {dna?.masters != null ? `${dna.masters} masters` : "masters —"}
                      {fileSize ? ` · ${fileSize}` : ""}
                    </p>
                    {parsing ? (
                      <span className="status status--warn">Читаем структуру PPTX…</span>
                    ) : dna ? (
                      <span className="status status--ok">Status: parsed</span>
                    ) : (
                      <span className="status status--warn">Анализ не завершён</span>
                    )}
                    {analyzeError && <p className="form-error">{analyzeError}</p>}
                  </div>
                  <button type="button" className="btn btn--ghost" onClick={clearFile} disabled={parsing || running}>
                    Заменить
                  </button>
                </div>
              ) : (
                <div
                  className={`dropzone${dragOver ? " is-over" : ""}`}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragOver(false);
                    const dropped = event.dataTransfer.files?.[0];
                    if (dropped) void acceptFile(dropped);
                  }}
                >
                  <strong>Загрузите PPTX-шаблон</strong>
                  <p>Сервис извлечёт цвета, шрифты, сетку и типовые layout-паттерны</p>
                  <div className="dropzone__actions">
                    <label className="btn btn--secondary">
                      Выбрать файл
                      <input
                        type="file"
                        accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                        hidden
                        onChange={(event) => {
                          const picked = event.target.files?.[0];
                          if (picked) void acceptFile(picked);
                          event.target.value = "";
                        }}
                      />
                    </label>
                  </div>
                  {fileError && <p className="form-error">{fileError}</p>}
                </div>
              )}
            </article>

            <article className="card">
              <div className="card__head">
                <h2>Бриф</h2>
                <button type="button" className="text-btn" onClick={() => setBrief(EXAMPLE_BRIEF)}>
                  Вставить пример
                </button>
              </div>
              <textarea
                className="brief"
                value={brief}
                onChange={(event) => setBrief(event.target.value)}
                placeholder="Опишите, какую презентацию нужно собрать. Например: Сделать презентацию для защиты сервиса, который автоматически создаёт корпоративные презентации по шаблону. Аудитория — эксперты VK Tech. Нужно показать проблему, подход, архитектуру, бизнес-эффект."
                rows={6}
                maxLength={5000}
              />
            </article>

            <article className="card">
              <div className="card__head">
                <h2>Назначение презентации</h2>
              </div>
              <div className="segment" role="radiogroup" aria-label="Назначение презентации">
                {PURPOSES.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="radio"
                    aria-checked={purpose === item.id}
                    className={purpose === item.id ? "is-on" : ""}
                    onClick={() => setPurpose(item.id)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <p className="hint">{purposeMeta.hint}</p>
              <p className="hint hint--quiet">Назначение влияет на структуру, тональность и выбор слайдов.</p>
            </article>

            <article className="card">
              <div className="card__head">
                <h2>Настройки</h2>
              </div>
              <div className="settings">
                <div>
                  <label className="setting-label" htmlFor="slide-count">
                    Слайды
                  </label>
                  <input
                    id="slide-count"
                    className={`count-input${slideCountError ? " is-invalid" : ""}`}
                    type="number"
                    inputMode="numeric"
                    min={SLIDE_MIN}
                    max={SLIDE_MAX}
                    step={1}
                    value={slideCount}
                    aria-invalid={Boolean(slideCountError)}
                    aria-describedby="slide-count-hint"
                    onChange={(event) => setSlideCount(event.target.value)}
                  />
                  <p id="slide-count-hint" className={slideCountError ? "form-error" : "hint hint--quiet"}>
                    {slideCountError || `От ${SLIDE_MIN} до ${SLIDE_MAX}`}
                  </p>
                </div>
                <div>
                  <span className="setting-label">Язык</span>
                  <span className="chip">RU</span>
                </div>
                <div>
                  <span className="setting-label">Стиль</span>
                  <span className="chip">Balanced</span>
                </div>
              </div>
            </article>

            <div className="launch">
              <button type="button" className="btn btn--primary btn--large" disabled={!canRun} onClick={() => void generate()}>
                Сгенерировать презентацию
              </button>
              {!canRun && !running && (
                <p className="hint">Загрузите PPTX и опишите задачу хотя бы в 10 символов.</p>
              )}
            </div>
          </section>

          <aside className="stack">
            <article className="card dna">
              <div className="card__head">
                <h2>Template DNA</h2>
                {dna && <span className="status status--ok">parsed</span>}
              </div>
              {dna ? (
                <DnaCard dna={dna} templateName={templateName} />
              ) : (
                <p className="empty">
                  {parsing
                    ? "Читаем шаблон через /api/templates/analyze."
                    : "Загрузите PPTX, чтобы увидеть цвета, шрифты и layout-паттерны."}
                </p>
              )}
            </article>
          </aside>
        </div>
      </main>

      {running && (
        <JobProgress
          title={runError ? "Генерация не завершилась" : "Собираем презентацию"}
          subtitle={templateName}
          stage={stage}
          progress={progress}
          log={log}
          error={runError}
          onClose={
            runError
              ? () => {
                  abortRef.current?.abort();
                  setRunning(false);
                }
              : undefined
          }
        />
      )}
    </div>
  );
}

function slideCountErrorFor(value: string) {
  if (!value.trim()) return "Укажите число слайдов";
  if (!/^\d+$/.test(value.trim())) return "Нужно целое число";
  const count = Number(value);
  if (count < SLIDE_MIN || count > SLIDE_MAX) return `От ${SLIDE_MIN} до ${SLIDE_MAX} слайдов`;
  return "";
}

function DnaCard({ dna, templateName }: { dna: TemplateDna; templateName: string }) {
  return (
    <>
      <div className="dna__score">
        {dna.score != null ? <ScoreRing value={dna.score} /> : null}
        <div>
          <strong>{dna.score != null ? `Template match ${dna.score}` : "Template DNA"}</strong>
          <p>{dna.name || templateName}</p>
        </div>
      </div>
      {dna.colors.length > 0 && (
        <div className="swatches">
          {dna.colors.map((color) => (
            <span key={color} title={color}>
              <i style={{ background: color }} />
              {color}
            </span>
          ))}
        </div>
      )}
      {dna.fonts.length > 0 && (
        <div className="chip-row">
          {dna.fonts.map((font) => (
            <span key={font} className="chip">
              {font}
            </span>
          ))}
        </div>
      )}
      {dna.patterns.length > 0 && (
        <div className="chip-row">
          {dna.patterns.map((pattern) => (
            <span key={pattern} className="chip chip--soft">
              {pattern}
            </span>
          ))}
        </div>
      )}
      <dl className="stats">
        <div>
          <dt>Slides</dt>
          <dd>{dna.slides ?? "—"}</dd>
        </div>
        <div>
          <dt>Layouts</dt>
          <dd>{dna.layouts ?? "—"}</dd>
        </div>
        <div>
          <dt>Masters</dt>
          <dd>{dna.masters ?? "—"}</dd>
        </div>
      </dl>
    </>
  );
}

function ScoreRing({ value }: { value: number }) {
  const radius = 18;
  const circ = 2 * Math.PI * radius;
  const offset = circ - (value / 100) * circ;
  return (
    <svg className="ring" width="52" height="52" viewBox="0 0 52 52" aria-hidden="true">
      <circle cx="26" cy="26" r={radius} stroke="#E6EBF2" strokeWidth="4" fill="none" />
      <circle
        cx="26"
        cy="26"
        r={radius}
        stroke="#0077FF"
        strokeWidth="4"
        fill="none"
        strokeDasharray={circ}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform="rotate(-90 26 26)"
      />
      <text x="26" y="30" textAnchor="middle" fontSize="12" fontWeight="700" fill="#141820">
        {value}
      </text>
    </svg>
  );
}
