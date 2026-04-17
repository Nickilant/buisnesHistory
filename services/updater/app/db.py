import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, create_engine, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = 'cases'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_case_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    case_number: Mapped[str] = mapped_column(String(128), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    events: Mapped[list['DocumentEvent']] = relationship(back_populates='case', cascade='all, delete-orphan')


class DocumentEvent(Base):
    __tablename__ = 'document_events'
    __table_args__ = (
        UniqueConstraint('source_hash', name='uq_document_events_source_hash'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('cases.id', ondelete='CASCADE'), index=True)
    document_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    find_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    actual_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_item: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case: Mapped[Case] = relationship(back_populates='events')
    content_types: Mapped[list['ContentType']] = relationship(back_populates='event', cascade='all, delete-orphan')


class ContentType(Base):
    __tablename__ = 'content_types'
    __table_args__ = (
        UniqueConstraint('event_id', 'content_type_external_id', name='uq_content_type_event_external_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('document_events.id', ondelete='CASCADE'), index=True)
    content_type_external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(Text)

    event: Mapped[DocumentEvent] = relationship(back_populates='content_types')


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
