"""Contracts for the inbound-reply endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReplyIngestResponse(BaseModel):
    """What happened to one inbound reply.

    ``status`` and ``disposition`` say different things on purpose. ``status``
    is the outcome of *this request* -- processed, queued, duplicate --
    while ``disposition`` is the state the reply is now in, which is what a
    reviewer's queue is filtered on.
    """

    model_config = ConfigDict(protected_namespaces=())

    status: str
    reply_id: str
    intent: str | None = None
    confidence: float = 0.0
    classifier_version: str | None = None
    disposition: str = ""
    reason: str = ""

    #: Present only when a promise was actually recorded.
    promise_id: str | None = None
    promised_amount: float | None = None
    promised_date: str | None = None


class ReplyReviewItem(BaseModel):
    """One reply waiting on a person."""

    model_config = ConfigDict(protected_namespaces=())

    reply_id: str
    from_email: str = ""
    subject: str = ""
    body: str = ""
    intent: str | None = None
    confidence: float = 0.0
    classifier_version: str | None = None
    #: Why this is in the queue rather than handled. The first thing a reviewer
    #: reads, so it is written for a person, not for a log parser.
    reason: str = ""
    received_at: datetime


class ReplyReviewQueue(BaseModel):
    """The human review queue."""

    model_config = ConfigDict(protected_namespaces=())

    count: int = 0
    items: list[ReplyReviewItem] = Field(default_factory=list)
