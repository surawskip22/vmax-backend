from __future__ import annotations

from sqlalchemy import select

from . import models


def iso(value):
    return value.isoformat() if value else None


def user(user: models.User | None):
    if not user:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "public_profile_name": user.public_profile_name or "",
        "phone": user.phone,
        "is_admin": user.is_admin,
        "locale": user.locale,
        "profile_type": user.profile_type,
        "preferred_mode": user.preferred_mode,
    }


def public_contractor_name(db, item: models.Project | None) -> str:
    if not item:
        return "Wykonawca"

    def clean(value: str | None) -> str:
        return (value or "").strip()

    worker = (
        db.get(models.WorkerProfile, item.worker_profile_id)
        if item.worker_profile_id
        else None
    )
    project_workspace = (
        db.get(models.Workspace, item.workspace_id) if item.workspace_id else None
    )
    worker_workspace = (
        db.get(models.Workspace, worker.workspace_id)
        if worker and worker.workspace_id
        else None
    )
    owner = db.get(models.User, item.created_by_id) if item.created_by_id else None

    if project_workspace and project_workspace.kind == "company":
        return clean(project_workspace.name) or "Firma wykonawcza"

    if worker_workspace and worker_workspace.kind == "company":
        return clean(worker_workspace.name) or "Firma wykonawcza"

    if owner and owner.profile_type == "independent_contractor":
        return (
            clean(owner.public_profile_name)
            or clean(owner.name)
            or "Wykonawca"
        )

    if owner and owner.profile_type == "company_owner":
        owner_company = db.scalar(
            select(models.Workspace)
            .where(
                models.Workspace.owner_id == owner.id,
                models.Workspace.kind == "company",
            )
            .order_by(models.Workspace.created_at.asc())
        )
        return clean(owner_company.name if owner_company else None) or "Firma wykonawcza"

    if worker:
        return clean(worker.label) or "Wykonawca"

    return "Wykonawca"


def stage(item: models.ProjectStage):
    return {
        "id": item.id,
        "title": item.title,
        "position": item.position,
        "status": item.status,
    }


def media(item: models.MediaAsset):
    return {
        "id": item.id,
        "kind": item.kind,
        "purpose": item.purpose,
        "original_name": item.original_name,
        "content_type": item.content_type,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "storage_provider": item.storage_provider,
        "status": item.status,
        "url": f"/api/media/{item.id}",
        "created_at": iso(item.created_at),
    }


def comment(item: models.Comment):
    return {
        "id": item.id,
        "author": user(item.author),
        "guest_label": item.guest_label,
        "author_type": item.author_type,
        "author_label": item.author_label,
        "intent": item.intent,
        "body": item.body,
        "created_at": iso(item.created_at),
    }


def entry(item: models.Entry):
    return {
        "id": item.id,
        "project_id": item.project_id,
        "stage": stage(item.stage) if item.stage else None,
        "author": user(item.author),
        "guest_label": item.guest_label,
        "kind": item.kind,
        "body": item.body,
        "transcript": item.transcript,
        "ai_summary": item.ai_summary,
        "occurred_at": iso(item.occurred_at),
        "problem_status": item.problem_status,
        "media": [media(asset) for asset in item.media],
        "comments": [comment(item_comment) for item_comment in item.comments],
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


def project(item: models.Project, role: str | None = None, details: bool = False):
    data = {
        "id": item.id,
        "name": item.name,
        "client_name": item.client_name,
        "client_email": item.client_email,
        "address": item.address,
        "description": item.description,
        "status": item.status,
        "template": item.template,
        "planned_start_date": item.planned_start_date.isoformat()
        if item.planned_start_date
        else None,
        "planned_end_date": item.planned_end_date.isoformat()
        if item.planned_end_date
        else None,
        "schedule_uncertainty_days": item.schedule_uncertainty_days,
        "contract_amount": str(item.contract_amount)
        if item.contract_amount is not None
        else None,
        "contract_currency": item.contract_currency or "PLN",
        "workspace_id": item.workspace_id,
        "worker_profile_id": item.worker_profile_id,
        "role": role,
        "started_at": iso(item.started_at),
        "finished_at": iso(item.finished_at),
        "portfolio_enabled": item.portfolio_enabled,
        "portfolio_slug": item.portfolio_slug,
        "portfolio_summary": item.portfolio_summary,
        "details_locked": item.details_locked,
        "client_cover_media_id": item.client_cover_media_id,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
    if details:
        data["stages"] = [stage(item_stage) for item_stage in item.stages]
    return data


def report(item: models.Report):
    content = item.content or {}
    created_by = user(item.created_by) if item.created_by else None
    generated_by_label = content.get("generated_by_label")
    if not generated_by_label and created_by:
        generated_by_label = created_by.get("name") or created_by.get("email")
    report_date = content.get("report_date")
    if not report_date and item.period_from:
        report_date = item.period_from.date().isoformat()
    return {
        "id": item.id,
        "project_id": item.project_id,
        "title": item.title,
        "report_type": item.report_type,
        "status": item.status,
        "content": content,
        "period_from": iso(item.period_from),
        "period_to": iso(item.period_to),
        "published_at": iso(item.published_at),
        "report_date": report_date,
        "generated_by": created_by,
        "generated_by_label": generated_by_label,
        "pdf_url": (
            f"/api/projects/{item.project_id}/reports/{item.id}.pdf"
            if item.pdf_storage_key
            else None
        ),
        "legacy_pdf_url": f"/api/reports/{item.id}/pdf" if item.pdf_storage_key else None,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
