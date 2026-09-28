import { stageLabel } from "../ui";

export function JobProgress({
  title,
  subtitle,
  stage,
  progress,
  log,
  error,
  onClose,
}: {
  title: string;
  subtitle: string;
  stage: string;
  progress: number;
  log: string[];
  error: string;
  onClose?: () => void;
}) {
  return (
    <div className="overlay">
      <div className="overlay__card" role="dialog" aria-modal="true" aria-labelledby="job-progress-title">
        <p className="eyebrow">{error ? "Ошибка" : "Генерация"}</p>
        <h2 id="job-progress-title">{title}</h2>
        <p className="muted">{subtitle}</p>
        <div className="progress" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${progress}%` }} />
        </div>
        <p className="stage-now">
          {stageLabel(stage)} · {progress}%
        </p>
        {log.length > 0 && (
          <ol className="run-steps">
            {log.map((item, index) => (
              <li key={`${item}-${index}`} className={`run-step ${index === log.length - 1 && !error ? "is-active" : "is-done"}`}>
                <span>{index === log.length - 1 && !error ? String(index + 1) : "✓"}</span>
                <strong>{stageLabel(item)}</strong>
              </li>
            ))}
          </ol>
        )}
        {error && <p className="form-error">{error}</p>}
        {error && onClose && (
          <div className="modal__actions">
            <button type="button" className="btn btn--primary" onClick={onClose}>
              Закрыть
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
