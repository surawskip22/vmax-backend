from __future__ import annotations

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
        "phone": user.phone,
        "is_admin": user.is_admin,
        "locale": user.locale,
    }


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
        "original_name": item.original_name,
        "content_type": item.content_type,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "status": item.status,
        "url": f"/api/media/{item.id}",
        "created_at": iso(item.created_at),
    }


def comment(item: models.Comment):
    return {
        "id": item.id,
        "author": user(item.author),
        "guest_label": item.guest_label,
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
        "workspace_id": item.workspace_id,
        "role": role,
        "started_at": iso(item.started_at),
        "finished_at": iso(item.finished_at),
        "portfolio_enabled": item.portfolio_enabled,
        "portfolio_slug": item.portfolio_slug,
        "portfolio_summary": item.portfolio_summary,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
    if details:
        data["stages"] = [stage(item_stage) for item_stage in item.stages]
    return data


def report(item: models.Report):
    return {
        "id": item.id,
        "project_id": item.project_id,
        "title": item.title,
        "report_type": item.report_type,
        "status": item.status,
        "content": item.content,
        "period_from": iso(item.period_from),
        "period_to": iso(item.period_to),
        "published_at": iso(item.published_at),
        "pdf_url": f"/api/reports/{item.id}/pdf" if item.pdf_storage_key else None,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
