"""Bounded shared-job execution for media requests."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Awaitable, Callable

from ..config.settings import settings
from .state_store import StateStore

logger = logging.getLogger(__name__)

JobExecutor = Callable[[], Awaitable[Any]]
StateListener = Callable[["SharedJob"], Awaitable[None]]


@dataclass
class RequestRecord:
    """One requester waiting on a shared job."""

    request_id: str
    chat_id: int
    user_id: int
    user_label: str
    created_monotonic: float = field(default_factory=monotonic)
    active: bool = True


@dataclass
class SharedJob:
    """One underlying provider execution shared by requesters."""

    job_id: str
    chat_id: int
    submitter_user_id: int
    provider: str
    provider_label: str
    original_url: str
    normalized_url: str
    state: str = "queued"
    created_monotonic: float = field(default_factory=monotonic)
    requesters: dict[str, RequestRecord] = field(default_factory=dict)
    result_future: asyncio.Future[Any] | None = None
    delivery_future: asyncio.Future[bool] | None = None
    task: asyncio.Task[Any] | None = None
    queue_position_at_submit: int = 1
    delivery_request_id: str | None = None
    last_delivery_error: Exception | None = None


@dataclass(frozen=True)
class JobSubmission:
    """Result of a submit call."""

    job: SharedJob
    request_id: str
    is_new_job: bool
    queue_position: int


class JobManager:
    """Coordinates queueing, duplicate suppression, and bounded concurrency."""

    def __init__(self, store: StateStore):
        self.store = store
        self._global_semaphore = asyncio.Semaphore(settings.GLOBAL_MAX_CONCURRENT_JOBS)
        self._chat_semaphores: dict[int, asyncio.Semaphore] = {}
        self._user_semaphores: dict[tuple[int, int], asyncio.Semaphore] = {}
        self._chat_semaphore_limits: dict[int, int] = {}
        self._user_semaphore_limits: dict[int, int] = {}
        self._jobs: dict[str, SharedJob] = {}
        self._active_jobs: dict[tuple[int, str], SharedJob] = {}
        self._listeners: list[StateListener] = []

    def add_state_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def submit(
        self,
        *,
        chat_id: int,
        user_id: int,
        user_label: str,
        provider: str,
        provider_label: str,
        original_url: str,
        normalized_url: str,
        execute: JobExecutor,
        duplicate_suppression: bool,
    ) -> JobSubmission:
        request_id = uuid.uuid4().hex
        active_key = (chat_id, normalized_url)
        existing = self._active_jobs.get(active_key) if duplicate_suppression else None
        if existing and existing.state not in {"completed", "failed", "cancelled"}:
            record = RequestRecord(
                request_id=request_id,
                chat_id=chat_id,
                user_id=user_id,
                user_label=user_label,
            )
            existing.requesters[request_id] = record
            self.store.create_request(
                request_id=request_id,
                job_id=existing.job_id,
                chat_id=chat_id,
                user_id=user_id,
                user_label=user_label,
                provider=provider,
                normalized_url=normalized_url,
                status=existing.state,
                joined_existing=True,
            )
            return JobSubmission(
                job=existing,
                request_id=request_id,
                is_new_job=False,
                queue_position=existing.queue_position_at_submit,
            )

        queued_count = sum(1 for job in self._jobs.values() if job.state == "queued")
        job = SharedJob(
            job_id=uuid.uuid4().hex,
            chat_id=chat_id,
            submitter_user_id=user_id,
            provider=provider,
            provider_label=provider_label,
            original_url=original_url,
            normalized_url=normalized_url,
            queue_position_at_submit=queued_count + 1,
            delivery_request_id=request_id,
        )
        job.result_future = asyncio.get_running_loop().create_future()
        job.delivery_future = asyncio.get_running_loop().create_future()
        job.requesters[request_id] = RequestRecord(
            request_id=request_id,
            chat_id=chat_id,
            user_id=user_id,
            user_label=user_label,
        )
        self._jobs[job.job_id] = job
        self._active_jobs[active_key] = job
        self.store.create_job(job.job_id, chat_id, normalized_url, provider, "queued")
        self.store.create_request(
            request_id=request_id,
            job_id=job.job_id,
            chat_id=chat_id,
            user_id=user_id,
            user_label=user_label,
            provider=provider,
            normalized_url=normalized_url,
            status="queued",
        )
        job.task = asyncio.create_task(self._run_job(job, execute))
        return JobSubmission(
            job=job,
            request_id=request_id,
            is_new_job=True,
            queue_position=job.queue_position_at_submit,
        )

    async def _run_job(self, job: SharedJob, execute: JobExecutor) -> None:
        try:
            async with self._global_semaphore:
                async with self._get_chat_semaphore(job.chat_id):
                    async with self._get_user_semaphore(job.chat_id, job.submitter_user_id):
                        await self._set_state(job, "running")
                        result = await execute()
                        if job.result_future and not job.result_future.done():
                            job.result_future.set_result(result)
                        await self._set_state(job, "completed")
        except asyncio.CancelledError:
            self.store.update_job_status(job.job_id, "cancelled")
            if job.result_future and not job.result_future.done():
                job.result_future.set_exception(asyncio.CancelledError())
            await self._set_state(job, "cancelled")
            raise
        except Exception as error:
            logger.exception("Shared job failed", extra={"job_id": job.job_id, "provider": job.provider})
            self.store.update_job_status(job.job_id, "failed", error.__class__.__name__)
            if job.result_future and not job.result_future.done():
                job.result_future.set_exception(error)
            await self._set_state(job, "failed")
        finally:
            self._active_jobs.pop((job.chat_id, job.normalized_url), None)

    async def _set_state(self, job: SharedJob, state: str) -> None:
        job.state = state
        self.store.update_job_status(job.job_id, state)
        if state in {"queued", "running", "failed", "cancelled"}:
            for request_id, request in job.requesters.items():
                if request.active:
                    self.store.update_request_status(request_id, state)
        await self._notify(job)

    async def _notify(self, job: SharedJob) -> None:
        for listener in list(self._listeners):
            try:
                await listener(job)
            except Exception:
                logger.exception("Job listener failed", extra={"job_id": job.job_id})

    def cancel_request(self, request_id: str) -> SharedJob | None:
        for job in self._jobs.values():
            request = job.requesters.get(request_id)
            if request and request.active:
                request.active = False
                self.store.update_request_status(request_id, "cancelled")
                if job.delivery_request_id == request_id and job.delivery_future and not job.delivery_future.done():
                    self._handoff_delivery(job, request_id, asyncio.CancelledError())
                elif job.delivery_request_id == request_id:
                    self._promote_delivery_request(job)
                if not any(item.active for item in job.requesters.values()):
                    if job.task and not job.task.done():
                        job.task.cancel()
                return job
        return None

    def get_latest_active_request_id(self, chat_id: int, user_id: int) -> str | None:
        candidates: list[RequestRecord] = []
        for job in self._jobs.values():
            for request in job.requesters.values():
                if request.chat_id == chat_id and request.user_id == user_id and request.active:
                    if job.state not in {"completed", "failed", "cancelled"}:
                        candidates.append(request)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.created_monotonic, reverse=True)
        return candidates[0].request_id

    def mark_request_completed(self, request_id: str, *, cache_hit: bool = False) -> None:
        self.store.update_request_status(request_id, "completed", cache_hit=cache_hit)
        self._deactivate_request(request_id)

    def mark_request_failed(self, request_id: str, status: str = "failed") -> None:
        self.store.update_request_status(request_id, status)
        self._deactivate_request(request_id)

    def is_delivery_request(self, job: SharedJob, request_id: str) -> bool:
        request = job.requesters.get(request_id)
        return bool(request and request.active and job.delivery_request_id == request_id)

    async def wait_for_delivery(self, job: SharedJob) -> bool:
        if job.delivery_future:
            return await job.delivery_future
        return False

    def mark_delivery_completed(self, job: SharedJob) -> None:
        job.last_delivery_error = None
        if job.delivery_future and not job.delivery_future.done():
            job.delivery_future.set_result(True)

    def mark_delivery_failed(self, job: SharedJob, request_id: str, error: Exception) -> bool:
        return self._handoff_delivery(job, request_id, error)

    def get_snapshot(self, chat_id: int) -> dict[str, int]:
        active = 0
        queued = 0
        watchers = 0
        for job in self._jobs.values():
            if job.chat_id != chat_id:
                continue
            if job.state == "running":
                active += 1
            elif job.state == "queued":
                queued += 1
            if job.state not in {"completed", "failed", "cancelled"}:
                watchers += sum(1 for request in job.requesters.values() if request.active)
        limits = self.store.get_queue_limits(chat_id)
        return {
            "active_jobs": active,
            "queued_jobs": queued,
            "active_requests": watchers,
            "chat_limit": limits["chat_max_concurrent_jobs"],
            "user_limit": limits["user_max_active_jobs"],
        }

    def update_chat_limits(self, chat_id: int, *, chat_limit: int | None = None, user_limit: int | None = None) -> None:
        """Refresh in-memory semaphores for updated owner-defined limits."""
        if chat_limit is not None:
            self._chat_semaphores[chat_id] = asyncio.Semaphore(chat_limit)
            self._chat_semaphore_limits[chat_id] = chat_limit
        if user_limit is not None:
            self._user_semaphore_limits[chat_id] = user_limit
            for key in [item for item in self._user_semaphores if item[0] == chat_id]:
                self._user_semaphores[key] = asyncio.Semaphore(user_limit)

    def _deactivate_request(self, request_id: str) -> None:
        for job_id, job in list(self._jobs.items()):
            request = job.requesters.get(request_id)
            if request:
                request.active = False
                if job.delivery_request_id == request_id and not (
                    job.delivery_future and job.delivery_future.done()
                ):
                    self._promote_delivery_request(job)
                if job.state in {"completed", "failed", "cancelled"} and not any(
                    item.active for item in job.requesters.values()
                ):
                    self._jobs.pop(job_id, None)
                return

    def _promote_delivery_request(self, job: SharedJob) -> None:
        current_future = job.delivery_future
        next_request_id = self._next_delivery_request_id(job)
        job.delivery_request_id = next_request_id
        job.last_delivery_error = None
        if current_future and not current_future.done():
            current_future.set_result(False)
        if next_request_id is not None:
            job.delivery_future = asyncio.get_running_loop().create_future()

    def _next_delivery_request_id(self, job: SharedJob, *, exclude_request_id: str | None = None) -> str | None:
        active_requests = [
            request for request in job.requesters.values()
            if request.active and request.request_id != exclude_request_id
        ]
        if not active_requests:
            return None
        active_requests.sort(key=lambda item: item.created_monotonic)
        return active_requests[0].request_id

    def _handoff_delivery(self, job: SharedJob, failed_request_id: str, error: Exception) -> bool:
        next_request_id = self._next_delivery_request_id(job, exclude_request_id=failed_request_id)
        current_future = job.delivery_future
        job.last_delivery_error = error
        if current_future and not current_future.done():
            current_future.set_result(False)
        job.delivery_request_id = next_request_id
        if next_request_id is None:
            return False
        job.delivery_future = asyncio.get_running_loop().create_future()
        return True

    def _get_chat_semaphore(self, chat_id: int) -> asyncio.Semaphore:
        limits = self.store.get_queue_limits(chat_id)
        limit = limits["chat_max_concurrent_jobs"]
        current = self._chat_semaphores.get(chat_id)
        if current is None or self._chat_semaphore_limits.get(chat_id) != limit:
            current = asyncio.Semaphore(limit)
            self._chat_semaphores[chat_id] = current
            self._chat_semaphore_limits[chat_id] = limit
        return current

    def _get_user_semaphore(self, chat_id: int, user_id: int) -> asyncio.Semaphore:
        limits = self.store.get_queue_limits(chat_id)
        limit = limits["user_max_active_jobs"]
        key = (chat_id, user_id)
        current = self._user_semaphores.get(key)
        if current is None or self._user_semaphore_limits.get(chat_id) != limit:
            current = asyncio.Semaphore(limit)
            self._user_semaphores[key] = current
            self._user_semaphore_limits[chat_id] = limit
        return current
