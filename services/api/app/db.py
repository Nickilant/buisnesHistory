import uuid
from time import sleep
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, create_engine, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = 'cases'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    external_case_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    case_number: Mapped[str] = mapped_column(String(128), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentEvent(Base):
    __tablename__ = 'document_events'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('cases.id', ondelete='CASCADE'), index=True)
    document_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    find_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    actual_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_item: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContentType(Base):
    __tablename__ = 'content_types'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('document_events.id', ondelete='CASCADE'), index=True)
    content_type_external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(Text)


class DocumentStatus(Base):
    __tablename__ = 'document_statuses'
    __table_args__ = (
        UniqueConstraint('case_id', 'document_key', 'content_type_external_id', name='uq_document_status_document'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('cases.id', ondelete='CASCADE'), index=True)
    document_key: Mapped[str] = mapped_column(String(128), index=True)
    content_type_external_id: Mapped[str] = mapped_column(String(128), index=True)
    is_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class BitrixPortal(Base):
    __tablename__ = 'bitrix_portals'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    member_id: Mapped[str] = mapped_column(String(255), index=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_in: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def init_db(max_attempts: int = 10, retry_delay_seconds: int = 3) -> None:
    last_error: OperationalError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql('SELECT pg_advisory_lock(214748364)')
                try:
                    Base.metadata.create_all(bind=conn)
                finally:
                    conn.exec_driver_sql('SELECT pg_advisory_unlock(214748364)')
            return
        except OperationalError as exc:
            last_error = exc
            if attempt == max_attempts:
                raise
            sleep(retry_delay_seconds)

    if last_error is not None:
        raise last_error
