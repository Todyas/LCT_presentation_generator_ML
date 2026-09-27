"""Persist generation jobs and slide revisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_jobs_and_revisions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_json", json_document, nullable=True),
        sa.Column("output_json", json_document, nullable=True),
        sa.Column("template_filename", sa.String(length=512), nullable=False),
        sa.Column("template_path", sa.Text(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("slide_count", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("style", sa.String(length=64), nullable=False),
        sa.Column("parent_job_id", sa.String(length=36), nullable=True),
        sa.Column("variant", sa.String(length=8), nullable=True),
        sa.Column("slide_position", sa.Integer(), nullable=True),
        sa.Column("revision_request", json_document, nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_job_id"], ["generation_jobs.job_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index("ix_generation_jobs_job_type", "generation_jobs", ["job_type"])
    op.create_index(
        "ix_generation_jobs_parent_job_id", "generation_jobs", ["parent_job_id"]
    )
    op.create_index("ix_generation_jobs_created_at", "generation_jobs", ["created_at"])
    op.create_table(
        "slide_revisions",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("deck_job_id", sa.String(length=36), nullable=False),
        sa.Column("variant", sa.String(length=8), nullable=False),
        sa.Column("slide_position", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=True),
        sa.Column("correction_prompt", sa.Text(), nullable=False),
        sa.Column("semantic_ir", json_document, nullable=False),
        sa.Column("preview_path", sa.Text(), nullable=True),
        sa.Column("audit_json", json_document, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["deck_job_id"], ["generation_jobs.job_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id"], ["generation_jobs.job_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint("source_job_id"),
        sa.UniqueConstraint(
            "deck_job_id",
            "variant",
            "slide_position",
            "revision_number",
            name="uq_slide_revision_number",
        ),
    )
    op.create_index(
        "ix_slide_revisions_deck_job_id", "slide_revisions", ["deck_job_id"]
    )
    op.create_index("ix_slide_revisions_variant", "slide_revisions", ["variant"])
    op.create_index(
        "ix_slide_revisions_slide_position", "slide_revisions", ["slide_position"]
    )
    op.create_index("ix_slide_revisions_created_at", "slide_revisions", ["created_at"])


def downgrade() -> None:
    op.drop_table("slide_revisions")
    op.drop_table("generation_jobs")
