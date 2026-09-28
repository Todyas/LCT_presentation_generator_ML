import { useEffect, useState } from "react";

const OPTIONS = [
  { id: "shorten_text", label: "Сократить текст" },
  { id: "make_action_title", label: "Сделать заголовок выводом" },
  { id: "change_layout", label: "Подобрать другой layout" },
  { id: "add_visual", label: "Добавить график / схему" },
] as const;

export type FixSelection = {
  shorten_text: boolean;
  make_action_title: boolean;
  change_layout: boolean;
  add_visual: boolean;
  comment: string;
};

export function FixDialog({
  slideNumber,
  slideTitle,
  revision,
  onClose,
  onApply,
}: {
  slideNumber: number;
  slideTitle: string;
  revision: number;
  onClose: () => void;
  onApply: (selection: FixSelection) => Promise<{ before: number | null; after: number | null }>;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState<{ before: number | null; after: number | null } | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !pending) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, pending]);

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  async function apply() {
    if (!selected.length && !comment.trim()) {
      setError("Выберите правку или напишите комментарий");
      return;
    }
    setError("");
    setPending(true);
    try {
      const scores = await onApply({
        shorten_text: selected.includes("shorten_text"),
        make_action_title: selected.includes("make_action_title"),
        change_layout: selected.includes("change_layout"),
        add_visual: selected.includes("add_visual"),
        comment: comment.trim(),
      });
      setDone(scores);
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "Не удалось применить правки");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="modal" role="presentation" onMouseDown={() => { if (!pending) onClose(); }}>
      <div
        className="modal__card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fix-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        {done ? (
          <div className="fix-done">
            <p className="eyebrow">Slide fixed</p>
            <h2 id="fix-title">Слайд {String(slideNumber).padStart(2, "0")} обновлён</h2>
            {done.before != null && done.after != null ? (
              <p className="score-jump">
                Audit score: {done.before} → {done.after}
              </p>
            ) : (
              <p className="muted">Превью и аудит перечитаны с сервера.</p>
            )}
            <button type="button" className="btn btn--primary" onClick={onClose}>
              Готово
            </button>
          </div>
        ) : (
          <>
            <div className="modal__head">
              <div>
                <p className="eyebrow">Слайд {String(slideNumber).padStart(2, "0")}</p>
                <h2 id="fix-title">Что поправить?</h2>
                <p className="muted">
                  {slideTitle}
                  {revision > 1 ? ` · ревизия ${revision}` : ""}
                </p>
              </div>
              <button type="button" className="icon-btn" onClick={onClose} disabled={pending} aria-label="Закрыть">
                ×
              </button>
            </div>
            <div className="checks">
              {OPTIONS.map((option) => (
                <label key={option.id} className="check">
                  <input
                    type="checkbox"
                    checked={selected.includes(option.id)}
                    disabled={pending}
                    onChange={() => toggle(option.id)}
                  />
                  <span>{option.label}</span>
                </label>
              ))}
            </div>
            <label className="field">
              <span>Комментарий</span>
              <textarea
                value={comment}
                disabled={pending}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Что изменить на этом слайде"
                rows={3}
                maxLength={2000}
              />
            </label>
            {pending && <p className="hint">Применяем правки…</p>}
            {error && <p className="form-error">{error}</p>}
            <div className="modal__actions">
              <button type="button" className="btn btn--ghost" onClick={onClose} disabled={pending}>
                Отмена
              </button>
              <button type="button" className="btn btn--primary" onClick={() => void apply()} disabled={pending}>
                Применить правки
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
