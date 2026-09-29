import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { applySlideChange, downloadVariantFile, getAudit, getJob, getJobResult } from "../api";
import { FixDialog, type FixSelection } from "../components/FixDialog";
import { JobProgress } from "../components/JobProgress";
import { SlidePreview } from "../components/SlidePreview";
import type { AuditReport, DeckVariant, JobStatus, TemplateDna } from "../normalize";
import { slidesForVariant } from "../normalize";
import { useSession } from "../state";
import {
  PURPOSES,
  orderVariants,
  plural,
  projectTitle,
  variantAudience,
  variantLabel,
} from "../ui";

export function ResultPage() {
  const { jobId = "" } = useParams();
  const { session } = useSession();
  const linked = session?.jobId === jobId ? session : null;
  const [job, setJob] = useState<JobStatus | null>(null);
  const [decks, setDecks] = useState<DeckVariant[]>([]);
  const [dna, setDna] = useState<TemplateDna | null>(linked?.dna ?? null);
  const [variant, setVariant] = useState("");
  const [index, setIndex] = useState(0);
  const [audit, setAudit] = useState<AuditReport | null>(null);
  const [pageError, setPageError] = useState("");
  const [auditError, setAuditError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [fixOpen, setFixOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyError, setBusyError] = useState("");
  const [stage, setStage] = useState("queued");
  const [progress, setProgress] = useState(0);
  const [log, setLog] = useState<string[]>([]);

  const variantIds = useMemo(() => {
    const ids = job?.variants.map((item) => item.variant) ?? [];
    const fromDeck = decks.map((item) => item.id);
    return orderVariants(ids.length ? ids : fromDeck);
  }, [job, decks]);

  const current = job?.variants.find((item) => item.variant === variant);
  const deck = decks.find((item) => item.id === variant);
  const slides = slidesForVariant(deck, current?.preview_count ?? 0);
  const safeIndex = slides.length ? Math.min(index, slides.length - 1) : 0;
  const slide = slides[safeIndex];
  const purpose = PURPOSES.find((item) => item.id === linked?.purpose)?.label;
  const openIssues = audit?.issues.length ?? 0;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setPageError("");
    Promise.all([getJob(jobId), getJobResult(jobId).catch(() => ({ variants: [], dna: null }))])
      .then(([nextJob, result]) => {
        if (controller.signal.aborted) return;
        setJob(nextJob);
        setDecks(result.variants);
        if (result.dna) setDna(result.dna);
        const ids = orderVariants(
          nextJob.variants.map((item) => item.variant).length
            ? nextJob.variants.map((item) => item.variant)
            : result.variants.map((item) => item.id),
        );
        setVariant((currentId) => (currentId && ids.includes(currentId) ? currentId : ids[0] ?? ""));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setPageError(error instanceof Error ? error.message : "Не удалось загрузить задачу");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [jobId, reloadKey]);

  useEffect(() => {
    if (!variant) return;
    const controller = new AbortController();
    setAuditError("");
    getAudit(jobId, variant)
      .then((report) => {
        if (!controller.signal.aborted) setAudit(report);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setAudit(null);
        setAuditError(error instanceof Error ? error.message : "Аудит недоступен");
      });
    return () => controller.abort();
  }, [jobId, variant, reloadKey]);

  useEffect(() => {
    if (!slide) return;
    document.getElementById(`slide-nav-${slide.position}`)?.scrollIntoView({ block: "nearest" });
  }, [slide]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (fixOpen || busy || !slides.length) return;
      if (event.key === "ArrowRight") setIndex((currentIndex) => Math.min(slides.length - 1, currentIndex + 1));
      if (event.key === "ArrowLeft") setIndex((currentIndex) => Math.max(0, currentIndex - 1));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fixOpen, busy, slides.length]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2800);
    return () => window.clearTimeout(timer);
  }, [toast]);

  function selectVariant(id: string) {
    const keepPosition = slide?.position;
    const nextCurrent = job?.variants.find((item) => item.variant === id);
    const nextDeck = decks.find((item) => item.id === id);
    const targetSlides = slidesForVariant(nextDeck, nextCurrent?.preview_count ?? 0);
    setVariant(id);
    if (!targetSlides.length) {
      setIndex(0);
      return;
    }
    if (keepPosition != null) {
      const nextIndex = targetSlides.findIndex((item) => item.position === keepPosition);
      if (nextIndex >= 0) {
        setIndex(nextIndex);
        return;
      }
    }
    setIndex(Math.min(safeIndex, targetSlides.length - 1));
  }

  function remember(nextStage: string, nextProgress: number) {
    if (nextStage) {
      setStage(nextStage);
      setLog((currentLog) => (currentLog.at(-1) === nextStage ? currentLog : [...currentLog, nextStage]));
    }
    if (Number.isFinite(nextProgress)) setProgress(nextProgress);
  }

  async function changeSlide(selection: FixSelection) {
    if (!slide) throw new Error("Слайд не выбран");
    const before = audit?.score ?? null;
    const updatedJob = await applySlideChange({
      jobId,
      variant,
      position: slide.position,
      revision: current?.revision ?? 1,
      body: { ...selection, regenerate: false },
    });
    setJob(updatedJob);
    setReloadKey((value) => value + 1);
    const nextAudit = await getAudit(jobId, variant).catch(() => null);
    return { before, after: nextAudit?.score ?? null };
  }

  async function regenerate() {
    if (!slide) return;
    setBusy(true);
    setBusyError("");
    setStage("queued");
    setProgress(0);
    setLog(["queued"]);
    try {
      const updatedJob = await applySlideChange({
        jobId,
        variant,
        position: slide.position,
        revision: current?.revision ?? 1,
        body: {
          comment: "",
          shorten_text: false,
          make_action_title: false,
          change_layout: false,
          add_visual: false,
          regenerate: true,
        },
        onUpdate: (update) => {
          if (update.event) remember(update.event.stage ?? "", update.event.progress ?? 0);
          if (update.job) remember(update.job.stage, update.job.progress);
        },
      });
      setJob(updatedJob);
      setReloadKey((value) => value + 1);
      setToast(`Слайд ${String(slide.position).padStart(2, "0")} пересобран`);
    } catch (error) {
      setBusyError(error instanceof Error ? error.message : "Не удалось перегенерировать слайд");
    } finally {
      setBusy(false);
    }
  }

  async function exportFile(kind: "pptx" | "pdf" | "html") {
    try {
      await downloadVariantFile(jobId, variant, kind);
    } catch (error) {
      setToast(error instanceof Error ? error.message : `Не удалось скачать ${kind.toUpperCase()}`);
    }
  }

  const scoreText = audit?.score != null ? String(audit.score) : current?.audit_passed == null ? "—" : current.audit_passed ? "ok" : "fail";

  return (
    <div className="result">
      <header className="topbar">
        <div className="topbar__left">
          <Link to="/" className="back-link">
            ← Запуск
          </Link>
          <div className="brand brand--compact">
            <span className="logo" aria-hidden="true">
              S
            </span>
            <div>
              <strong>{projectTitle(linked?.brief ?? "")}</strong>
              <span>
                {linked?.templateName || dna?.name || jobId.slice(0, 8)}
                {purpose ? ` · ${purpose}` : ""}
              </span>
            </div>
          </div>
        </div>
        <div className="topbar__right">
          <span className="score-pill">Audit {scoreText}</span>
          <span className="demo-pill">{job?.status ?? "…"}</span>
          <button type="button" className="btn btn--primary" disabled={!variant || current?.pptx_available === false || busy} onClick={() => void exportFile("pptx")}>
            Export PPTX
          </button>
          <button type="button" className="btn btn--secondary" disabled={!variant || current?.pdf_available === false || busy} onClick={() => void exportFile("pdf")}>
            Export PDF
          </button>
          <button type="button" className="btn btn--secondary" disabled={!variant || current?.html_available === false || busy} onClick={() => void exportFile("html")}>
            Export HTML
          </button>
        </div>
      </header>

      {pageError ? (
        <p className="page-error">{pageError}</p>
      ) : (
        <div className="result__body">
          <aside className="rail">
            <p className="rail__label">
              {loading ? "Загрузка" : `${slides.length} ${plural(slides.length, "слайд", "слайда", "слайдов")}`}
            </p>
            <ul className="slide-list">
              {slides.map((item, itemIndex) => {
                const related = audit?.issues.filter((issue) => issue.slide === item.position) ?? [];
                const status = related.some((issue) => issue.severity === "critical")
                  ? "critical"
                  : related.length
                    ? "warning"
                    : "ok";
                return (
                  <li key={`${item.position}-${item.title}`}>
                    <button
                      id={`slide-nav-${item.position}`}
                      type="button"
                      className={itemIndex === safeIndex ? "is-active" : ""}
                      onClick={() => setIndex(itemIndex)}
                    >
                      <i className={`dot dot--${status}`} />
                      <span className="num">{String(item.position).padStart(2, "0")}</span>
                      <span className="meta">
                        <b>{item.title}</b>
                        <small>{item.type || variantLabel(variant)}</small>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </aside>

          <main className="stage-wrap">
            <div className="variant-bar">
              <div className="segment" role="radiogroup" aria-label="Вариант генерации">
                {variantIds.map((id) => (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={variant === id}
                    className={variant === id ? "is-on" : ""}
                    onClick={() => selectVariant(id)}
                  >
                    <b>{variantLabel(id)}</b>
                    <small>{variantAudience(id)}</small>
                  </button>
                ))}
              </div>
              {deck?.rationale && <p className="rationale">{deck.rationale}</p>}
              {current?.error && <p className="form-error">{current.error}</p>}
            </div>

            <div className="stage">
              {slide ? (
                <div className="slide-sizer">
                  <SlidePreview jobId={jobId} variant={variant} position={slide.position} reloadKey={reloadKey} />
                </div>
              ) : (
                <p className="preview-fallback">{loading ? "Загружаем результат…" : "В этом варианте нет слайдов"}</p>
              )}
            </div>

            <div className="stage-bar">
              <div className="stage-nav">
                <button
                  type="button"
                  className="btn btn--ghost stage-nav__arrow"
                  aria-label="Предыдущий слайд"
                  title="Предыдущий слайд"
                  disabled={safeIndex === 0 || !slide}
                  onClick={() => setIndex((value) => Math.max(0, value - 1))}
                >
                  ←
                </button>
                <span className="stage-nav__counter">
                  {slide ? `${String(slide.position).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}` : "—"}
                </span>
                <button
                  type="button"
                  className="btn btn--ghost stage-nav__arrow"
                  aria-label="Следующий слайд"
                  title="Следующий слайд"
                  disabled={!slide || safeIndex >= slides.length - 1}
                  onClick={() => setIndex((value) => Math.min(slides.length - 1, value + 1))}
                >
                  →
                </button>
              </div>
              <div className="stage-actions">
                <button type="button" className="btn btn--ghost" disabled={!slide || busy} onClick={() => void regenerate()}>
                  Перегенерировать слайд
                </button>
                <button type="button" className="btn btn--primary" disabled={!slide || busy} onClick={() => setFixOpen(true)}>
                  Поправить слайд
                </button>
              </div>
              <p className="disclaimer">Preview с сервера. PPTX скачивается отдельным файлом варианта.</p>
            </div>
          </main>

          <aside className="inspector">
            <section className="panel">
              <div className="panel__head">
                <h2>Template DNA</h2>
                {dna?.score != null && <span className="status status--ok">{dna.score}</span>}
              </div>
              {dna ? (
                <>
                  <p className="panel__lead">{dna.name || linked?.templateName}</p>
                  {dna.colors.length > 0 && (
                    <div className="swatches swatches--compact">
                      {dna.colors.map((color) => (
                        <span key={color} title={color}>
                          <i style={{ background: color, borderColor: color.toLowerCase() === "#ffffff" ? "#D5DDE6" : color }} />
                        </span>
                      ))}
                    </div>
                  )}
                  {dna.fonts.length > 0 && <p className="fine">{dna.fonts.join(" · ")}</p>}
                  {dna.patterns.length > 0 && <p className="fine">{dna.patterns.join(" · ")}</p>}
                </>
              ) : (
                <p className="fine">DNA появится после успешного анализа шаблона.</p>
              )}
            </section>

            <section className="panel">
              <div className="panel__head">
                <h2>Аудит</h2>
                <span className={openIssues ? "status status--warn" : "status status--ok"}>
                  {auditError ? "нет данных" : openIssues ? `${openIssues} ${plural(openIssues, "замечание", "замечания", "замечаний")}` : "чисто"}
                </span>
              </div>
              {auditError ? (
                <p className="fine">{auditError}</p>
              ) : (
                <ul className="issues">
                  {(audit?.issues ?? []).map((issue) => {
                    const currentSlide = issue.slide === slide?.position;
                    return (
                      <li key={`${issue.slide}-${issue.title}`} className={currentSlide ? "is-current" : ""}>
                        <button
                          type="button"
                          onClick={() => {
                            const next = slides.findIndex((item) => item.position === issue.slide);
                            if (next >= 0) setIndex(next);
                          }}
                        >
                          <span className={`badge badge--${issue.severity}`}>{issue.severity}</span>
                          <span className="badge badge--type">{issue.type}</span>
                          <strong>{issue.title}</strong>
                          <p>{issue.description}</p>
                          {issue.slide != null && <small>Слайд {String(issue.slide).padStart(2, "0")}</small>}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section className="panel export-card">
              <p className="eyebrow">Ready to export</p>
              <ul>
                <li>
                  {slides.length} {plural(slides.length, "слайд", "слайда", "слайдов")}
                </li>
                <li>
                  {variantIds.length} {plural(variantIds.length, "вариант", "варианта", "вариантов")}
                </li>
                <li>Audit score: {audit?.score ?? "—"}{audit?.score != null ? "/100" : ""}</li>
                <li>{current?.export_state === "STALE" ? "Экспорт устарел" : "Native PPTX objects"}</li>
              </ul>
              <p className="fine">PPTX, PDF и HTML скачиваются из файлов выбранного варианта.</p>
            </section>
          </aside>
        </div>
      )}

      {fixOpen && slide && (
        <FixDialog
          slideNumber={slide.position}
          slideTitle={slide.title}
          revision={current?.revision ?? 1}
          onClose={() => setFixOpen(false)}
          onApply={changeSlide}
        />
      )}
      {busy && (
        <JobProgress
          title={busyError ? "Правка не применилась" : "Обновляем слайд"}
          subtitle={slide ? slide.title : variantLabel(variant)}
          stage={stage}
          progress={progress}
          log={log}
          error={busyError}
          onClose={busyError ? () => setBusy(false) : undefined}
        />
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
