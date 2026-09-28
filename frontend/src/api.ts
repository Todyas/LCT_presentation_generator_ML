import {
  normalizeAudit,
  normalizeDna,
  normalizeJob,
  normalizeResult,
  revisionIdentity,
  type AuditReport,
  type ExportKind,
  type JobEvent,
  type JobState,
  type JobStatus,
  type SlideRevisionBody,
  type TemplateDna,
} from "./normalize";

export const API_BASE = (import.meta.env.VITE_API_BASE || "https://lct.shmyaks.ru/api/").replace(/\/$/, "");

const TERMINAL: JobState[] = ["DONE", "PARTIAL", "FAILED"];

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function errorMessage(body: unknown, fallback: string) {
  if (typeof body === "string" && body.trim()) return body.trim();
  if (!body || typeof body !== "object" || !("detail" in body)) return fallback;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return fallback;
  return detail
    .map((item) => {
      if (!item || typeof item !== "object" || !("msg" in item)) return String(item);
      const message = String((item as { msg: unknown }).msg);
      const loc = Array.isArray((item as { loc?: unknown }).loc)
        ? (item as { loc: unknown[] }).loc.map(String).join(".")
        : "";
      return loc ? `${loc}: ${message}` : message;
    })
    .join("; ");
}

async function readBody(response: Response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function requestJson(response: Response) {
  const body = await readBody(response);
  if (!response.ok) throw new ApiError(response.status, errorMessage(body, response.statusText));
  return body;
}

export async function checkHealth() {
  const response = await fetch(`${API_BASE}/health`);
  return response.ok;
}

export async function analyzeTemplate(file: File): Promise<TemplateDna> {
  const form = new FormData();
  form.append("template", file, file.name);
  const response = await fetch(`${API_BASE}/templates/analyze`, { method: "POST", body: form });
  const body = await requestJson(response);
  return normalizeDna(body, file.name);
}

export async function startGeneration(input: {
  file: File;
  brief: string;
  slideCount: number;
  purpose: string;
}) {
  const form = new FormData();
  form.append("template", input.file, input.file.name);
  form.append("brief", input.brief);
  form.append("slide_count", String(input.slideCount));
  form.append("purpose", input.purpose);
  form.append("language", "ru");
  form.append("style", "balanced");
  const response = await fetch(`${API_BASE}/generate`, { method: "POST", body: form });
  const body = (await requestJson(response)) as { job_id?: string };
  if (!body?.job_id) throw new ApiError(response.status, "Сервер не вернул job_id");
  return body.job_id;
}

export async function getJob(jobId: string) {
  const response = await fetch(`${API_BASE}/jobs/${jobId}`);
  return normalizeJob(await requestJson(response));
}

export async function getJobResult(jobId: string) {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/result`);
  return normalizeResult(await requestJson(response));
}

export async function getAudit(jobId: string, variant: string): Promise<AuditReport> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/audit/${encodeURIComponent(variant)}`);
  return normalizeAudit(await requestJson(response));
}

export async function fetchPreview(jobId: string, variant: string, position: number, signal?: AbortSignal) {
  const response = await fetch(
    `${API_BASE}/jobs/${jobId}/previews/${encodeURIComponent(variant)}/${position}`,
    { signal },
  );
  if (!response.ok) {
    const body = await readBody(response);
    throw new ApiError(response.status, errorMessage(body, "Preview недоступен"));
  }
  const type = response.headers.get("content-type") ?? "";
  const blob = await response.blob();
  return {
    kind: type.includes("html") || type.startsWith("text/") ? ("html" as const) : ("image" as const),
    url: URL.createObjectURL(blob),
  };
}

export async function downloadVariantFile(jobId: string, variant: string, kind: ExportKind) {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/files/${encodeURIComponent(variant)}/${kind}`);
  if (!response.ok) {
    const body = await readBody(response);
    throw new ApiError(response.status, errorMessage(body, `Не удалось скачать ${kind.toUpperCase()}`));
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename\*?=(?:UTF-8''|"?)([^\";]+)/i.exec(disposition);
  const filename = match ? decodeURIComponent(match[1].replace(/"/g, "")) : `slideops-${variant}.${kind}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function postJob(path: string, body: unknown) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = (await requestJson(response)) as { job_id?: string };
  if (!payload?.job_id) throw new ApiError(response.status, "Сервер не вернул job_id");
  return payload.job_id;
}

export function reviseSlide(jobId: string, variant: string, position: number, body: SlideRevisionBody) {
  return postJob(`/jobs/${jobId}/slides/${encodeURIComponent(variant)}/${position}/revise`, body);
}

export async function listRevisionIds(jobId: string, variant: string, position: number) {
  const response = await fetch(
    `${API_BASE}/jobs/${jobId}/slides/${encodeURIComponent(variant)}/${position}/revisions`,
  );
  const body = await requestJson(response);
  const source = Array.isArray(body)
    ? body
    : body && typeof body === "object" && Array.isArray((body as { items?: unknown }).items)
      ? (body as { items: unknown[] }).items
      : [];
  return source.map(revisionIdentity).filter((item): item is NonNullable<typeof item> => Boolean(item));
}

export function activateRevision(
  jobId: string,
  variant: string,
  position: number,
  revisionId: string,
  baseRevision: number | null,
) {
  return postJob(
    `/jobs/${jobId}/slides/${encodeURIComponent(variant)}/${position}/revisions/${encodeURIComponent(revisionId)}/activate`,
    { base_revision: baseRevision },
  );
}

function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

async function streamJob(jobId: string, onEvent: (event: JobEvent) => void, signal: AbortSignal) {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/events`, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok || !response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\n\n/);
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const data = part
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (!data) continue;
      try {
        onEvent(JSON.parse(data) as JobEvent);
      } catch {
        // ignore malformed event frames
      }
    }
  }
}

export function waitForJob(
  jobId: string,
  onUpdate: (update: { event?: JobEvent; job?: JobStatus }) => void,
  signal?: AbortSignal,
) {
  const local = new AbortController();
  const abort = () => local.abort();
  signal?.addEventListener("abort", abort);
  let settled = false;

  return new Promise<JobStatus>((resolve, reject) => {
    const finish = (job: JobStatus) => {
      if (settled || !TERMINAL.includes(job.status)) return;
      settled = true;
      local.abort();
      resolve(job);
    };
    const fail = (error: unknown) => {
      if (settled) return;
      settled = true;
      local.abort();
      reject(error);
    };

    if (signal?.aborted) {
      fail(new DOMException("Aborted", "AbortError"));
      return;
    }

    void streamJob(jobId, (event) => {
      onUpdate({ event });
      if (event.status && TERMINAL.includes(event.status)) {
        void getJob(jobId).then(finish).catch(() => undefined);
      }
    }, local.signal).catch(() => undefined);

    void (async () => {
      while (!settled && !local.signal.aborted) {
        try {
          const job = await getJob(jobId);
          onUpdate({ job });
          finish(job);
          if (settled) return;
        } catch (error) {
          fail(error);
          return;
        }
        await delay(1500, local.signal);
      }
      if (!settled) fail(new DOMException("Aborted", "AbortError"));
    })();
  }).finally(() => signal?.removeEventListener("abort", abort));
}

export async function applySlideChange(input: {
  jobId: string;
  variant: string;
  position: number;
  revision: number;
  body: Omit<SlideRevisionBody, "base_revision">;
  onUpdate?: (update: { event?: JobEvent; job?: JobStatus }) => void;
  signal?: AbortSignal;
}) {
  const baseRevision = input.revision >= 1 ? input.revision : null;
  const childId = await reviseSlide(input.jobId, input.variant, input.position, {
    ...input.body,
    base_revision: baseRevision,
  });
  const child = await waitForJob(childId, (update) => input.onUpdate?.(update), input.signal);
  if (child.status === "FAILED") throw new ApiError(500, child.error || "Правка слайда не выполнилась");

  const revisions = await listRevisionIds(input.jobId, input.variant, input.position);
  const latest = revisions.at(-1);
  if (!latest) return;
  try {
    const activateId = await activateRevision(
      input.jobId,
      input.variant,
      input.position,
      latest.id,
      latest.revision ?? baseRevision,
    );
    const activated = await waitForJob(activateId, (update) => input.onUpdate?.(update), input.signal);
    if (activated.status === "FAILED") throw new ApiError(500, activated.error || "Не удалось активировать ревизию");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}
