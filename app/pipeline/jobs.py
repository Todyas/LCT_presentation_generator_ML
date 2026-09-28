from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from app.models.audit_report import AuditReport
from app.models.outline import Outline
from app.models.presentation_ir import PresentationIR, SlideIR
from app.models.template_manifest import TemplateManifest


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class JobType(str, Enum):
    GENERATE_DECK = "GENERATE_DECK"
    REVISE_SLIDE = "REVISE_SLIDE"
    ACTIVATE_REVISION = "ACTIVATE_REVISION"


@dataclass
class Job:
    job_id: str
    status: JobStatus = JobStatus.PENDING
    result: Any | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    template_filename: str = ""
    template_path: str = ""
    brief: str = ""
    purpose: str = "project"
    slide_count: int = 12
    language: str = "ru"
    style: str = "balanced"
    stage: str = "queued"
    progress: int = 0
    job_type: JobType = JobType.GENERATE_DECK
    parent_job_id: str | None = None
    variant: str | None = None
    slide_position: int | None = None
    revision_request: dict[str, Any] | None = None
    task_id: str | None = None


@dataclass
class SlideRevision:
    revision_id: str
    deck_job_id: str
    variant: str
    slide_position: int
    revision_number: int
    source_job_id: str | None
    correction_prompt: str
    semantic_ir: dict[str, Any]
    preview_path: str | None
    audit_json: dict[str, Any] | None
    is_active: bool
    created_at: datetime


class Base(DeclarativeBase):
    pass


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class JobRow(Base):
    __tablename__ = "generation_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    template_filename: Mapped[str] = mapped_column(String(512), default="")
    template_path: Mapped[str] = mapped_column(Text, default="")
    brief: Mapped[str] = mapped_column(Text, default="")
    purpose: Mapped[str] = mapped_column(String(64), default="project")
    slide_count: Mapped[int] = mapped_column(Integer, default=12)
    language: Mapped[str] = mapped_column(String(16), default="ru")
    style: Mapped[str] = mapped_column(String(64), default="balanced")
    parent_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.job_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    variant: Mapped[str | None] = mapped_column(String(8), nullable=True)
    slide_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision_request: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SlideRevisionRow(Base):
    __tablename__ = "slide_revisions"
    __table_args__ = (
        UniqueConstraint(
            "deck_job_id",
            "variant",
            "slide_position",
            "revision_number",
            name="uq_slide_revision_number",
        ),
    )

    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deck_job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.job_id", ondelete="CASCADE"), index=True
    )
    variant: Mapped[str] = mapped_column(String(8), index=True)
    slide_position: Mapped[int] = mapped_column(Integer, index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    source_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.job_id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    correction_prompt: Mapped[str] = mapped_column(Text, default="")
    semantic_ir: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


_ENGINES: dict[str, Any] = {}


def get_engine(database_url: str):
    engine = _ENGINES.get(database_url)
    if engine is not None:
        return engine
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)
    _ENGINES[database_url] = engine
    return engine


def init_db(database_url: str) -> None:
    Base.metadata.create_all(get_engine(database_url))


def _serialize_result(result: Any | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "template_manifest": (
            result.template_manifest.model_dump(mode="json")
            if result.template_manifest is not None
            else None
        ),
        "variants": {
            code: {
                "variant": value.variant,
                "pptx_path": value.pptx_path,
                "pdf_path": value.pdf_path,
                "html_path": value.html_path,
                "preview_paths": value.preview_paths,
                "audit_report": (
                    value.audit_report.model_dump(mode="json")
                    if value.audit_report is not None
                    else None
                ),
                "presentation_ir": (
                    value.presentation_ir.model_dump(mode="json")
                    if value.presentation_ir is not None
                    else None
                ),
                "outline": (
                    value.outline.model_dump(mode="json")
                    if value.outline is not None
                    else None
                ),
                "revision": value.revision,
                "export_state": value.export_state,
                "error": value.error,
            }
            for code, value in result.variants.items()
        },
    }


def _deserialize_result(data: dict[str, Any] | None) -> Any | None:
    if data is None:
        return None
    from app.pipeline.orchestrator import ResultPackage, VariantResult

    variants = {}
    for code, value in data.get("variants", {}).items():
        variants[code] = VariantResult(
            variant=value["variant"],
            pptx_path=value.get("pptx_path"),
            pdf_path=value.get("pdf_path"),
            html_path=value.get("html_path"),
            preview_paths=value.get("preview_paths", []),
            audit_report=(
                AuditReport.model_validate(value["audit_report"])
                if value.get("audit_report") is not None
                else None
            ),
            presentation_ir=(
                PresentationIR.model_validate(value["presentation_ir"])
                if value.get("presentation_ir") is not None
                else None
            ),
            outline=(
                Outline.model_validate(value["outline"])
                if value.get("outline") is not None
                else None
            ),
            revision=value.get("revision", 1),
            export_state=value.get("export_state", "READY"),
            error=value.get("error"),
        )
    manifest_data = data.get("template_manifest")
    return ResultPackage(
        variants=variants,
        template_manifest=(
            TemplateManifest.model_validate(manifest_data)
            if manifest_data is not None
            else None
        ),
    )


def _row_to_job(row: JobRow) -> Job:
    return Job(
        job_id=row.job_id,
        status=JobStatus(row.status),
        result=_deserialize_result(row.result_json),
        output=row.output_json,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        template_filename=row.template_filename,
        template_path=row.template_path,
        brief=row.brief,
        purpose=row.purpose,
        slide_count=row.slide_count,
        language=row.language,
        style=row.style,
        stage=row.stage,
        progress=row.progress,
        job_type=JobType(row.job_type),
        parent_job_id=row.parent_job_id,
        variant=row.variant,
        slide_position=row.slide_position,
        revision_request=row.revision_request,
        task_id=row.task_id,
    )


class JobStore:
    def __init__(
        self,
        database_url: str = "sqlite+pysqlite:///:memory:",
        *,
        initialize_schema: bool = True,
    ) -> None:
        self.database_url = database_url
        self.engine = get_engine(database_url)
        if initialize_schema:
            init_db(database_url)

    def create(self, template_filename: str = "", **fields: Any) -> Job:
        now = datetime.now(UTC)
        job = Job(
            job_id=str(uuid.uuid4()),
            template_filename=template_filename,
            created_at=now,
            updated_at=now,
            **fields,
        )
        self.put(job)
        return job

    def put(self, job: Job) -> None:
        with Session(self.engine) as session:
            row = session.get(JobRow, job.job_id)
            if row is None:
                row = JobRow(job_id=job.job_id)
                session.add(row)
            row.status = job.status.value
            row.job_type = job.job_type.value
            row.stage = job.stage
            row.progress = job.progress
            row.error = job.error
            row.result_json = _serialize_result(job.result)
            row.output_json = job.output
            row.template_filename = job.template_filename
            row.template_path = job.template_path
            row.brief = job.brief
            row.purpose = job.purpose
            row.slide_count = job.slide_count
            row.language = job.language
            row.style = job.style
            row.parent_job_id = job.parent_job_id
            row.variant = job.variant
            row.slide_position = job.slide_position
            row.revision_request = job.revision_request
            row.task_id = job.task_id
            row.created_at = job.created_at
            row.updated_at = datetime.now(UTC)
            session.commit()

    def get(self, job_id: str) -> Job | None:
        with Session(self.engine) as session:
            row = session.get(JobRow, job_id)
            return _row_to_job(row) if row is not None else None

    def list_all(self) -> list[Job]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(JobRow).order_by(JobRow.created_at.desc())
            ).all()
            return [_row_to_job(row) for row in rows]

    def delete(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        with Session(self.engine) as session:
            session.execute(
                delete(SlideRevisionRow).where(SlideRevisionRow.deck_job_id == job_id)
            )
            session.execute(delete(JobRow).where(JobRow.parent_job_id == job_id))
            session.execute(delete(JobRow).where(JobRow.job_id == job_id))
            session.commit()
        return job

    def update(self, job_id: str, **fields: Any) -> None:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        for key, value in fields.items():
            setattr(job, key, value)
        self.put(job)

    def clear(self) -> None:
        with Session(self.engine) as session:
            session.execute(delete(JobRow))
            session.execute(delete(SlideRevisionRow))
            session.commit()


def _revision_row_to_model(row: SlideRevisionRow) -> SlideRevision:
    return SlideRevision(
        revision_id=row.revision_id,
        deck_job_id=row.deck_job_id,
        variant=row.variant,
        slide_position=row.slide_position,
        revision_number=row.revision_number,
        source_job_id=row.source_job_id,
        correction_prompt=row.correction_prompt,
        semantic_ir=row.semantic_ir,
        preview_path=row.preview_path,
        audit_json=row.audit_json,
        is_active=row.is_active,
        created_at=row.created_at,
    )


class SlideRevisionStore:
    def __init__(
        self,
        database_url: str = "sqlite+pysqlite:///:memory:",
        *,
        initialize_schema: bool = True,
    ) -> None:
        self.engine = get_engine(database_url)
        if initialize_schema:
            init_db(database_url)

    def save(
        self,
        *,
        deck_job_id: str,
        variant: str,
        slide_position: int,
        revision_number: int,
        source_job_id: str | None = None,
        correction_prompt: str,
        slide: SlideIR,
        preview_path: str | None,
        audit_json: dict[str, Any] | None,
    ) -> SlideRevision:
        with Session(self.engine) as session:
            if source_job_id is not None:
                existing = session.scalar(
                    select(SlideRevisionRow).where(
                        SlideRevisionRow.source_job_id == source_job_id
                    )
                )
                if existing is not None:
                    return _revision_row_to_model(existing)
            existing = session.scalar(
                select(SlideRevisionRow).where(
                    SlideRevisionRow.deck_job_id == deck_job_id,
                    SlideRevisionRow.variant == variant,
                    SlideRevisionRow.slide_position == slide_position,
                    SlideRevisionRow.revision_number == revision_number,
                )
            )
            if existing is not None:
                return _revision_row_to_model(existing)
            current = session.scalars(
                select(SlideRevisionRow).where(
                    SlideRevisionRow.deck_job_id == deck_job_id,
                    SlideRevisionRow.variant == variant,
                    SlideRevisionRow.slide_position == slide_position,
                    SlideRevisionRow.is_active.is_(True),
                )
            ).all()
            for row in current:
                row.is_active = False
            row = SlideRevisionRow(
                revision_id=str(uuid.uuid4()),
                deck_job_id=deck_job_id,
                variant=variant,
                slide_position=slide_position,
                revision_number=revision_number,
                source_job_id=source_job_id,
                correction_prompt=correction_prompt,
                semantic_ir=slide.model_dump(mode="json"),
                preview_path=preview_path,
                audit_json=audit_json,
                is_active=True,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _revision_row_to_model(row)

    def list(
        self, deck_job_id: str, variant: str, slide_position: int
    ) -> list[SlideRevision]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(SlideRevisionRow)
                .where(
                    SlideRevisionRow.deck_job_id == deck_job_id,
                    SlideRevisionRow.variant == variant,
                    SlideRevisionRow.slide_position == slide_position,
                )
                .order_by(SlideRevisionRow.revision_number.desc())
            ).all()
            return [_revision_row_to_model(row) for row in rows]

    def get(self, revision_id: str) -> SlideRevision | None:
        with Session(self.engine) as session:
            row = session.get(SlideRevisionRow, revision_id)
            return _revision_row_to_model(row) if row is not None else None

    def get_by_source_job(self, source_job_id: str) -> SlideRevision | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(SlideRevisionRow).where(
                    SlideRevisionRow.source_job_id == source_job_id
                )
            )
            return _revision_row_to_model(row) if row is not None else None
