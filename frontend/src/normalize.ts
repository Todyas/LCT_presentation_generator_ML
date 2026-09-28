export type TemplateDna = {
  name: string;
  slides: number | null;
  layouts: number | null;
  masters: number | null;
  colors: string[];
  fonts: string[];
  patterns: string[];
  score: number | null;
};

export type DeckSlide = {
  position: number;
  title: string;
  type: string;
};

export type DeckVariant = {
  id: string;
  rationale: string;
  slides: DeckSlide[];
};

export type AuditIssue = {
  slide: number | null;
  severity: "critical" | "warning";
  type: string;
  title: string;
  description: string;
};

export type AuditReport = {
  score: number | null;
  passed: boolean | null;
  issues: AuditIssue[];
};

export type JobState = "PENDING" | "RUNNING" | "DONE" | "PARTIAL" | "FAILED";
export type ExportKind = "pptx" | "pdf" | "html";

export type VariantResult = {
  variant: string;
  pptx_available: boolean;
  pdf_available: boolean;
  html_available: boolean;
  preview_count: number;
  audit_passed: boolean | null;
  error: string | null;
  revision: number;
  export_state: "READY" | "STALE";
};

export type JobStatus = {
  job_id: string;
  status: JobState;
  variants: VariantResult[];
  error: string | null;
  stage: string;
  progress: number;
  job_type: string;
  parent_job_id: string | null;
};

export type JobEvent = {
  job_id?: string;
  status?: JobState;
  stage?: string;
  progress?: number;
  error?: string | null;
};

export type SlideRevisionBody = {
  base_revision: number | null;
  comment: string;
  shorten_text: boolean;
  make_action_title: boolean;
  change_layout: boolean;
  add_visual: boolean;
  regenerate: boolean;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  return null;
}

function pickNumber(obj: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = obj[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return null;
}

function pickString(obj: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = obj[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function colorText(item: unknown) {
  if (typeof item === "string") return item.trim();
  const record = asRecord(item);
  if (!record) return "";
  const text = record.hex ?? record.value ?? record.color ?? record.name;
  return typeof text === "string" ? text.trim() : "";
}

function namedText(item: unknown) {
  if (typeof item === "string") return item.trim();
  const record = asRecord(item);
  if (!record) return "";
  const text = record.name ?? record.font ?? record.family ?? record.pattern ?? record.title ?? record.label ?? record.type;
  return typeof text === "string" ? text.trim() : "";
}

function pickList(obj: Record<string, unknown>, keys: string[], map: (item: unknown) => string) {
  for (const key of keys) {
    const value = obj[key];
    if (!Array.isArray(value)) continue;
    const items = value.map(map).filter(Boolean);
    if (items.length) return items;
  }
  return [];
}

function normalizeColor(value: string) {
  if (/^[0-9a-fA-F]{6}$/.test(value)) return `#${value}`;
  return value;
}

export function normalizeDna(payload: unknown, fallbackName = ""): TemplateDna {
  const root = asRecord(payload) ?? {};
  const source = asRecord(root.template_dna) ?? asRecord(root.dna) ?? asRecord(root.template) ?? root;
  return {
    name: pickString(source, ["template_name", "templateName", "filename", "file_name", "name"]) || fallbackName,
    slides: pickNumber(source, ["slides", "slide_count", "slideCount", "n_slides"]),
    layouts: pickNumber(source, ["layouts", "layout_count", "layoutCount", "n_layouts"]),
    masters: pickNumber(source, ["masters", "master_count", "masterCount", "n_masters"]),
    colors: pickList(source, ["colors", "palette", "color_palette"], colorText).map(normalizeColor),
    fonts: pickList(source, ["fonts", "font_families", "typefaces"], namedText),
    patterns: pickList(source, ["patterns", "layouts_used", "layout_patterns", "layout_names", "layout_types"], namedText),
    score: pickNumber(source, ["score", "template_match", "template_match_score", "match_score"]),
  };
}

function slideFrom(item: unknown, index: number): DeckSlide | null {
  const record = asRecord(item);
  if (!record) return null;
  const rawPosition = pickNumber(record, ["position", "slide_position", "slide", "number", "index"]);
  const position = rawPosition == null ? index + 1 : rawPosition === 0 ? index + 1 : rawPosition;
  return {
    position,
    title: pickString(record, ["title", "name", "headline"]) || `Слайд ${String(position).padStart(2, "0")}`,
    type: pickString(record, ["type", "layout_type", "layout", "kind", "role", "pattern"]),
  };
}

function variantFrom(item: unknown): DeckVariant | null {
  const record = asRecord(item);
  if (!record) return null;
  const id = pickString(record, ["variant", "id", "name", "key"]);
  if (!id) return null;
  const slideSource = record.slides ?? record.outline ?? record.pages ?? record.items;
  const slides = Array.isArray(slideSource)
    ? slideSource.map(slideFrom).filter((slide): slide is DeckSlide => Boolean(slide))
    : [];
  return {
    id,
    rationale: pickString(record, ["rationale", "explanation", "summary", "description", "why", "difference"]),
    slides,
  };
}

export function normalizeResult(payload: unknown): { variants: DeckVariant[]; dna: TemplateDna | null } {
  const root = asRecord(payload);
  if (!root) return { variants: [], dna: null };
  const variantSource = root.variants ?? root.decks ?? root.results;
  const variants = Array.isArray(variantSource)
    ? variantSource.map(variantFrom).filter((item): item is DeckVariant => Boolean(item))
    : [];
  const hasDna = ["template_dna", "dna", "template", "colors", "fonts"].some((key) => key in root);
  return { variants, dna: hasDna ? normalizeDna(payload) : null };
}

function severityOf(value: string): "critical" | "warning" {
  const normalized = value.toLowerCase();
  if (["critical", "error", "high", "blocker"].includes(normalized)) return "critical";
  return "warning";
}

function issueFrom(item: unknown): AuditIssue | null {
  const record = asRecord(item);
  if (!record) return null;
  const title = pickString(record, ["title", "code", "rule", "name", "kind"]);
  const description = pickString(record, ["description", "message", "detail", "text"]);
  if (!title && !description) return null;
  return {
    slide: pickNumber(record, ["slide", "slide_position", "position", "slide_number"]),
    severity: severityOf(pickString(record, ["severity", "level", "priority"])),
    type: pickString(record, ["type", "source", "layer", "checker", "origin"]) || "audit",
    title: title || description,
    description: description || title,
  };
}

export function normalizeAudit(payload: unknown): AuditReport {
  const root = asRecord(payload) ?? {};
  const source = asRecord(root.audit) ?? asRecord(root.report) ?? root;
  const issueSource = source.issues ?? source.findings ?? source.violations ?? source.items ?? source.problems;
  const issues = Array.isArray(issueSource)
    ? issueSource.map(issueFrom).filter((item): item is AuditIssue => Boolean(item))
    : [];
  const passedValue = source.passed ?? source.audit_passed ?? source.ok;
  return {
    score: pickNumber(source, ["score", "audit_score", "total", "value"]),
    passed: typeof passedValue === "boolean" ? passedValue : null,
    issues,
  };
}

function variantResultFrom(item: unknown): VariantResult | null {
  const record = asRecord(item);
  if (!record) return null;
  const variant = pickString(record, ["variant", "id", "name"]);
  if (!variant) return null;
  const exportState = pickString(record, ["export_state"]);
  return {
    variant,
    pptx_available: record.pptx_available !== false,
    pdf_available: record.pdf_available !== false,
    html_available: record.html_available === true,
    preview_count: pickNumber(record, ["preview_count"]) ?? 0,
    audit_passed: typeof record.audit_passed === "boolean" ? record.audit_passed : null,
    error: typeof record.error === "string" ? record.error : null,
    revision: pickNumber(record, ["revision"]) ?? 1,
    export_state: exportState === "STALE" ? "STALE" : "READY",
  };
}

export function normalizeJob(payload: unknown): JobStatus {
  const root = asRecord(payload) ?? {};
  const status = pickString(root, ["status"]) as JobState;
  const variants = Array.isArray(root.variants)
    ? root.variants.map(variantResultFrom).filter((item): item is VariantResult => Boolean(item))
    : [];
  return {
    job_id: pickString(root, ["job_id", "id"]),
    status: ["PENDING", "RUNNING", "DONE", "PARTIAL", "FAILED"].includes(status) ? status : "PENDING",
    variants,
    error: typeof root.error === "string" ? root.error : null,
    stage: pickString(root, ["stage"]) || "queued",
    progress: Math.max(0, Math.min(100, pickNumber(root, ["progress"]) ?? 0)),
    job_type: pickString(root, ["job_type"]) || "GENERATE_DECK",
    parent_job_id: typeof root.parent_job_id === "string" ? root.parent_job_id : null,
  };
}

export function slidesForVariant(deck: DeckVariant | undefined, previewCount: number) {
  if (deck && deck.slides.length) return deck.slides;
  return Array.from({ length: Math.max(0, previewCount) }, (_, index) => ({
    position: index + 1,
    title: `Слайд ${String(index + 1).padStart(2, "0")}`,
    type: "",
  }));
}

export function revisionIdentity(item: unknown) {
  if (typeof item === "string" && item) return { id: item, revision: null as number | null };
  const record = asRecord(item);
  if (!record) return null;
  const id = pickString(record, ["id", "revision_id", "uuid"]);
  if (!id) return null;
  return { id, revision: pickNumber(record, ["revision", "number", "index"]) };
}
