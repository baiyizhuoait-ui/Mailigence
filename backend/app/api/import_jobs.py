"""Historical import endpoints: start, poll, cancel, latest job."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email_account import EmailAccount
from app.models.import_job import ImportJob, ImportStatus
from app.schemas.import_job import ImportJobOut, ImportStartRequest
from app.services import importer

router = APIRouter(tags=["import"])


@router.post(
    "/api/accounts/{account_id}/import",
    response_model=ImportJobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_import(
    account_id: int, req: ImportStartRequest, db: AsyncSession = Depends(get_db)
) -> ImportJob:
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if await importer.has_running_import(db, account_id):
        raise HTTPException(status_code=409, detail="An import is already running for this account.")

    since = req.since or (date.today() - timedelta(days=req.days or 7))
    range_days = req.days if req.days is not None else (date.today() - since).days

    job = ImportJob(
        account_id=account_id,
        status=ImportStatus.PENDING,
        range_days=range_days,
        since_date=since,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Launch the background task on the running event loop.
    importer.manager.start(job.id, account_id, since)
    return job


@router.get("/api/import-jobs/{job_id}", response_model=ImportJobOut)
async def get_import_job(job_id: int, db: AsyncSession = Depends(get_db)) -> ImportJob:
    job = await importer.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job


@router.post("/api/import-jobs/{job_id}/cancel")
async def cancel_import_job(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    job = await importer.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    if job.status not in (ImportStatus.PENDING, ImportStatus.RUNNING):
        raise HTTPException(status_code=409, detail=f"Job is already {job.status.value}.")
    importer.manager.request_cancel(job_id)
    return {"ok": True, "status": ImportStatus.CANCELLED.value}


@router.get("/api/accounts/{account_id}/import-jobs/latest", response_model=ImportJobOut)
async def latest_import_job(account_id: int, db: AsyncSession = Depends(get_db)) -> ImportJob:
    job = await importer.latest_job_for_account(db, account_id)
    if not job:
        raise HTTPException(status_code=404, detail="No import job for this account.")
    return job
