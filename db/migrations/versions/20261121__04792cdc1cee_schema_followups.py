"""schema follow-ups: UUID defaults, cascading deletes, incident state check, unique URLs

Revision ID: 04792cdc1cee
Revises: 06128b7a2e2d
Create Date: 2026-11-21 19:00:00

Follow-ups from the review of the core schema (PR #8):
- Server-side UUID defaults, so raw-SQL inserts (e.g. the worker writing check_results) work without an id.
- ON DELETE CASCADE from history tables to resources, so a resource with history can be deleted.
- The same state CHECK on incidents as on resources.
- One registration per URL per course.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '04792cdc1cee'
down_revision: Union[str, Sequence[str], None] = '06128b7a2e2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_TABLES = ('resources', 'check_results', 'incidents', 'student_reports')
HISTORY_TABLES = ('check_results', 'incidents', 'student_reports')
STATES = "'HEALTHY', 'DEGRADED', 'SUSPECT', 'DOWN', 'AUTH_REQUIRED', 'MISSING'"


def _fk_name(table: str) -> str:
    # PostgreSQL's default name for the unnamed foreign keys created in 06128b7a2e2d.
    return f'{table}_resource_id_fkey'


def upgrade() -> None:
    for table in UUID_TABLES:
        op.alter_column(table, 'id', server_default=sa.text('gen_random_uuid()'))

    for table in HISTORY_TABLES:
        op.drop_constraint(_fk_name(table), table, type_='foreignkey')
        op.create_foreign_key(_fk_name(table), table, 'resources', ['resource_id'], ['id'], ondelete='CASCADE')

    op.create_check_constraint('ck_incidents_state', 'incidents', f'state IN ({STATES})')
    op.create_unique_constraint('uq_resources_course_url', 'resources', ['course_id', 'url'])


def downgrade() -> None:
    op.drop_constraint('uq_resources_course_url', 'resources', type_='unique')
    op.drop_constraint('ck_incidents_state', 'incidents', type_='check')

    for table in HISTORY_TABLES:
        op.drop_constraint(_fk_name(table), table, type_='foreignkey')
        op.create_foreign_key(_fk_name(table), table, 'resources', ['resource_id'], ['id'])

    for table in UUID_TABLES:
        op.alter_column(table, 'id', server_default=None)
