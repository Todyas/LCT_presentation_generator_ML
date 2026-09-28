export type PurposeId = "feature" | "product" | "project" | "initiative";

export const EXAMPLE_BRIEF =
  "Сделать презентацию для защиты сервиса, который автоматически создаёт корпоративные презентации по шаблону. Аудитория — эксперты VK Tech. Нужно показать проблему, подход, архитектуру, бизнес-эффект.";

export const PURPOSES: { id: PurposeId; label: string; hint: string }[] = [
  {
    id: "feature",
    label: "Фича",
    hint: "Акцент на одной возможности: сценарий, ограничения и критерий готовности.",
  },
  {
    id: "product",
    label: "Продукт",
    hint: "Структура вокруг ценности, аудитории и отличия от обычной генерации.",
  },
  {
    id: "project",
    label: "Проект",
    hint: "Статус, архитектура, план, риски и что уже можно показать.",
  },
  {
    id: "initiative",
    label: "Инициатива",
    hint: "Проблема, предложение, эффект и запрос решения у аудитории.",
  },
];

export const VARIANT_COPY: Record<string, { label: string; audience: string }> = {
  executive: { label: "Executive", audience: "Для руководства" },
  analytical: { label: "Analytical", audience: "Для проектной защиты" },
  pitch: { label: "Pitch", audience: "Для выступления" },
};

const VARIANT_ORDER = ["executive", "analytical", "pitch"];

export function orderVariants(ids: string[]) {
  return [...ids].sort((a, b) => {
    const ai = VARIANT_ORDER.indexOf(a);
    const bi = VARIANT_ORDER.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) || a.localeCompare(b);
  });
}

export function variantLabel(id: string) {
  return VARIANT_COPY[id]?.label ?? id;
}

export function variantAudience(id: string) {
  return VARIANT_COPY[id]?.audience ?? "Вариант генерации";
}

export function projectTitle(brief: string) {
  const clean = brief.trim().replace(/\s+/g, " ");
  if (!clean) return "Новая презентация";
  if (clean.includes("защиты сервиса")) return "Защита Шмякс";
  return clean.length > 42 ? `${clean.slice(0, 42)}…` : clean;
}

export function plural(n: number, one: string, few: string, many: string) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function formatBytes(size: number) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function stageLabel(stage: string) {
  const known: Record<string, string> = {
    queued: "В очереди",
    pending: "В очереди",
    running: "Выполняется",
    failed: "Ошибка",
    done: "Готово",
  };
  return known[stage] ?? stage.replaceAll("_", " ");
}
