import { useEffect, useState } from "react";
import { fetchPreview } from "../api";

export function SlidePreview({
  jobId,
  variant,
  position,
  reloadKey,
}: {
  jobId: string;
  variant: string;
  position: number;
  reloadKey: number;
}) {
  const [state, setState] = useState<"loading" | "image" | "html" | "error">("loading");
  const [url, setUrl] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setState("loading");
    setMessage("");
    fetchPreview(jobId, variant, position, controller.signal)
      .then((preview) => {
        if (controller.signal.aborted) {
          URL.revokeObjectURL(preview.url);
          return;
        }
        objectUrl = preview.url;
        setUrl(preview.url);
        setState(preview.kind);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState("error");
        setMessage(error instanceof Error ? error.message : "Не удалось загрузить preview");
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId, variant, position, reloadKey]);

  return (
    <article className="slide slide--live" aria-busy={state === "loading"}>
      {state === "loading" && <p className="preview-fallback">Загружаем слайд…</p>}
      {state === "error" && <p className="preview-fallback">{message}</p>}
      {state === "image" && <img className="slide__media" src={url} alt="" />}
      {state === "html" && <iframe className="slide__media" src={url} title={`Слайд ${position}`} />}
    </article>
  );
}
