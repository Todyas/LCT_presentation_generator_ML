"""Deterministic visual regression harness (no LLM).

Builds a fixed 12-slide PresentationIR that exercises every component type,
renders it on each reference template and writes one contact-sheet PNG per
template to samples/output/visual_check/<template>.png, plus a text summary.

Usage:
    uv run python scripts/visual_check.py [--only lct]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from pptx import Presentation

from app.core.agents.prompt_registry import PromptRegistry
from app.core.auditor.audit_runner import run_full_audit
from app.core.builder.pptx_builder import PptxBuilder
from app.core.parser.template_parser import TemplateParser
from app.models.audit_report import Severity
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    ChartData,
    ChartSeries,
    ComparisonData,
    IconListData,
    IconListItem,
    ImagePlaceholder,
    MetricCard,
    PresentationIR,
    ProcessData,
    ProcessStep,
    SlideIR,
    TableData,
    TitleComponent,
)
from app.models.template_manifest import LayoutType

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "samples" / "output" / "visual_check"
TEMPLATE_DIR = Path(r"C:/Users/TODYAS/Downloads/Telegram Desktop")
TEMPLATES = {
    "lct2026": "ЛЦТ2026 Шаблон презентации.pptx",
    "vk_workspace": "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "vk_tech": "VK Tech шаблон.pptx",
    "vk_education": "Шаблон презентации VK Education.pptx",
}
_LIBREOFFICE_DIR = r"C:\Program Files\LibreOffice\program"


class _StubLLM:
    async def complete_structured(self, **_kwargs):
        raise RuntimeError("stub LLM: semantic audit disabled in visual_check")


def _bullets(*texts: str) -> BulletBlock:
    return BulletBlock(items=[BulletItem(text=t) for t in texts])


def build_ir(source_hash: str) -> PresentationIR:
    slides = [
        SlideIR(
            slide_index=0,
            layout_type=LayoutType.TITLE_SLIDE,
            title=TitleComponent(text="Цифровая платформа городских услуг"),
            components=[_bullets("Итоги пилота и план масштабирования на 2027 год")],
        ),
        SlideIR(
            slide_index=1,
            layout_type=LayoutType.CONTENT_1COL,
            title=TitleComponent(
                text="Пилот сократил время обработки заявок втрое", is_action_title=True
            ),
            components=[
                _bullets(
                    "Среднее время обработки заявки снизилось с 9 до 3 дней",
                    "Автоматическая маршрутизация покрывает 78% обращений граждан",
                    "Нагрузка на операторов колл-центра уменьшилась на 41%",
                    "Удовлетворённость пользователей выросла до 4,6 из 5",
                )
            ],
        ),
        SlideIR(
            slide_index=2,
            layout_type=LayoutType.CONTENT_2COL,
            title=TitleComponent(text="Сильные стороны и зоны роста"),
            components=[
                _bullets(
                    "Единое окно для всех городских сервисов",
                    "Интеграция с двенадцатью ведомственными системами",
                    "Прозрачная аналитика по каждому обращению",
                ),
                _bullets(
                    "Мобильное приложение пока не поддерживает офлайн-режим",
                    "Нужна доработка голосового ассистента",
                    "Требуется обучение сотрудников районных управ",
                ),
            ],
        ),
        SlideIR(
            slide_index=3,
            layout_type=LayoutType.KPI_DASHBOARD,
            title=TitleComponent(text="Ключевые показатели пилота"),
            components=[
                MetricCard(label="Обработано заявок", value="128 400", delta="+34%", trend="up"),
                MetricCard(label="Среднее время ответа", value="3 дня", delta="−67%", trend="down"),
                MetricCard(label="Экономия бюджета", value="412 млн ₽", delta="+18%", trend="up"),
            ],
        ),
        SlideIR(
            slide_index=4,
            layout_type=LayoutType.CHART_FOCUSED,
            title=TitleComponent(text="Рост числа обращений через платформу"),
            components=[
                ChartData(
                    chart_type="column",
                    categories=["I кв.", "II кв.", "III кв.", "IV кв."],
                    series=[
                        ChartSeries(name="2025", values=[18, 24, 29, 35]),
                        ChartSeries(name="2026", values=[27, 36, 44, 52]),
                    ],
                )
            ],
        ),
        SlideIR(
            slide_index=5,
            layout_type=LayoutType.TABLE_FOCUSED,
            title=TitleComponent(text="Результаты по районам"),
            components=[
                TableData(
                    headers=["Район", "Заявки", "Время, дни", "Оценка"],
                    rows=[
                        ["Центральный", "32 100", "2,8", "4,7"],
                        ["Северный", "27 450", "3,1", "4,6"],
                        ["Восточный", "24 900", "3,4", "4,5"],
                        ["Южный", "22 300", "3,0", "4,6"],
                    ],
                )
            ],
        ),
        SlideIR(
            slide_index=6,
            layout_type=LayoutType.COMPARISON,
            title=TitleComponent(text="До и после внедрения платформы"),
            components=[
                ComparisonData(
                    left_title="До внедрения",
                    left_items=[
                        "Бумажные заявления в МФЦ",
                        "Ручное распределение обращений",
                        "Отчётность раз в квартал",
                    ],
                    right_title="После внедрения",
                    right_items=[
                        "Подача заявки онлайн за пять минут",
                        "Автоматическая маршрутизация по ведомствам",
                        "Аналитика в реальном времени",
                    ],
                )
            ],
        ),
        SlideIR(
            slide_index=7,
            layout_type=LayoutType.PROCESS_TIMELINE,
            title=TitleComponent(text="Дорожная карта масштабирования"),
            components=[
                ProcessData(
                    steps=[
                        ProcessStep(title="Аудит", description="Анализ текущих процессов"),
                        ProcessStep(title="Интеграция", description="Подключение ведомственных систем"),
                        ProcessStep(title="Обучение", description="Подготовка сотрудников управ"),
                        ProcessStep(title="Запуск", description="Открытие для всех районов"),
                        ProcessStep(title="Оптимизация", description="Доработка по обратной связи"),
                    ]
                )
            ],
        ),
        SlideIR(
            slide_index=8,
            layout_type=LayoutType.CONTENT_1COL,
            title=TitleComponent(text="Что даёт платформа жителям"),
            components=[
                IconListData(
                    items=[
                        IconListItem(icon="speed", title="Быстро", description="Решение вопроса в среднем за три дня"),
                        IconListItem(icon="shield", title="Надёжно", description="Персональные данные защищены по ГОСТ"),
                        IconListItem(icon="people", title="Доступно", description="Единый вход через Госуслуги"),
                        IconListItem(icon="chart", title="Прозрачно", description="Статус заявки виден онлайн"),
                    ]
                )
            ],
        ),
        SlideIR(
            slide_index=9,
            layout_type=LayoutType.CONTENT_2COL,
            title=TitleComponent(text="Интерфейс мобильного приложения"),
            components=[
                ImagePlaceholder(alt_text="Скриншот мобильного приложения", role="illustration"),
                _bullets(
                    "Подача заявки в три касания",
                    "Push-уведомления о смене статуса",
                    "Фотофиксация проблемы с геометкой",
                ),
            ],
        ),
        SlideIR(
            slide_index=10,
            layout_type=LayoutType.CONTENT_1COL,
            title=TitleComponent(text="Риски и меры их снижения"),
            components=[
                _bullets(
                    "Перегрузка серверов в пиковые часы: горизонтальное масштабирование",
                    "Сопротивление изменениям: программа обучения и наставничества",
                    "Утечка данных: сертифицированная инфраструктура и аудит доступа",
                )
            ],
        ),
        SlideIR(
            slide_index=11,
            layout_type=LayoutType.SECTION_HEADER,
            title=TitleComponent(text="Просим одобрить масштабирование на весь город"),
            components=[
                _bullets(
                    "Бюджет второго этапа: 1,2 млрд ₽",
                    "Срок запуска во всех районах: IV квартал 2027",
                )
            ],
        ),
    ]
    return PresentationIR(variant="A", template_source_hash=source_hash, slides=slides)


def _ensure_tools_on_path() -> None:
    if shutil.which("soffice") is None and Path(_LIBREOFFICE_DIR).exists():
        os.environ["PATH"] = _LIBREOFFICE_DIR + os.pathsep + os.environ["PATH"]


def _to_pdf(pptx_path: Path, out_dir: Path) -> Path:
    profile = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"
    subprocess.run(
        [
            "soffice",
            "--headless",
            f"-env:UserInstallation=file:///{profile.as_posix().lstrip('/')}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(pptx_path),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    shutil.rmtree(profile, ignore_errors=True)
    return out_dir / (pptx_path.stem + ".pdf")


def _contact_sheet(pdf_path: Path, work: Path, dest: Path) -> None:
    for old in work.glob("page-*.png"):
        old.unlink()
    subprocess.run(
        ["pdftoppm", "-r", "40", "-png", str(pdf_path), str(work / "page")],
        check=True,
        capture_output=True,
    )
    pages = sorted(work.glob("page-*.png"))
    images = [Image.open(p).convert("RGB") for p in pages]
    if not images:
        return
    w, h = images[0].size
    cols, gap = 4, 6
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + gap) + gap, rows * (h + gap) + gap), "#808080")
    for i, img in enumerate(images):
        r, c = divmod(i, cols)
        sheet.paste(img.resize((w, h)), (gap + c * (w + gap), gap + r * (h + gap)))
    sheet.save(dest)


async def check_template(key: str, filename: str) -> dict:
    src = TEMPLATE_DIR / filename
    work = OUT_DIR / "_work" / key
    work.mkdir(parents=True, exist_ok=True)
    template_copy = work / "template.pptx"
    if not template_copy.exists() or template_copy.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, template_copy)

    manifest = TemplateParser().parse(str(template_copy))
    ir = build_ir(manifest.source_hash)
    try:
        from app.pipeline.layout_assignment import assign_layouts
    except ImportError:
        assign_layouts = None
    if assign_layouts is not None:
        ir = assign_layouts(ir, manifest)

    build = PptxBuilder().build(str(template_copy), manifest, ir)
    report = await run_full_audit(
        build,
        ir,
        manifest,
        "visual check",
        _StubLLM(),
        PromptRegistry(skills_dir=str(REPO_ROOT / "skills")),
        "stub",
    )
    critical = [i for i in report.issues if i.severity == Severity.CRITICAL]
    layout_names = [s.slide_layout.name for s in Presentation(build.pptx_path).slides]

    pdf = _to_pdf(Path(build.pptx_path), work)
    _contact_sheet(pdf, work, OUT_DIR / f"{key}.png")

    return {
        "key": key,
        "accent1": manifest.colors.accent1,
        "dk2": manifest.colors.dk2,
        "layouts": layout_names,
        "critical": Counter(i.issue_type.value for i in critical),
        "critical_msgs": [f"s{i.slide_index} {i.issue_type.value}: {i.message}" for i in critical],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    _ensure_tools_on_path()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for key, filename in TEMPLATES.items():
        if args.only and key not in args.only:
            continue
        try:
            res = await check_template(key, filename)
        except Exception as exc:  # noqa: BLE001 — report and continue with other templates
            print(f"== {key}: FAILED {type(exc).__name__}: {exc}")
            continue
        layouts = res["layouts"]
        adjacent = sum(
            1 for i in range(2, len(layouts)) if layouts[i] == layouts[i - 1]
        )
        print(f"== {key}  accent1={res['accent1']} dk2={res['dk2']}")
        for i, name in enumerate(layouts):
            print(f"   slide {i:2d}: {name}")
        print(
            f"   distinct layouts: {len(set(layouts))} | adjacent repeats (content): {adjacent}"
            f" | CRITICAL: {sum(res['critical'].values())} {dict(res['critical'])}"
        )
        if args.verbose:
            for m in res["critical_msgs"]:
                print(f"     {m}")
        print(f"   sheet: {OUT_DIR / (key + '.png')}")


if __name__ == "__main__":
    asyncio.run(main())
