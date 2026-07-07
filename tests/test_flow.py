from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest


TEST_ROOT = Path(tempfile.mkdtemp(prefix="panmajster-tests-"))
os.environ.update(
    {
        "APP_ENV": "development",
        "SECRET_KEY": "test-secret",
        "DATABASE_URL": f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}",
        "STORAGE_PROVIDER": "database",
        "MEDIA_ROOT": str(TEST_ROOT / "media"),
        "WORKER_ENABLED": "false",
        "ADMIN_EMAILS": "admin@example.com",
    }
)

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from main import app
from panmajster import models
from panmajster import api as api_module
from panmajster import reporting
from panmajster.access import ProjectAccess
from panmajster.db import SessionLocal
from panmajster.demo_seed import seed_demo_data
from panmajster.reporting import _merge_generated_content
from panmajster.security import hash_secret
from panmajster.worker import process_next_job


def login(client: TestClient, email: str) -> dict:
    requested = client.post("/api/auth/request-code", json={"email": email})
    assert requested.status_code == 200
    verified = client.post(
        "/api/auth/verify",
        json={"email": email, "code": requested.json()["dev_code"]},
    )
    assert verified.status_code == 200
    return verified.json()["user"]


def password_login(client: TestClient, email: str, password: str) -> dict:
    response = client.post(
        "/api/auth/password",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()["user"]


def assert_pdf_response(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 1000


def assert_generated_pdf_report(response):
    assert response.status_code == 202
    report = response.json()
    assert report["status"] == "ready"
    assert report["pdf_url"]
    assert report["pdf_url"].endswith(f"/reports/{report['id']}.pdf")
    assert report["content"]["snapshot"] is True
    return report


def create_project_with_entry(
    client: TestClient, email: str, name: str = "Audio guard project"
) -> tuple[dict, dict]:
    login(client, email)
    project_response = client.post(
        "/api/projects",
        json={
            "name": name,
            "client_name": "Klient audio",
            "address": "ul. Audio 1",
            "template": "remont",
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()
    entry_response = client.post(
        f"/api/projects/{project['id']}/entries",
        json={
            "kind": "update",
            "body": "Audio test",
            "stage_id": project["stages"][0]["id"],
        },
    )
    assert entry_response.status_code == 201
    return project, entry_response.json()


def add_media_asset(
    project_id: str,
    entry_id: str,
    *,
    key: str,
    kind: str,
    content_type: str,
    size_bytes: int,
    content: bytes | None = None,
):
    with SessionLocal() as db:
        asset = models.MediaAsset(
            project_id=project_id,
            entry_id=entry_id,
            kind=kind,
            purpose="attachment",
            original_name=key.rsplit("/", 1)[-1],
            content_type=content_type,
            size_bytes=size_bytes,
            sha256="a" * 64,
            storage_provider="database",
            storage_key=key,
        )
        db.add(asset)
        if content is not None:
            db.add(
                models.StoredBlob(
                    storage_key=key,
                    content=content,
                    size_bytes=len(content),
                    sha256="b" * 64,
                )
            )
        db.commit()
        return asset.id


def test_complete_report_flow_and_media_integrity():
    with TestClient(app) as client:
        user = login(client, "admin@example.com")
        assert user["is_admin"] is True

        project_response = client.post(
            "/api/projects",
            json={
                "name": "Remont testowy",
                "client_name": "Anna Klient",
                "address": "ul. Testowa 1",
                "template": "remont",
            },
        )
        assert project_response.status_code == 201
        project = project_response.json()
        assert project["status"] == "assigned"
        assert client.get("/api/projects").json()[0]["status"] == "assigned"
        assert client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Zly etap", "stage_id": "missing-stage"},
        ).status_code == 400

        entry_response = client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Przygotowano miejsce pracy.",
                "stage_id": project["stages"][0]["id"],
                "client_ref": "offline-entry-1",
            },
        )
        assert entry_response.status_code == 201
        assert client.get(f"/api/projects/{project['id']}").json()["status"] == "in_progress"
        entry = entry_response.json()
        assert entry["stage"]["title"] == project["stages"][0]["title"]

        image = b"\x89PNG\r\n\x1a\n" + b"test-image"
        upload = client.post(
            f"/api/entries/{entry['id']}/media",
            files={"file": ("postep.png", image, "image/png")},
            data={"client_ref": "offline-file-1"},
        )
        assert upload.status_code == 201
        asset = upload.json()
        assert len(asset["sha256"]) == 64
        assert asset["storage_provider"] == "database"
        with SessionLocal() as db:
            stored_asset = db.get(models.MediaAsset, asset["id"])
            assert stored_asset is not None
            blob = db.scalar(
                select(models.StoredBlob).where(
                    models.StoredBlob.storage_key == stored_asset.storage_key
                )
            )
        assert blob is not None
        assert blob.content == image
        assert blob.sha256 == asset["sha256"]

        repeated_upload = client.post(
            f"/api/entries/{entry['id']}/media",
            files={"file": ("postep.png", image, "image/png")},
            data={"client_ref": "offline-file-1"},
        )
        assert repeated_upload.json()["id"] == asset["id"]

        report_response = client.post(
            f"/api/projects/{project['id']}/reports",
            json={"title": "Raport testowy", "report_type": "periodic"},
        )
        report_id = report_response.json()["id"]
        assert process_next_job() is True
        report = client.get(f"/api/reports/{report_id}").json()
        assert report["status"] == "draft"
        assert (
            report["content"]["stages"][0]["entries"][0]["media_ids"]
            == [asset["id"]]
        )

        published = client.post(
            f"/api/reports/{report_id}/publish", json={"pin": "1234"}
        )
        assert published.status_code == 200
        token = published.json()["token"]
        assert client.get(f"/api/public/reports/{token}").json()["requires_pin"]
        assert (
            client.get(f"/api/public/reports/{token}?pin=1234").status_code == 200
        )
        assert (
            client.get(
                f"/api/public/reports/{token}/media/{asset['id']}?pin=1234"
            ).content
            == image
        )
        pdf = client.get(f"/api/public/reports/{token}/pdf?pin=1234")
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")


def test_audio_upload_does_not_queue_server_transcription_by_default(monkeypatch):
    from panmajster.api import settings as api_settings

    monkeypatch.setattr(api_settings, "openai_api_key", "fake-key")
    monkeypatch.setattr(api_settings, "enable_server_transcription", False)
    with TestClient(app) as client:
        project, entry = create_project_with_entry(
            client, "audio-guard-default@example.com"
        )
        with SessionLocal() as db:
            before = db.scalar(
                select(func.count(models.Job.id)).where(
                    models.Job.job_type == "transcribe"
                )
            )

        upload = client.post(
            f"/api/entries/{entry['id']}/media",
            files={"file": ("opis.webm", b"fake-audio", "audio/webm")},
            data={"purpose": "voice_description"},
        )
        assert upload.status_code == 201
        assert upload.json()["kind"] == "audio"
        disabled = client.post(
            f"/api/projects/{project['id']}/transcribe",
            files={"file": ("opis.webm", b"fake-audio", "audio/webm")},
        )
        assert disabled.status_code == 503
        with SessionLocal() as db:
            after = db.scalar(
                select(func.count(models.Job.id)).where(
                    models.Job.job_type == "transcribe"
                )
            )
        assert after == before


def test_audio_upload_does_not_queue_server_transcription_when_flag_is_false(
    monkeypatch,
):
    from panmajster.api import settings as api_settings

    monkeypatch.setattr(api_settings, "openai_api_key", "fake-key")
    monkeypatch.setattr(api_settings, "enable_server_transcription", False)
    with TestClient(app) as client:
        _, entry = create_project_with_entry(client, "audio-guard-false@example.com")
        with SessionLocal() as db:
            before = db.scalar(
                select(func.count(models.Job.id)).where(
                    models.Job.job_type == "transcribe"
                )
            )

        upload = client.post(
            f"/api/entries/{entry['id']}/media",
            files={"file": ("notatka.webm", b"fake-audio", "audio/webm")},
            data={"purpose": "voice_note"},
        )
        assert upload.status_code == 201
        with SessionLocal() as db:
            after = db.scalar(
                select(func.count(models.Job.id)).where(
                    models.Job.job_type == "transcribe"
                )
            )
        assert after == before


def test_existing_transcribe_job_is_skipped_when_server_transcription_is_disabled(
    monkeypatch,
):
    from panmajster.api import settings as api_settings

    monkeypatch.setattr(api_settings, "enable_server_transcription", False)
    monkeypatch.setattr(
        "panmajster.worker.transcribe_asset",
        lambda asset: pytest.fail("server transcription should be disabled"),
    )
    with SessionLocal() as db:
        job = models.Job(
            job_type="transcribe",
            payload={"asset_id": "missing", "entry_id": "missing"},
        )
        db.add(job)
        db.commit()
        job_id = job.id

    assert process_next_job() is True
    with SessionLocal() as db:
        skipped = db.get(models.Job, job_id)
        assert skipped is not None
        assert skipped.status == "done"
        assert "disabled" in skipped.last_error


def test_server_transcription_can_be_enabled_explicitly(monkeypatch):
    from panmajster.api import settings as api_settings

    monkeypatch.setattr(api_settings, "openai_api_key", "fake-key")
    monkeypatch.setattr(api_settings, "enable_server_transcription", True)
    monkeypatch.setattr(
        "panmajster.worker.transcribe_asset",
        lambda asset: "Transkrypcja z mocka",
    )
    with TestClient(app) as client:
        _, entry = create_project_with_entry(client, "audio-guard-on@example.com")
        upload = client.post(
            f"/api/entries/{entry['id']}/media",
            files={"file": ("notatka.webm", b"fake-audio", "audio/webm")},
            data={"purpose": "voice_note"},
        )
        assert upload.status_code == 201

    with SessionLocal() as db:
        queued = db.scalar(
            select(func.count(models.Job.id)).where(
                models.Job.job_type == "transcribe",
                models.Job.status == "queued",
            )
        )
    assert queued >= 1
    assert process_next_job() is True
    with SessionLocal() as db:
        updated = db.get(models.Entry, entry["id"])
        assert updated is not None
        assert updated.transcript == "Transkrypcja z mocka"


def test_daily_and_final_project_pdf_reports_for_project_members():
    report_day = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

    with TestClient(app) as owner:
        login(owner, "pdf-owner@example.com")
        project = owner.post(
            "/api/projects",
            json={
                "name": "PDF owner project",
                "client_name": "Anna PDF",
                "address": "ul. PDF 1",
                "template": "custom",
                "planned_start_date": "2026-06-17",
                "planned_end_date": "2026-06-30",
                "contract_amount": "4200.00",
            },
        ).json()
        entry = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Wykonano prace testowe do raportu dziennego.",
                "stage_id": project["stages"][1]["id"],
                "occurred_at": report_day.isoformat(),
            },
        )
        assert entry.status_code == 201
        problem = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "problem",
                "body": "Uwaga do raportu PDF.",
                "stage_id": project["stages"][1]["id"],
                "occurred_at": report_day.isoformat(),
            },
        )
        assert problem.status_code == 201
        assert_pdf_response(
            owner.get(
                f"/api/projects/{project['id']}/report.pdf?type=daily&date=2026-06-18"
            )
        )
        assert_pdf_response(
            owner.get(f"/api/projects/{project['id']}/report.pdf?type=final")
        )
        assert_pdf_response(
            owner.get(
                f"/api/projects/{project['id']}/report.pdf?type=daily&date=2026-06-19"
            )
        )

    with TestClient(app) as investor:
        login(investor, "pdf-investor@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        investment = investor.post(
            "/api/projects",
            json={"name": "PDF investor project", "template": "custom"},
        ).json()
        assert_pdf_response(
            investor.get(
                f"/api/projects/{investment['id']}/report.pdf?type=daily&date=2026-06-18"
            )
        )
        assert_pdf_response(
            investor.get(f"/api/projects/{investment['id']}/report.pdf?type=final")
        )

    with TestClient(app) as independent:
        login(independent, "pdf-independent@example.com")
        independent.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        own_project = independent.post(
            "/api/projects",
            json={"name": "PDF independent project", "template": "custom"},
        ).json()
        assert_pdf_response(
            independent.get(
                f"/api/projects/{own_project['id']}/report.pdf?type=daily&date=2026-06-18"
            )
        )
        assert_pdf_response(
            independent.get(f"/api/projects/{own_project['id']}/report.pdf?type=final")
        )


def test_project_pdf_report_access_for_worker_guest_and_public_client():
    report_day = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)

    with TestClient(app) as worker_seed:
        login(worker_seed, "pdf-worker@example.com")

    with TestClient(app) as owner:
        login(owner, "pdf-access-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "PDF Access"},
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "PDF worker",
                "email": "pdf-worker@example.com",
            },
        ).json()
        assigned = owner.post(
            "/api/projects",
            json={
                "name": "PDF assigned worker project",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        unassigned = owner.post(
            "/api/projects",
            json={
                "name": "PDF unassigned worker project",
                "workspace_id": workspace_id,
                "template": "custom",
            },
        ).json()
        owner.post(
            f"/api/projects/{assigned['id']}/entries",
            json={
                "kind": "update",
                "body": "Wpis widoczny w raporcie pracownika.",
                "stage_id": assigned["stages"][1]["id"],
                "occurred_at": report_day.isoformat(),
            },
        )
        history_link = owner.post(
            f"/api/projects/{assigned['id']}/guest-links",
            json={"label": "PDF link history", "kind": "worker", "permission": "history"},
        ).json()
        add_only_link = owner.post(
            f"/api/projects/{assigned['id']}/guest-links",
            json={"label": "PDF link add", "kind": "worker", "permission": "add"},
        ).json()
        client_token = owner.get(f"/api/projects/{assigned['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

    with TestClient(app) as worker_client:
        login(worker_client, "pdf-worker@example.com")
        assert_pdf_response(
            worker_client.get(
                f"/api/projects/{assigned['id']}/report.pdf?type=daily&date=2026-06-18"
            )
        )
        assert_pdf_response(
            worker_client.get(f"/api/projects/{assigned['id']}/report.pdf?type=final")
        )
        assert (
            worker_client.get(
                f"/api/projects/{unassigned['id']}/report.pdf?type=final"
            ).status_code
            == 403
        )
        assert worker_client.get("/api/workers").json() == []

    with TestClient(app) as guest:
        assert_pdf_response(
            guest.get(
                f"/api/projects/{assigned['id']}/report.pdf?type=daily&date=2026-06-18",
                headers={"x-guest-token": history_link["token"]},
            )
        )
        assert_pdf_response(
            guest.get(
                f"/api/projects/{assigned['id']}/report.pdf?type=final&guest_token={history_link['token']}"
            )
        )
        assert (
            guest.get(
                f"/api/projects/{unassigned['id']}/report.pdf?type=final",
                headers={"x-guest-token": history_link["token"]},
            ).status_code
            == 403
        )
        assert (
            guest.get(
                f"/api/projects/{assigned['id']}/report.pdf?type=final",
                headers={"x-guest-token": add_only_link["token"]},
            ).status_code
            == 403
        )

    with TestClient(app) as public_client:
        assert public_client.get(f"/api/public/projects/{client_token}").status_code == 200
        assert (
            public_client.get(
                f"/api/projects/{assigned['id']}/report.pdf?type=final"
            ).status_code
            == 403
        )


def test_generated_pdf_report_panel_flow_for_owner():
    report_day = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)

    with TestClient(app) as client:
        login(client, "generated-owner@example.com")
        project = client.post(
            "/api/projects",
            json={
                "name": "Generated report project",
                "client_name": "Anna Snapshot",
                "address": "ul. Snapshot 1",
                "template": "custom",
            },
        ).json()
        client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Wpis do zapisanego raportu dziennego.",
                "stage_id": project["stages"][1]["id"],
                "occurred_at": report_day.isoformat(),
            },
        )

        assert client.get(f"/api/projects/{project['id']}/reports").json() == []
        daily = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "daily", "date": "2026-06-18"},
            )
        )
        assert daily["report_type"] == "daily"
        assert daily["report_date"] == "2026-06-18"
        assert daily["generated_by_label"]
        assert_pdf_response(client.get(daily["pdf_url"]))

        final = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )
        assert final["report_type"] == "final"
        assert_pdf_response(
            client.get(f"/api/projects/{project['id']}/reports/{final['id']}.pdf")
        )

        listed = client.get(f"/api/projects/{project['id']}/reports").json()
        assert [item["id"] for item in listed] == [final["id"], daily["id"]]


def test_public_client_link_returns_generated_ready_pdf_report():
    with TestClient(app) as client:
        login(client, "public-ready-report-owner@example.com")
        project = client.post(
            "/api/projects",
            json={
                "name": "Public ready PDF project",
                "client_name": "Anna Public",
                "address": "ul. Publiczna 1",
                "template": "custom",
            },
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

        generated = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )

        public_project = client.get(f"/api/public/projects/{token}").json()
        assert [item["id"] for item in public_project["reports"]] == [generated["id"]]
        public_report = public_project["reports"][0]
        assert public_report["status"] == "ready"
        assert public_report["pdf_url"].endswith(
            f"/api/public/projects/{token}/reports/{generated['id']}/pdf"
        )
        assert public_report["legacy_pdf_url"] is None


def test_public_client_link_returns_grouped_chronological_entries_with_audio():
    with TestClient(app) as client:
        login(client, "public-history-owner@example.com")
        project = client.post(
            "/api/projects",
            json={
                "name": "Public history project",
                "client_name": "Anna Public",
                "address": "ul. Historii 1",
                "template": "custom",
            },
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

        first = client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Pierwszy opis prac",
                "occurred_at": "2026-06-12T14:20:00+00:00",
            },
        ).json()
        first_image_id = add_media_asset(
            project["id"],
            first["id"],
            key="public/history/first.jpg",
            kind="image",
            content_type="image/jpeg",
            size_bytes=8,
            content=b"img-one",
        )
        second = client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "",
                "transcript": "Opis z nagrania audio",
                "occurred_at": "2026-06-12T15:05:00+00:00",
            },
        ).json()
        audio_id = add_media_asset(
            project["id"],
            second["id"],
            key="public/history/audio.webm",
            kind="audio",
            content_type="audio/webm",
            size_bytes=12,
            content=b"audio-bytes",
        )
        problem = client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "problem",
                "body": "Problem do omowienia",
                "occurred_at": "2026-06-13T09:10:00+00:00",
            },
        ).json()

        public = client.get(f"/api/public/projects/{token}").json()

        assert [item["id"] for item in public["entries"]] == [
            first["id"],
            second["id"],
            problem["id"],
        ]
        assert public["entries"][0]["body"] == "Pierwszy opis prac"
        assert public["entries"][0]["media"][0]["id"] == first_image_id
        assert public["entries"][0]["media"][0]["media_type"] == "image"
        assert public["entries"][0]["media"][0]["url"].startswith(
            f"/api/public/projects/{token}/media/"
        )
        assert public["entries"][1]["transcript"] == "Opis z nagrania audio"
        assert public["entries"][1]["media"][0]["id"] == audio_id
        assert public["entries"][1]["media"][0]["media_type"] == "audio"

        audio = client.get(public["entries"][1]["media"][0]["url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/webm")
        assert audio.content == b"audio-bytes"


def test_public_client_media_token_cannot_read_other_project_media():
    with TestClient(app) as client:
        login(client, "public-cross-media-owner@example.com")
        first = client.post(
            "/api/projects",
            json={"name": "Public media token", "template": "custom"},
        ).json()
        second = client.post(
            "/api/projects",
            json={"name": "Foreign media project", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{first['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        entry = client.post(
            f"/api/projects/{second['id']}/entries",
            json={"kind": "update", "body": "Foreign media"},
        ).json()
        foreign_asset_id = add_media_asset(
            second["id"],
            entry["id"],
            key="public/history/foreign.jpg",
            kind="image",
            content_type="image/jpeg",
            size_bytes=8,
            content=b"foreign",
        )

        assert (
            client.get(
                f"/api/public/projects/{token}/media/{foreign_asset_id}"
            ).status_code
            == 404
        )


def test_public_client_link_hides_deleted_progress_entry_from_history():
    with TestClient(app) as client:
        login(client, "public-deleted-entry-owner@example.com")
        project = client.post(
            "/api/projects",
            json={"name": "Public deleted history", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        entry = client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Wpis do usuniecia"},
        ).json()

        assert client.delete(f"/api/entries/{entry['id']}").status_code == 200
        assert client.get(f"/api/public/projects/{token}").json()["entries"] == []


def test_project_client_cover_selection_and_public_fallback():
    with TestClient(app) as client:
        login(client, "public-cover-owner@example.com")
        project = client.post(
            "/api/projects",
            json={"name": "Public cover project", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        first_entry = client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Starsze zdjecie",
                "occurred_at": "2026-06-12T14:20:00+00:00",
            },
        ).json()
        second_entry = client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Nowsze zdjecie",
                "occurred_at": "2026-06-12T15:05:00+00:00",
            },
        ).json()
        first_image_id = add_media_asset(
            project["id"],
            first_entry["id"],
            key="public/cover/first.jpg",
            kind="image",
            content_type="image/jpeg",
            size_bytes=8,
            content=b"first",
        )
        second_image_id = add_media_asset(
            project["id"],
            second_entry["id"],
            key="public/cover/second.jpg",
            kind="image",
            content_type="image/jpeg",
            size_bytes=8,
            content=b"second",
        )
        audio_id = add_media_asset(
            project["id"],
            second_entry["id"],
            key="public/cover/audio.webm",
            kind="audio",
            content_type="audio/webm",
            size_bytes=8,
            content=b"audio",
        )

        assert (
            client.get(f"/api/public/projects/{token}").json()["client_cover_media"][
                "id"
            ]
            == second_image_id
        )
        selected = client.patch(
            f"/api/projects/{project['id']}/client-cover",
            json={"media_id": first_image_id},
        )
        assert selected.status_code == 200
        assert selected.json()["client_cover_media_id"] == first_image_id
        assert (
            client.get(f"/api/public/projects/{token}").json()["client_cover_media"][
                "id"
            ]
            == first_image_id
        )
        assert (
            client.patch(
                f"/api/projects/{project['id']}/client-cover",
                json={"media_id": audio_id},
            ).status_code
            == 400
        )

        other = client.post(
            "/api/projects",
            json={"name": "Other cover project", "template": "custom"},
        ).json()
        other_entry = client.post(
            f"/api/projects/{other['id']}/entries",
            json={"kind": "update", "body": "Obce zdjecie"},
        ).json()
        other_image_id = add_media_asset(
            other["id"],
            other_entry["id"],
            key="public/cover/foreign.jpg",
            kind="image",
            content_type="image/jpeg",
            size_bytes=8,
            content=b"foreign",
        )
        assert (
            client.patch(
                f"/api/projects/{project['id']}/client-cover",
                json={"media_id": other_image_id},
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/projects/{project['id']}/client-cover",
                json={"media_id": None},
            ).json()["client_cover_media_id"]
            is None
        )

    with TestClient(app) as public_client:
        assert (
            public_client.patch(
                f"/api/projects/{project['id']}/client-cover",
                json={"media_id": second_image_id},
            ).status_code
            in {401, 403}
        )


def test_public_client_link_returns_null_cover_without_images():
    with TestClient(app) as client:
        login(client, "public-no-cover-owner@example.com")
        project = client.post(
            "/api/projects",
            json={"name": "Public no cover", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Tylko opis"},
        )

        assert client.get(f"/api/public/projects/{token}").json()["client_cover_media"] is None


def test_owner_can_delete_progress_entry_without_deleting_project_or_reports():
    with TestClient(app) as client:
        login(client, "delete-entry-owner@example.com")
        project = client.post(
            "/api/projects",
            json={"name": "Delete entry project", "template": "custom"},
        ).json()
        entry = client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Wpis do usuniecia z historii.",
                "stage_id": project["stages"][0]["id"],
            },
        ).json()
        report_before = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )
        assert_pdf_response(client.get(report_before["pdf_url"]))

        removed = client.delete(f"/api/entries/{entry['id']}")
        assert removed.status_code == 200
        assert client.get(f"/api/projects/{project['id']}").status_code == 200
        assert client.get(f"/api/projects/{project['id']}/entries").json() == []
        assert_pdf_response(client.get(report_before["pdf_url"]))

        report_after = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )
        assert report_after["id"] != report_before["id"]
        assert_pdf_response(client.get(report_after["pdf_url"]))


def test_company_worker_can_delete_own_entry_but_not_owner_entry():
    with TestClient(app) as worker_seed:
        login(worker_seed, "delete-worker@example.com")

    with TestClient(app) as owner:
        login(owner, "delete-worker-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Delete Worker"},
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Delete worker",
                "email": "delete-worker@example.com",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Delete worker assigned",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        owner_entry = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Wpis wlasciciela"},
        ).json()

    with TestClient(app) as worker_client:
        login(worker_client, "delete-worker@example.com")
        own_entry = worker_client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Wpis pracownika"},
        ).json()
        assert worker_client.delete(f"/api/entries/{owner_entry['id']}").status_code == 403
        assert worker_client.delete(f"/api/entries/{own_entry['id']}").status_code == 200
        entries = worker_client.get(f"/api/projects/{project['id']}/entries").json()
        assert [item["id"] for item in entries] == [owner_entry["id"]]


def test_guest_public_and_foreign_user_cannot_delete_progress_entry():
    with TestClient(app) as owner:
        login(owner, "delete-entry-access-owner@example.com")
        project = owner.post(
            "/api/projects",
            json={"name": "Delete entry access", "template": "custom"},
        ).json()
        entry = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Wpis chroniony"},
        ).json()
        guest_link = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={"label": "Guest no delete", "kind": "worker", "permission": "history"},
        ).json()
        client_token = owner.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

    with TestClient(app) as guest:
        assert (
            guest.delete(
                f"/api/entries/{entry['id']}",
                headers={"x-guest-token": guest_link["token"]},
            ).status_code
            == 403
        )

    with TestClient(app) as public_client:
        assert public_client.get(f"/api/public/projects/{client_token}").status_code == 200
        assert public_client.delete(f"/api/entries/{entry['id']}").status_code == 403

    with TestClient(app) as foreign:
        login(foreign, "delete-entry-foreign@example.com")
        assert foreign.delete(f"/api/entries/{entry['id']}").status_code == 403

    with TestClient(app) as owner_check:
        login(owner_check, "delete-entry-access-owner@example.com")
        entries = owner_check.get(f"/api/projects/{project['id']}/entries").json()
        assert [item["id"] for item in entries] == [entry["id"]]


def test_public_client_can_comment_entries_and_confirm_problem_status():
    with TestClient(app) as owner:
        login(owner, "client-comments-owner@example.com")
        project = owner.post(
            "/api/projects",
            json={"name": "Komentarze klienta", "template": "custom"},
        ).json()
        update_entry = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Zrobiono pierwszy etap"},
        ).json()
        problem_entry = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "problem", "body": "Wyciek przy umywalce"},
        ).json()
        other_project = owner.post(
            "/api/projects",
            json={"name": "Inny projekt komentarzy", "template": "custom"},
        ).json()
        token = owner.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        other_token = owner.get(
            f"/api/projects/{other_project['id']}/client-link"
        ).json()["url"].rsplit("/", 1)[-1]

    with TestClient(app) as public_client:
        added = public_client.post(
            f"/api/public/projects/{token}/entries/{update_entry['id']}/comments",
            json={"body": "Klient widzi postep."},
        )
        assert added.status_code == 201
        assert added.json()["comments"][0]["author_type"] == "client"
        assert added.json()["comments"][0]["author_label"] == "Klient"
        assert added.json()["comments"][0]["intent"] == "comment"
        assert added.json()["comments"][0]["body"] == "Klient widzi postep."

        assert (
            public_client.post(
                f"/api/public/projects/{token}/entries/{update_entry['id']}/comments",
                json={"body": "   "},
            ).status_code
            == 400
        )
        assert (
            public_client.post(
                f"/api/public/projects/{token}/entries/{update_entry['id']}/comments",
                json={"intent": "confirm_resolved"},
            ).status_code
            == 400
        )

        confirmed = public_client.post(
            f"/api/public/projects/{token}/entries/{problem_entry['id']}/comments",
            json={"intent": "confirm_resolved"},
        )
        assert confirmed.status_code == 201
        assert confirmed.json()["comments"][-1]["intent"] == "confirm_resolved"
        assert "rozwi" in confirmed.json()["comments"][-1]["body"]

        still_open = public_client.post(
            f"/api/public/projects/{token}/entries/{problem_entry['id']}/comments",
            json={"intent": "still_open"},
        )
        assert still_open.status_code == 201
        assert still_open.json()["comments"][-1]["intent"] == "still_open"

        assert (
            public_client.post(
                f"/api/public/projects/{other_token}/entries/{update_entry['id']}/comments",
                json={"body": "Nie ten projekt"},
            ).status_code
            == 404
        )
        assert (
            public_client.patch(
                f"/api/entries/{problem_entry['id']}",
                json={"problem_status": "resolved"},
            ).status_code
            == 403
        )
        assert public_client.delete(f"/api/entries/{problem_entry['id']}").status_code == 403

    with TestClient(app) as owner_check:
        login(owner_check, "client-comments-owner@example.com")
        entries = owner_check.get(f"/api/projects/{project['id']}/entries").json()
        progress = next(item for item in entries if item["id"] == update_entry["id"])
        problem = next(item for item in entries if item["id"] == problem_entry["id"])
        assert progress["comments"][0]["author_type"] == "client"
        assert progress["comments"][0]["body"] == "Klient widzi postep."
        assert [item["intent"] for item in problem["comments"]] == [
            "confirm_resolved",
            "still_open",
        ]


def test_problem_status_updates_are_restricted_to_problem_entries_and_project_access():
    with TestClient(app) as worker_seed:
        login(worker_seed, "problem-status-worker@example.com")

    with TestClient(app) as owner:
        login(owner, "problem-status-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Problem status"},
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Problem worker",
                "email": "problem-status-worker@example.com",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Problem status project",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        owner_problem = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "problem", "body": "Problem wlasciciela"},
        ).json()
        normal_entry = owner.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Zwykly wpis"},
        ).json()

        resolved = owner.patch(
            f"/api/entries/{owner_problem['id']}",
            json={"problem_status": "resolved"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["problem_status"] == "resolved"
        assert (
            owner.patch(
                f"/api/entries/{normal_entry['id']}",
                json={"problem_status": "resolved"},
            ).status_code
            == 400
        )

    with TestClient(app) as worker_client:
        login(worker_client, "problem-status-worker@example.com")
        worker_problem = worker_client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "problem", "body": "Problem pracownika"},
        ).json()
        updated = worker_client.patch(
            f"/api/entries/{worker_problem['id']}",
            json={"problem_status": "resolved"},
        )
        assert updated.status_code == 200
        assert updated.json()["problem_status"] == "resolved"
        assert (
            worker_client.patch(
                f"/api/entries/{owner_problem['id']}",
                json={"problem_status": "open"},
            ).status_code
            == 403
        )

    with TestClient(app) as foreign:
        login(foreign, "problem-status-foreign@example.com")
        assert (
            foreign.patch(
                f"/api/entries/{owner_problem['id']}",
                json={"problem_status": "open"},
            ).status_code
            == 403
        )


def test_public_client_can_download_generated_ready_pdf_report():
    with TestClient(app) as client:
        login(client, "public-ready-download-owner@example.com")
        project = client.post(
            "/api/projects",
            json={"name": "Public ready download", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        generated = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )

        assert_pdf_response(
            client.get(
                f"/api/public/projects/{token}/reports/{generated['id']}/pdf"
            )
        )


def test_public_client_cannot_download_report_from_other_project():
    with TestClient(app) as client:
        login(client, "public-cross-report-owner@example.com")
        first = client.post(
            "/api/projects",
            json={"name": "Public token project", "template": "custom"},
        ).json()
        second = client.post(
            "/api/projects",
            json={"name": "Foreign PDF project", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{first['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        foreign = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{second['id']}/reports",
                json={"type": "final"},
            )
        )

        assert (
            client.get(
                f"/api/public/projects/{token}/reports/{foreign['id']}/pdf"
            ).status_code
            == 404
        )


def test_public_client_link_returns_empty_report_list_without_reports():
    with TestClient(app) as client:
        login(client, "public-empty-report-owner@example.com")
        project = client.post(
            "/api/projects",
            json={"name": "Public empty reports", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

        assert client.get(f"/api/public/projects/{token}").json()["reports"] == []


def test_public_client_link_hides_failed_generating_and_ready_without_pdf():
    with TestClient(app) as client:
        login(client, "public-hidden-report-owner@example.com")
        project = client.post(
            "/api/projects",
            json={"name": "Public hidden reports", "template": "custom"},
        ).json()
        token = client.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

        with SessionLocal() as db:
            user_id = db.scalar(
                select(models.User.id).where(
                    models.User.email == "public-hidden-report-owner@example.com"
                )
            )
            assert user_id is not None
            db.add_all(
                [
                    models.Report(
                        project_id=project["id"],
                        created_by_id=user_id,
                        title="Generating report",
                        report_type="final",
                        status="generating",
                        pdf_storage_key="reports/generating.pdf",
                    ),
                    models.Report(
                        project_id=project["id"],
                        created_by_id=user_id,
                        title="Failed report",
                        report_type="final",
                        status="failed",
                        pdf_storage_key="reports/failed.pdf",
                    ),
                    models.Report(
                        project_id=project["id"],
                        created_by_id=user_id,
                        title="Ready without file",
                        report_type="final",
                        status="ready",
                    ),
                ]
            )
            db.commit()

        assert client.get(f"/api/public/projects/{token}").json()["reports"] == []


def test_generated_pdf_reports_can_be_created_sequentially_for_same_project():
    with TestClient(app) as client:
        login(client, "generated-sequential-owner@example.com")
        project = client.post(
            "/api/projects",
            json={
                "name": "Sequential report project",
                "client_name": "Anna Sequential",
                "address": "ul. Sequential 1",
                "template": "custom",
            },
        ).json()

        first = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "daily", "date": "2026-06-18"},
            )
        )
        second = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "daily", "date": "2026-06-18"},
            )
        )

        assert first["id"] != second["id"]
        assert client.get(f"/api/projects/{project['id']}").status_code == 200
        reports_response = client.get(f"/api/projects/{project['id']}/reports")
        assert reports_response.status_code == 200
        listed = reports_response.json()
        assert [item["status"] for item in listed] == ["ready", "ready"]
        assert all(item["pdf_url"] for item in listed)
        assert_pdf_response(client.get(first["pdf_url"]))
        assert_pdf_response(client.get(second["pdf_url"]))


def test_generated_pdf_report_storage_failure_does_not_create_ready_report(monkeypatch):
    with TestClient(app) as client:
        login(client, "pdf-storage-failure-owner@example.com")
        project = client.post(
            "/api/projects",
            json={
                "name": "PDF storage failure project",
                "client_name": "Anna Storage",
                "address": "ul. Storage 1",
                "template": "custom",
            },
        ).json()

        def fail_write(*args, **kwargs):
            raise RuntimeError("storage write failed")

        monkeypatch.setattr(api_module.storage, "write_bytes", fail_write)

        generated = client.post(
            f"/api/projects/{project['id']}/reports",
            json={"type": "final"},
        )
        assert generated.status_code == 503
        assert generated.json()["detail"].endswith("raportu PDF")
        assert client.get(f"/api/projects/{project['id']}").status_code == 200
        reports_response = client.get(f"/api/projects/{project['id']}/reports")
        assert reports_response.status_code == 200
        assert reports_response.json() == []


def test_pdf_report_generation_failure_returns_controlled_error(monkeypatch):
    with TestClient(app) as client:
        login(client, "pdf-failure-owner@example.com")
        project = client.post(
            "/api/projects",
            json={
                "name": "PDF failure project",
                "client_name": "Anna Failure",
                "address": "ul. Failure 1",
                "template": "custom",
            },
        ).json()

        def fail_pdf(*args, **kwargs):
            raise RuntimeError("renderer crashed")

        monkeypatch.setattr(api_module, "render_project_report_pdf", fail_pdf)

        generated = client.post(
            f"/api/projects/{project['id']}/reports",
            json={"type": "final"},
        )
        assert generated.status_code == 503
        assert generated.json()["detail"] == "Nie udało się wygenerować raportu PDF"

        legacy = client.get(f"/api/projects/{project['id']}/report.pdf?type=final")
        assert legacy.status_code == 503
        assert legacy.json()["detail"] == "Nie udało się wygenerować raportu PDF"


def test_generated_pdf_skips_oversized_image_without_reading_blob(monkeypatch):
    with TestClient(app) as client:
        project, entry = create_project_with_entry(
            client, "pdf-oversized-owner@example.com", "PDF oversized image project"
        )
        add_media_asset(
            project["id"],
            entry["id"],
            key="media/oversized-image.jpg",
            kind="image",
            content_type="image/jpeg",
            size_bytes=reporting.PDF_MAX_IMAGE_SOURCE_BYTES + 1,
        )

        def fail_read(key: str):
            pytest.fail(f"oversized image should not be read: {key}")

        monkeypatch.setattr(reporting.storage, "read_bytes", fail_read)

        generated = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )
        assert generated["status"] == "ready"
        assert client.get(f"/api/projects/{project['id']}").status_code == 200


def test_generated_pdf_skips_corrupted_image_and_keeps_project_loadable():
    with TestClient(app) as client:
        project, entry = create_project_with_entry(
            client, "pdf-corrupted-owner@example.com", "PDF corrupted image project"
        )
        add_media_asset(
            project["id"],
            entry["id"],
            key="media/corrupted-image.jpg",
            kind="image",
            content_type="image/jpeg",
            size_bytes=32,
            content=b"this is not an image",
        )

        generated = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )
        assert_pdf_response(client.get(generated["pdf_url"]))
        assert client.get(f"/api/projects/{project['id']}").status_code == 200


def test_generated_pdf_does_not_read_audio_blob(monkeypatch):
    with TestClient(app) as client:
        project, entry = create_project_with_entry(
            client, "pdf-audio-owner@example.com", "PDF audio guard project"
        )
        add_media_asset(
            project["id"],
            entry["id"],
            key="media/audio-note.webm",
            kind="audio",
            content_type="audio/webm;codecs=opus",
            size_bytes=2_000_000,
        )

        def fail_read(key: str):
            pytest.fail(f"audio blob should not be read during PDF render: {key}")

        monkeypatch.setattr(reporting.storage, "read_bytes", fail_read)

        generated = assert_generated_pdf_report(
            client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        )
        assert generated["status"] == "ready"


def test_generated_pdf_returns_conflict_when_project_report_is_already_generating():
    with TestClient(app) as client:
        login(client, "pdf-lock-owner@example.com")
        project = client.post(
            "/api/projects",
            json={
                "name": "PDF locked project",
                "client_name": "Anna Lock",
                "address": "ul. Lock 1",
                "template": "custom",
            },
        ).json()

        lock = api_module.acquire_report_generation_lock(project["id"])
        assert lock is not None
        try:
            response = client.post(
                f"/api/projects/{project['id']}/reports",
                json={"type": "final"},
            )
        finally:
            api_module.release_report_generation_lock(project["id"], lock)

        assert response.status_code == 409
        assert "generowany" in response.json()["detail"]
        assert client.get(f"/api/projects/{project['id']}").status_code == 200
        assert client.get(f"/api/projects/{project['id']}/reports").json() == []


def test_generated_pdf_reports_permissions_for_worker_guest_and_public_client():
    with TestClient(app) as worker_seed:
        login(worker_seed, "generated-worker@example.com")

    with TestClient(app) as owner:
        login(owner, "generated-access-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Generated Access"},
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Generated worker",
                "email": "generated-worker@example.com",
            },
        ).json()
        assigned = owner.post(
            "/api/projects",
            json={
                "name": "Generated assigned project",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        unassigned = owner.post(
            "/api/projects",
            json={
                "name": "Generated foreign project",
                "workspace_id": workspace_id,
                "template": "custom",
            },
        ).json()
        foreign_report = assert_generated_pdf_report(
            owner.post(
                f"/api/projects/{unassigned['id']}/reports",
                json={"type": "final"},
            )
        )
        history_link = owner.post(
            f"/api/projects/{assigned['id']}/guest-links",
            json={"label": "Generated history link", "kind": "worker", "permission": "history"},
        ).json()
        add_only_link = owner.post(
            f"/api/projects/{assigned['id']}/guest-links",
            json={"label": "Generated add link", "kind": "worker", "permission": "add"},
        ).json()
        client_token = owner.get(f"/api/projects/{assigned['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

    with TestClient(app) as worker_client:
        login(worker_client, "generated-worker@example.com")
        worker_report = assert_generated_pdf_report(
            worker_client.post(
                f"/api/projects/{assigned['id']}/reports",
                json={"type": "daily", "date": "2026-06-18"},
            )
        )
        assert_pdf_response(worker_client.get(worker_report["pdf_url"]))
        assert (
            worker_client.post(
                f"/api/projects/{unassigned['id']}/reports",
                json={"type": "final"},
            ).status_code
            == 403
        )
        listed = worker_client.get(f"/api/projects/{assigned['id']}/reports").json()
        assert listed
        assert all(item["project_id"] == assigned["id"] for item in listed)
        assert foreign_report["id"] not in {item["id"] for item in listed}

    with TestClient(app) as guest:
        guest_report = assert_generated_pdf_report(
            guest.post(
                f"/api/projects/{assigned['id']}/reports",
                json={"type": "final"},
                headers={"x-guest-token": history_link["token"]},
            )
        )
        assert_pdf_response(
            guest.get(
                guest_report["pdf_url"],
                headers={"x-guest-token": history_link["token"]},
            )
        )
        assert (
            guest.post(
                f"/api/projects/{unassigned['id']}/reports",
                json={"type": "final"},
                headers={"x-guest-token": history_link["token"]},
            ).status_code
            == 403
        )
        assert (
            guest.post(
                f"/api/projects/{assigned['id']}/reports",
                json={"type": "daily"},
                headers={"x-guest-token": add_only_link["token"]},
            ).status_code
            == 403
        )
        assert (
            guest.get(
                f"/api/projects/{assigned['id']}/reports",
                headers={"x-guest-token": add_only_link["token"]},
            ).status_code
            == 403
        )

    with TestClient(app) as public_client:
        assert public_client.get(f"/api/public/projects/{client_token}").status_code == 200
        assert (
            public_client.post(
                f"/api/projects/{assigned['id']}/reports",
                json={"type": "final"},
            ).status_code
            == 403
        )


def test_guest_permissions_and_revocation():
    with TestClient(app) as owner:
        login(owner, "owner@example.com")
        project = owner.post(
            "/api/projects", json={"name": "Projekt gościnny", "template": "custom"}
        ).json()
        invitation = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={
                "label": "Ekipa",
                "kind": "worker",
                "permission": "add",
                "expires_in_days": 30,
            },
        ).json()

        token = invitation["token"]
        assert owner.get(f"/api/guest/{token}").status_code == 200
        assert (
            owner.get(
                f"/api/projects/{project['id']}/entries",
                headers={"x-guest-token": token},
            ).json()
            == []
        )
        created = owner.post(
            f"/api/projects/{project['id']}/entries",
            headers={"x-guest-token": token},
            json={"kind": "problem", "body": "Brak materiału"},
        )
        assert created.status_code == 201

        rotated = owner.post(
            f"/api/projects/{project['id']}/guest-links/{invitation['id']}/rotate"
        )
        assert rotated.status_code == 200
        rotated_token = rotated.json()["token"]
        assert rotated_token != token
        assert owner.get(f"/api/guest/{token}").status_code == 404
        assert owner.get(f"/api/guest/{rotated_token}").status_code == 200

        owner.delete(
            f"/api/projects/{project['id']}/guest-links/{invitation['id']}"
        )
        assert owner.get(f"/api/guest/{rotated_token}").status_code == 404


def test_user_can_own_one_project_and_contribute_to_another():
    with TestClient(app) as first:
        login(first, "first@example.com")
        owned = first.post(
            "/api/projects", json={"name": "Własny projekt", "template": "custom"}
        ).json()
        second_project = first.post(
            "/api/projects", json={"name": "Projekt zespołu", "template": "custom"}
        ).json()
        first.post(
            f"/api/projects/{second_project['id']}/invite",
            json={"email": "second@example.com", "role": "contributor"},
        )

    with TestClient(app) as second:
        login(second, "second@example.com")
        projects = second.get("/api/projects").json()
        assert {item["id"] for item in projects} == {second_project["id"]}
        assert projects[0]["role"] == "contributor"
        own_second = second.post(
            "/api/projects",
            json={"name": "Samodzielna realizacja", "template": "custom"},
        ).json()
        roles = {item["id"]: item["role"] for item in second.get("/api/projects").json()}
        assert roles[second_project["id"]] == "contributor"
        assert roles[own_second["id"]] == "owner"
        assert owned["id"] not in roles


def test_project_detail_can_edit_details_uses_project_access_decision():
    with TestClient(app) as manager_seed:
        login(manager_seed, "access-manager@example.com")
    with TestClient(app) as contributor_seed:
        login(contributor_seed, "access-contributor@example.com")

    with TestClient(app) as owner:
        login(owner, "access-owner@example.com")
        project = owner.post(
            "/api/projects",
            json={"name": "Access cleanup project", "template": "custom"},
        ).json()
        project_id = project["id"]
        owner_detail = owner.get(f"/api/projects/{project_id}").json()
        assert owner_detail["can_edit_details"] is True

        manager_invite = owner.post(
            f"/api/projects/{project_id}/invite",
            json={"email": "access-manager@example.com", "role": "manager"},
        )
        assert manager_invite.status_code == 200
        contributor_invite = owner.post(
            f"/api/projects/{project_id}/invite",
            json={"email": "access-contributor@example.com", "role": "contributor"},
        )
        assert contributor_invite.status_code == 200
        worker_link = owner.post(
            f"/api/projects/{project_id}/guest-links",
            json={"label": "Link-only", "kind": "worker", "permission": "history"},
        ).json()
        client_token = owner.get(f"/api/projects/{project_id}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

    with TestClient(app) as manager:
        login(manager, "access-manager@example.com")
        manager_detail = manager.get(f"/api/projects/{project_id}").json()
        assert manager_detail["can_edit_details"] is True

    with TestClient(app) as contributor:
        login(contributor, "access-contributor@example.com")
        contributor_detail = contributor.get(f"/api/projects/{project_id}").json()
        assert contributor_detail["can_edit_details"] is True

    with SessionLocal() as db:
        stored_project = db.get(models.Project, project_id)
        for email, payload in {
            "access-manager@example.com": manager_detail,
            "access-contributor@example.com": contributor_detail,
        }.items():
            stored_user = db.scalar(select(models.User).where(models.User.email == email))
            role = db.scalar(
                select(models.ProjectMember.role).where(
                    models.ProjectMember.project_id == project_id,
                    models.ProjectMember.user_id == stored_user.id,
                )
            )
            assert payload["can_edit_details"] == ProjectAccess(
                project=stored_project,
                user=stored_user,
                role=role,
            ).can_edit_details()

    with TestClient(app) as owner:
        login(owner, "access-owner@example.com")
        locked = owner.patch(
            f"/api/projects/{project_id}",
            json={"details_locked": True},
        )
        assert locked.status_code == 200

    with TestClient(app) as contributor:
        login(contributor, "access-contributor@example.com")
        locked_detail = contributor.get(f"/api/projects/{project_id}").json()
        assert locked_detail["can_edit_details"] is False
        assert (
            contributor.patch(
                f"/api/projects/{project_id}",
                json={"description": "Blocked detail edit"},
            ).status_code
            == 403
        )
        assert (
            contributor.post(
                f"/api/projects/{project_id}/entries",
                json={"kind": "update", "body": "Contributor can still add progress"},
            ).status_code
            == 201
        )

    with TestClient(app) as guest:
        guest_detail = guest.get(
            f"/api/projects/{project_id}",
            headers={"x-guest-token": worker_link["token"]},
        ).json()
        assert guest_detail["can_edit_details"] is False
        assert (
            guest.patch(
                f"/api/projects/{project_id}",
                headers={"x-guest-token": worker_link["token"]},
                json={"description": "Guest cannot edit details"},
            ).status_code
            == 403
        )

    with TestClient(app) as public_client:
        public_project = public_client.get(
            f"/api/public/projects/{client_token}"
        ).json()["project"]
        assert "can_edit_details" not in public_project


def test_project_detail_worker_links_are_manager_only():
    with TestClient(app) as manager_seed:
        login(manager_seed, "worker-links-manager@example.com")
    with TestClient(app) as worker_seed:
        login(worker_seed, "worker-links-worker@example.com")

    with TestClient(app) as owner:
        login(owner, "worker-links-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Worker Links QA"},
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik worker-links",
                "email": "worker-links-worker@example.com",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Worker links payload",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        project_id = project["id"]
        owner.post(
            f"/api/projects/{project_id}/invite",
            json={"email": "worker-links-manager@example.com", "role": "manager"},
        )
        link = owner.post(
            f"/api/projects/{project_id}/guest-links",
            json={"label": "Link zarzadczo ukryty", "kind": "worker", "permission": "history"},
        ).json()
        owner_detail = owner.get(f"/api/projects/{project_id}").json()
        assert [item["id"] for item in owner_detail["worker_links"]] == [link["id"]]
        client_token = owner.get(f"/api/projects/{project_id}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

    with TestClient(app) as manager:
        login(manager, "worker-links-manager@example.com")
        manager_detail = manager.get(f"/api/projects/{project_id}").json()
        assert [item["id"] for item in manager_detail["worker_links"]] == [link["id"]]

    with TestClient(app) as worker_client:
        login(worker_client, "worker-links-worker@example.com")
        worker_detail = worker_client.get(f"/api/projects/{project_id}").json()
        assert worker_detail["role"] == "contributor"
        assert worker_detail["worker_links"] == []
        assert worker_client.get(f"/api/projects/{project_id}/guest-links").status_code == 403
        with SessionLocal() as db:
            before_count = db.scalar(
                select(func.count(models.GuestInvite.id)).where(
                    models.GuestInvite.project_id == project_id
                )
            )
        blocked = worker_client.post(
            f"/api/projects/{project_id}/guest-links",
            json={"label": "Nie wolno", "kind": "worker", "permission": "history"},
        )
        assert blocked.status_code == 403
        with SessionLocal() as db:
            after_count = db.scalar(
                select(func.count(models.GuestInvite.id)).where(
                    models.GuestInvite.project_id == project_id
                )
            )
        assert after_count == before_count

    with TestClient(app) as guest:
        guest_detail = guest.get(
            f"/api/projects/{project_id}",
            headers={"x-guest-token": link["token"]},
        ).json()
        assert guest_detail["worker_links"] == []

    with TestClient(app) as public_client:
        public_project = public_client.get(
            f"/api/public/projects/{client_token}"
        ).json()["project"]
        assert "worker_links" not in public_project


def test_investor_cannot_assign_foreign_worker_profile():
    with TestClient(app) as owner:
        login(owner, "foreign-worker-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Cudza firma"},
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        foreign_worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Cudzy wykonawca",
            },
        ).json()

    with TestClient(app) as investor:
        login(investor, "foreign-worker-investor@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        project = investor.post(
            "/api/projects",
            json={"name": "Inwestycja bez cudzego wykonawcy", "template": "custom"},
        ).json()

        blocked = investor.patch(
            f"/api/projects/{project['id']}",
            json={"worker_profile_id": foreign_worker["id"]},
        )

        assert blocked.status_code == 403
        details = investor.get(f"/api/projects/{project['id']}").json()
        assert details["worker_profile_id"] is None
        assert details["worker_profile"] is None


def test_ai_report_merge_preserves_source_media_and_metadata():
    fallback = {
        "summary": "Podsumowanie źródłowe",
        "stages": [
            {
                "title": "Instalacje",
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "date": "2026-06-11",
                        "text": "Opis źródłowy",
                        "kind": "update",
                        "problem_status": None,
                        "media_ids": ["asset-1"],
                    }
                ],
            }
        ],
        "problems": [],
    }
    generated = {
        "summary": "Lepsze podsumowanie",
        "stages": [
            {
                "title": "Inny etap",
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "text": "Zredagowany opis",
                        "media_ids": [],
                    }
                ],
            }
        ],
    }

    merged = _merge_generated_content(fallback, generated)

    assert merged["summary"] == "Lepsze podsumowanie"
    assert merged["stages"][0]["title"] == "Instalacje"
    assert merged["stages"][0]["entries"][0]["text"] == "Zredagowany opis"
    assert merged["stages"][0]["entries"][0]["media_ids"] == ["asset-1"]


def test_onboarding_company_management_and_project_edit_lock():
    with TestClient(app) as owner:
        login(owner, "company-owner@example.com")
        onboarding = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Dobra Ekipa",
                "preferred_mode": "expanded",
            },
        )
        assert onboarding.status_code == 200
        owner_user = onboarding.json()
        assert owner_user["profile_type"] == "company_owner"
        assert len(owner_user["workspaces"]) == 1
        workspace_id = owner_user["workspaces"][0]["id"]

        updated_workspace = owner.patch(
            f"/api/workspaces/{workspace_id}",
            json={
                "description": "Remonty i wykończenia",
                "phone": "+48 500 600 700",
                "address": "Kraków",
            },
        )
        assert updated_workspace.status_code == 200
        assert updated_workspace.json()["description"] == "Remonty i wykończenia"

        invitation = owner.post(
            f"/api/workspaces/{workspace_id}/invite",
            json={"email": "company-master@example.com", "role": "member"},
        )
        assert invitation.status_code == 200
        invitation_token = invitation.json()["url"].rsplit("/", 1)[-1]
        invitation_details = owner.get(f"/api/invitations/{invitation_token}")
        assert invitation_details.json()["email"] == "company-master@example.com"

        project = owner.post(
            "/api/projects",
            json={
                "name": "Łazienka klienta",
                "workspace_id": workspace_id,
                "template": "remont",
            },
        ).json()
        assert [stage["title"] for stage in project["stages"]] == [
            "Przed rozpoczęciem",
            "W trakcie realizacji",
            "Po zakończeniu",
        ]
        owner.post(
            f"/api/projects/{project['id']}/invite",
            json={"email": "project-master@example.com", "role": "contributor"},
        )

        with TestClient(app) as master:
            login(master, "project-master@example.com")
            master.post(
                "/api/onboarding",
                json={
                    "profile_type": "independent_contractor",
                    "preferred_mode": "field",
                },
            )
            editable = master.patch(
                f"/api/projects/{project['id']}",
                json={"description": "Opis dodany przez majstra"},
            )
            assert editable.status_code == 200

            locked = owner.patch(
                f"/api/projects/{project['id']}",
                json={"details_locked": True},
            )
            assert locked.status_code == 200
            denied = master.patch(
                f"/api/projects/{project['id']}",
                json={"description": "Ta zmiana ma być zablokowana"},
            )
            assert denied.status_code == 403
            progress = master.post(
                f"/api/projects/{project['id']}/entries",
                json={"kind": "update", "body": "Majster nadal dodaje postęp"},
            )
            assert progress.status_code == 201


def test_stable_client_link_updates_and_report_can_be_deleted():
    with TestClient(app) as client:
        login(client, "client-link-owner@example.com")
        client.post(
            "/api/onboarding",
            json={
                "profile_type": "independent_contractor",
                "preferred_mode": "expanded",
            },
        )
        project = client.post(
            "/api/projects",
            json={"name": "Stały link", "template": "custom"},
        ).json()
        assert project["status"] == "assigned"
        link = client.get(f"/api/projects/{project['id']}/client-link").json()
        token = link["url"].rsplit("/", 1)[-1]

        first_entry = client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Pierwszy dzień"},
        )
        assert first_entry.status_code == 201
        public_before = client.get(f"/api/public/projects/{token}").json()
        assert public_before["project"]["status"] == "in_progress"
        assert public_before["entries"][0]["stage"]["title"] == "W trakcie realizacji"
        assert [item["body"] for item in public_before["entries"]] == [
            "Pierwszy dzień"
        ]

        report = client.post(
            f"/api/projects/{project['id']}/reports",
            json={"title": "Raport dzienny", "report_type": "periodic"},
        ).json()
        for _ in range(10):
            if client.get(f"/api/reports/{report['id']}").json()["status"] == "draft":
                break
            assert process_next_job() is True
        published = client.post(
            f"/api/reports/{report['id']}/publish", json={}
        )
        assert published.status_code == 200
        assert published.json()["url"] == link["url"]

        second_entry = client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Drugi dzień"},
        )
        assert second_entry.status_code == 201
        public_after = client.get(f"/api/public/projects/{token}").json()
        assert public_after["project"]["status"] == "in_progress"
        assert [item["body"] for item in public_after["entries"]] == [
            "Pierwszy dzień",
            "Drugi dzień",
        ]
        assert [item["id"] for item in public_after["reports"]] == [report["id"]]
        assert (
            client.get(
                f"/api/public/projects/{token}/reports/{report['id']}/pdf"
            ).status_code
            == 200
        )

        deleted = client.delete(f"/api/reports/{report['id']}")
        assert deleted.status_code == 200
        assert client.get(f"/api/projects/{project['id']}/reports").json() == []
        assert client.get(f"/api/public/projects/{token}").json()["reports"] == []

    with TestClient(app) as public_client:
        assert public_client.get(f"/api/public/projects/{token}").status_code == 200
        assert public_client.patch(
            f"/api/entries/{first_entry.json()['id']}",
            json={"stage_id": project["stages"][2]["id"]},
        ).status_code == 403
        assert (
            public_client.patch(
                f"/api/projects/{project['id']}",
                json={"status": "completed"},
            ).status_code
            == 403
        )
        assert public_client.get("/api/projects").status_code == 401
        assert public_client.get("/api/workspaces").status_code == 401
        assert public_client.get("/api/workers").status_code == 401


def test_worker_link_without_email_is_project_scoped_and_visible_in_team():
    with TestClient(app) as owner:
        login(owner, "worker-link-owner@example.com")
        workspace = owner.post(
            "/api/workspaces",
            json={"name": "Firma testowa", "kind": "company"},
        ).json()
        assigned = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie przypisane",
                "workspace_id": workspace["id"],
                "template": "custom",
            },
        ).json()
        other = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie obce",
                "workspace_id": workspace["id"],
                "template": "custom",
            },
        ).json()

        link_response = owner.post(
            f"/api/projects/{assigned['id']}/guest-links",
            json={
                "label": "Mieciu bez maila",
                "kind": "worker",
                "permission": "history",
                "expires_in_days": 30,
            },
        )
        assert link_response.status_code == 201
        link = link_response.json()
        assert link["email"] == ""
        assert link["kind"] == "worker"
        assert link["account_type"] == "link_only"
        token = link["token"]

        resolved = owner.get(f"/api/guest/{token}")
        assert resolved.status_code == 200
        assert resolved.json()["project_id"] == assigned["id"]
        assert resolved.json()["kind"] == "worker"

        assigned_detail = owner.get(f"/api/projects/{assigned['id']}").json()
        assert assigned_detail["status"] == "assigned"
        assert assigned_detail["worker_links"][0]["label"] == "Mieciu bez maila"
        assert assigned_detail["worker_links"][0]["account_type"] == "link_only"
        team_detail = owner.get(f"/api/workspaces/{workspace['id']}").json()
        assert team_detail["worker_links"][0]["project_id"] == assigned["id"]
        assert team_detail["worker_links"][0]["project_name"] == "Zlecenie przypisane"

        client_link = owner.get(f"/api/projects/{assigned['id']}/client-link").json()
        client_token = client_link["url"].rsplit("/", 1)[-1]
        public_project = owner.get(f"/api/public/projects/{client_token}").json()
        assert public_project["project"]["id"] == assigned["id"]

    with TestClient(app) as worker_link:
        assert (
            worker_link.get(
                f"/api/projects/{assigned['id']}/entries",
                headers={"x-guest-token": token},
            ).status_code
            == 200
        )
        assert (
            worker_link.post(
                f"/api/projects/{assigned['id']}/entries",
                headers={"x-guest-token": token},
                json={
                    "kind": "update",
                    "body": "Drugi postęp od linku",
                    "stage_id": assigned_detail["stages"][2]["id"],
                },
            ).status_code
            == 201
        )
        worker_entries = worker_link.get(
            f"/api/projects/{assigned['id']}/entries",
            headers={"x-guest-token": token},
        ).json()
        assert worker_entries[0]["stage"]["title"] == "Po zakończeniu"
        assert (
            worker_link.get(
                f"/api/projects/{assigned['id']}",
                headers={"x-guest-token": token},
            ).json()["status"]
            == "in_progress"
        )
        assert (
            worker_link.get(
                f"/api/projects/{other['id']}/entries",
                headers={"x-guest-token": token},
            ).status_code
            == 403
        )
        assert (
            worker_link.post(
                f"/api/projects/{other['id']}/entries",
                headers={"x-guest-token": token},
                json={"kind": "update", "body": "Nie powinno przejść"},
            ).status_code
            == 403
        )
        assert (
            worker_link.get("/api/projects", headers={"x-guest-token": token}).status_code
            == 401
        )
        assert (
            worker_link.get("/api/workers", headers={"x-guest-token": token}).status_code
            == 401
        )


def test_worker_link_with_email_creates_usable_project_invitation():
    with TestClient(app) as owner:
        login(owner, "worker-email-owner@example.com")
        project = owner.post(
            "/api/projects",
            json={"name": "Zlecenie z kontem majstra", "template": "custom"},
        ).json()
        link_response = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={
                "label": "Stały majster",
                "email": "staly-majster@example.com",
                "kind": "worker",
                "permission": "history",
                "expires_in_days": 30,
            },
        )
        assert link_response.status_code == 201
        assert link_response.json()["account_type"] == "account"

    with TestClient(app) as worker:
        login(worker, "staly-majster@example.com")
        projects = worker.get("/api/projects").json()
        assert [item["id"] for item in projects] == [project["id"]]
        assert projects[0]["role"] == "contributor"


def test_password_login_for_local_seed_style_accounts():
    with SessionLocal() as db:
        user = models.User(
            email="local-password-user@example.com",
            name="Lokalny Password",
            profile_type="investor",
            preferred_mode="expanded",
            password_hash=hash_secret("test1234"),
        )
        db.add(user)
        db.flush()
        db.add(models.BetaEntitlement(user_id=user.id, active=True, note="test"))
        db.commit()

    with TestClient(app) as client:
        user = password_login(client, "local-password-user@example.com", "test1234")
        assert user["email"] == "local-password-user@example.com"
        assert user["profile_type"] == "investor"
        assert client.post(
            "/api/auth/password",
            json={"email": "local-password-user@example.com", "password": "wrong"},
        ).status_code == 400


def test_worker_profiles_roles_and_assignment_flow():
    with TestClient(app) as owner:
        login(owner, "roles-company-owner@example.com")
        user = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma ról",
            },
        ).json()
        workspace_id = user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Majster bez maila",
                "profile_kind": "craftsman",
            },
        )
        assert worker.status_code == 201
        worker_id = worker.json()["id"]
        crew = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Ekipa łazienkowa",
                "profile_kind": "crew",
            },
        )
        assert crew.status_code == 201
        assert crew.json()["profile_kind"] == "crew"
        removed = owner.delete(f"/api/workers/{crew.json()['id']}")
        assert removed.status_code == 200
        workspace = owner.get(f"/api/workspaces/{workspace_id}").json()
        inactive = [
            item
            for item in workspace["worker_profiles"]
            if item["id"] == crew.json()["id"]
        ][0]
        assert inactive["active"] is False
        assert crew.json()["id"] not in [
            item["id"] for item in owner.get(f"/api/workers?workspace_id={workspace_id}").json()
        ]
        activated = owner.post(f"/api/workers/{crew.json()['id']}/activate")
        assert activated.status_code == 200
        assert activated.json()["active"] is True
        assert activated.json()["id"] == crew.json()["id"]
        assert crew.json()["id"] in [
            item["id"] for item in owner.get(f"/api/workers?workspace_id={workspace_id}").json()
        ]
        project = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie z wykonawcą",
                "workspace_id": workspace_id,
                "worker_profile_id": worker_id,
                "template": "custom",
            },
        ).json()
        details = owner.get(f"/api/projects/{project['id']}").json()
        assert details["worker_profile"]["label"] == "Majster bez maila"
        assert details["worker_profile_id"] == worker_id

        link = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={
                "worker_profile_id": worker_id,
                "label": "Majster bez maila",
                "kind": "worker",
                "permission": "history",
            },
        ).json()
        assert link["account_type"] == "link_only"

    with TestClient(app) as guest:
        token = link["token"]
        assert guest.get(f"/api/guest/{token}").json()["project_id"] == project["id"]
        assert guest.post(
            f"/api/projects/{project['id']}/entries",
            headers={"x-guest-token": token},
            json={"kind": "update", "body": "Praca dodana z linku"},
        ).status_code == 201

    with TestClient(app) as independent:
        login(independent, "roles-independent@example.com")
        independent.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        own_project = independent.post(
            "/api/projects",
            json={"name": "Własne zlecenie", "template": "custom"},
        ).json()
        assert own_project["status"] == "assigned"
        assert independent.get("/api/workers").json() == []
        assert independent.post(
            "/api/workers", json={"label": "Nie powinno przejść"}
        ).status_code == 403
        assert independent.post(
            f"/api/projects/{own_project['id']}/guest-links",
            json={"label": "Podwykonawca", "kind": "worker"},
        ).status_code == 403

    with TestClient(app) as investor:
        login(investor, "roles-investor@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        worker = investor.post("/api/workers", json={"label": "Ekipa inwestora"})
        assert worker.status_code == 201
        project = investor.post(
            "/api/projects",
            json={
                "name": "Inwestycja z ekipą",
                "worker_profile_id": worker.json()["id"],
                "template": "custom",
            },
        ).json()
        assert project["status"] == "assigned"
        assert investor.get(f"/api/projects/{project['id']}").json()[
            "worker_profile"
        ]["label"] == "Ekipa inwestora"
        project_without_client = investor.post(
            "/api/projects",
            json={
                "name": "Inwestycja bez danych klienta",
                "client_name": None,
                "client_email": None,
                "template": "custom",
            },
        )
        assert project_without_client.status_code == 201
        assert project_without_client.json()["status"] == "assigned"
        assert project_without_client.json()["client_name"] == ""
        assert project_without_client.json()["client_email"] == ""


def test_public_contractor_name_uses_independent_profile_name_and_fallbacks():
    with TestClient(app) as independent:
        user = login(independent, "public-name-independent@example.com")
        independent.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        too_long = independent.patch(
            "/api/me",
            json={"public_profile_name": "x" * 121},
        )
        assert too_long.status_code == 422

        updated = independent.patch(
            "/api/me",
            json={
                "name": "Piotr Kowalski",
                "public_profile_name": "Remonty Kowalski",
                "phone": "+48 600 100 200",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["email"] == user["email"]
        assert updated.json()["public_profile_name"] == "Remonty Kowalski"

        project = independent.post(
            "/api/projects",
            json={"name": "Zlecenie publiczne", "template": "custom"},
        ).json()
        details = independent.get(f"/api/projects/{project['id']}").json()
        assert details["public_contractor_name"] == "Remonty Kowalski"
        token = independent.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        public_project = independent.get(f"/api/public/projects/{token}").json()
        assert public_project["project"]["public_contractor_name"] == "Remonty Kowalski"

        with SessionLocal() as db:
            item = db.get(models.Project, project["id"])
            assert reporting._project_worker_label(db, item) == "Remonty Kowalski"

    with TestClient(app) as other:
        login(other, "public-name-other@example.com")
        other.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        other.patch("/api/me", json={"public_profile_name": "Inna nazwa"})

    with TestClient(app) as fallback:
        login(fallback, "public-name-fallback@example.com")
        fallback.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        fallback.patch("/api/me", json={"name": "Jan Bez Profilu"})
        project = fallback.post(
            "/api/projects",
            json={"name": "Zlecenie fallback", "template": "custom"},
        ).json()
        token = fallback.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        public_project = fallback.get(f"/api/public/projects/{token}").json()
        assert public_project["project"]["public_contractor_name"] == "Jan Bez Profilu"


def test_public_contractor_name_uses_company_name_not_internal_worker_label():
    with TestClient(app) as owner:
        login(owner, "public-name-company@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma Publiczna XYZ",
            },
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Wewnętrzna Ekipa A",
                "profile_kind": "crew",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie firmowe publiczne",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        token = owner.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        public_project = owner.get(f"/api/public/projects/{token}").json()
        assert public_project["project"]["public_contractor_name"] == "Firma Publiczna XYZ"
        assert public_project["project"]["public_contractor_name"] != worker["label"]

        with SessionLocal() as db:
            item = db.get(models.Project, project["id"])
            assert reporting._project_worker_label(db, item) == "Firma Publiczna XYZ"


def test_public_contractor_name_for_investor_assigned_contractor():
    with TestClient(app) as investor:
        login(investor, "public-name-investor@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        worker = investor.post(
            "/api/workers",
            json={"label": "Wykonawca Zewnętrzny"},
        ).json()
        project = investor.post(
            "/api/projects",
            json={
                "name": "Inwestycja z publicznym wykonawcą",
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        token = investor.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]
        public_project = investor.get(f"/api/public/projects/{token}").json()
        assert public_project["project"]["public_contractor_name"] == "Wykonawca Zewnętrzny"


def test_company_worker_account_sees_project_assigned_at_creation():
    with TestClient(app) as worker_client:
        login(worker_client, "pracownik-firmy@example.com")

    with TestClient(app) as owner:
        login(owner, "owner-direct-worker@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma z pracownikiem",
            },
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik firmy",
                "email": "pracownik-firmy@example.com",
            },
        )
        assert worker.status_code == 201
        assert worker.json()["account_status"] == "active"
        unassigned = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie nieprzypisane",
                "workspace_id": workspace_id,
                "template": "custom",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie dla pracownika",
                "workspace_id": workspace_id,
                "worker_profile_id": worker.json()["id"],
                "template": "custom",
            },
        ).json()
        assert project["status"] == "assigned"

    with TestClient(app) as worker_client:
        user = login(worker_client, "pracownik-firmy@example.com")
        assert user["profile_type"] == "company_worker"
        projects = worker_client.get("/api/projects").json()
        assert [item["id"] for item in projects] == [project["id"]]
        assert unassigned["id"] not in [item["id"] for item in projects]
        assert projects[0]["role"] == "contributor"
        assert projects[0]["status"] == "assigned"
        progress = worker_client.post(
            f"/api/projects/{project['id']}/entries",
            json={
                "kind": "update",
                "body": "Postep od pracownika firmy",
                "stage_id": project["stages"][1]["id"],
            },
        )
        assert progress.status_code == 201
        assert progress.json()["stage"]["title"] == "W trakcie realizacji"
        assert worker_client.get(f"/api/projects/{project['id']}").json()["status"] == "in_progress"
        assert worker_client.post(
            f"/api/projects/{unassigned['id']}/entries",
            json={
                "kind": "update",
                "body": "Nieprzypisany etap",
                "stage_id": unassigned["stages"][1]["id"],
            },
        ).status_code == 403
        assert worker_client.get("/api/workers").json() == []
        assert worker_client.post(
            "/api/projects", json={"name": "Nie moje zlecenie"}
        ).status_code == 403
        assert worker_client.post(
            "/api/workspaces", json={"name": "Nie moja firma", "kind": "company"}
        ).status_code == 403
        assert worker_client.patch(
            f"/api/workspaces/{workspace_id}",
            json={"name": "Nie moge edytowac"},
        ).status_code == 403
        assert worker_client.post(
            f"/api/workspaces/{workspace_id}/invite",
            json={"email": "worker-invite-blocked@example.com", "role": "member"},
        ).status_code == 403
        assert worker_client.post(
            "/api/workers", json={"label": "Nie powinno przejsc"}
        ).status_code == 403


def test_company_worker_can_start_assigned_project_without_changing_stage():
    with TestClient(app) as owner:
        login(owner, "owner-worker-start@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma start worker",
            },
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik start",
                "email": "worker-start@example.com",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Start przypisanego zlecenia",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        unassigned = owner.post(
            "/api/projects",
            json={
                "name": "Start nieprzypisanego zlecenia",
                "workspace_id": workspace_id,
                "template": "custom",
            },
        ).json()
        completed = owner.post(
            "/api/projects",
            json={
                "name": "Start zakonczonego zlecenia",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        owner.post(f"/api/projects/{completed['id']}/close")

    with TestClient(app) as worker_client:
        login(worker_client, "worker-start@example.com")
        started = worker_client.post(f"/api/projects/{project['id']}/start")
        assert started.status_code == 200
        payload = started.json()
        assert payload["status"] == "in_progress"
        assert payload["started_at"]
        assert [stage["status"] for stage in payload["stages"]] == [
            "active",
            "planned",
            "planned",
        ]
        assert worker_client.post(f"/api/projects/{unassigned['id']}/start").status_code == 403
        assert worker_client.post(f"/api/projects/{completed['id']}/start").status_code == 400


def test_investor_can_assign_worker_after_project_creation():
    with TestClient(app) as investor:
        login(investor, "investor-assign-worker@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        project = investor.post(
            "/api/projects",
            json={"name": "Inwestycja do przypisania", "template": "custom"},
        ).json()
        worker = investor.post(
            "/api/workers", json={"label": "Wykonawca inwestora"}
        ).json()

        assigned = investor.patch(
            f"/api/projects/{project['id']}",
            json={"worker_profile_id": worker["id"]},
        )

        assert assigned.status_code == 200
        details = investor.get(f"/api/projects/{project['id']}").json()
        assert details["worker_profile_id"] == worker["id"]
        assert details["worker_profile"]["label"] == "Wykonawca inwestora"
        workers = investor.get("/api/workers").json()
        assigned_projects = [
            item
            for item in workers
            if item["id"] == worker["id"]
        ][0]["assigned_projects"]
        assert assigned_projects[0]["id"] == project["id"]


def test_set_current_stage_permissions_and_payload():
    with TestClient(app) as worker_client:
        login(worker_client, "stage-worker@example.com")

    with TestClient(app) as owner:
        login(owner, "stage-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma etapow",
            },
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik etapow",
                "email": "stage-worker@example.com",
            },
        ).json()
        unassigned = owner.post(
            "/api/projects",
            json={
                "name": "Etap nieprzypisany",
                "workspace_id": workspace_id,
                "template": "custom",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Etap przypisany",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        owner_stage = owner.post(
            f"/api/projects/{project['id']}/stages/{project['stages'][1]['id']}/set-current"
        )
        assert owner_stage.status_code == 200
        assert [stage["status"] for stage in owner_stage.json()["stages"]] == [
            "completed",
            "active",
            "planned",
        ]
        assert owner_stage.json()["status"] == "in_progress"
        fallback_stage = owner.post(
            f"/api/projects/{project['id']}/stages/{project['stages'][0]['id']}"
        )
        assert fallback_stage.status_code == 200
        assert fallback_stage.json()["stages"][0]["status"] == "active"

        link = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={
                "label": "Link stage",
                "kind": "worker",
                "permission": "add",
            },
        ).json()

    with TestClient(app) as investor:
        login(investor, "stage-investor@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        investor_project = investor.post(
            "/api/projects",
            json={"name": "Etap inwestora", "template": "custom"},
        ).json()
        response = investor.post(
            f"/api/projects/{investor_project['id']}/stages/{investor_project['stages'][1]['id']}/set-current"
        )
        assert response.status_code == 200
        assert response.json()["stages"][1]["status"] == "active"

    with TestClient(app) as independent:
        login(independent, "stage-independent@example.com")
        independent.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        own_project = independent.post(
            "/api/projects",
            json={"name": "Etap samodzielnego", "template": "custom"},
        ).json()
        response = independent.post(
            f"/api/projects/{own_project['id']}/stages/{own_project['stages'][2]['id']}/set-current"
        )
        assert response.status_code == 200
        assert response.json()["stages"][2]["status"] == "active"
        assert response.json()["status"] == "in_progress"

    with TestClient(app) as worker_client:
        login(worker_client, "stage-worker@example.com")
        response = worker_client.post(
            f"/api/projects/{project['id']}/stages/{project['stages'][2]['id']}/set-current"
        )
        assert response.status_code == 200
        assert response.json()["stages"][2]["status"] == "active"
        assert worker_client.post(
            f"/api/projects/{unassigned['id']}/stages/{unassigned['stages'][1]['id']}/set-current"
        ).status_code == 403

    with TestClient(app) as link_client:
        response = link_client.post(
            f"/api/projects/{project['id']}/stages/{project['stages'][1]['id']}/set-current",
            headers={"x-guest-token": link["token"]},
        )
        assert response.status_code == 200
        assert response.json()["stages"][1]["status"] == "active"
        assert link_client.post(
            f"/api/projects/{project['id']}/stages/{project['stages'][0]['id']}/set-current"
        ).status_code == 403


def test_project_close_and_reopen_permissions():
    def assert_final_stage_current(payload: dict) -> None:
        stages = payload["stages"]
        assert stages
        assert [stage["status"] for stage in stages[:-1]] == ["completed"] * (
            len(stages) - 1
        )
        assert stages[-1]["status"] == "active"

    with TestClient(app) as worker_client:
        login(worker_client, "close-worker@example.com")

    with TestClient(app) as owner:
        login(owner, "close-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma zamykania",
            },
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik do zamykania",
                "email": "close-worker@example.com",
            },
        ).json()
        unassigned = owner.post(
            "/api/projects",
            json={
                "name": "Nieprzypisane do zamykania",
                "workspace_id": workspace_id,
                "template": "custom",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie do zamkniecia",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
            },
        ).json()
        assert project["status"] == "assigned"

        closed = owner.post(f"/api/projects/{project['id']}/close")
        assert closed.status_code == 200
        closed_payload = closed.json()
        assert closed_payload["status"] == "completed"
        assert_final_stage_current(closed_payload)
        assert owner.post(f"/api/projects/{project['id']}/close").json()["status"] == "completed"

        reopened = owner.post(f"/api/projects/{project['id']}/reopen")
        assert reopened.status_code == 200
        reopened_payload = reopened.json()
        assert reopened_payload["status"] == "in_progress"
        assert [stage["status"] for stage in reopened_payload["stages"]] == [
            stage["status"] for stage in closed_payload["stages"]
        ]

        no_stage_project = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie bez etapow do zamkniecia",
                "workspace_id": workspace_id,
                "template": "custom",
            },
        ).json()
        with SessionLocal() as db:
            stages = db.scalars(
                select(models.ProjectStage).where(
                    models.ProjectStage.project_id == no_stage_project["id"]
                )
            ).all()
            for stage in stages:
                db.delete(stage)
            db.commit()
        no_stage_closed = owner.post(f"/api/projects/{no_stage_project['id']}/close")
        assert no_stage_closed.status_code == 200
        assert no_stage_closed.json()["status"] == "completed"
        assert no_stage_closed.json()["stages"] == []

        link = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={"label": "Linkowy", "kind": "worker", "permission": "history"},
        ).json()
        client_token = owner.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

    with TestClient(app) as worker_client:
        login(worker_client, "close-worker@example.com")
        worker_closed = worker_client.post(f"/api/projects/{project['id']}/close")
        assert worker_closed.status_code == 200
        assert worker_closed.json()["status"] == "completed"
        assert_final_stage_current(worker_closed.json())
        closed_stage_statuses = [stage["status"] for stage in worker_closed.json()["stages"]]
        worker_reopened = worker_client.post(f"/api/projects/{project['id']}/reopen")
        assert worker_reopened.status_code == 200
        assert worker_reopened.json()["status"] == "in_progress"
        assert worker_reopened.json()["finished_at"] is None
        assert [stage["status"] for stage in worker_reopened.json()["stages"]] == closed_stage_statuses
        assert worker_client.post(f"/api/projects/{unassigned['id']}/close").status_code == 403
        assert worker_client.post(f"/api/projects/{unassigned['id']}/reopen").status_code == 403
        assert worker_client.patch(
            f"/api/projects/{project['id']}",
            json={"status": "completed"},
        ).status_code == 403

    with TestClient(app) as guest:
        assert guest.post(
            f"/api/projects/{project['id']}/close",
            headers={"x-guest-token": link["token"]},
        ).status_code == 403
        assert guest.post(
            f"/api/projects/{project['id']}/reopen",
            headers={"x-guest-token": link["token"]},
        ).status_code == 403

    with TestClient(app) as worker_client:
        login(worker_client, "close-worker@example.com")
        worker_closed_again = worker_client.post(f"/api/projects/{project['id']}/close")
        assert worker_closed_again.status_code == 200
        assert worker_closed_again.json()["status"] == "completed"

    with TestClient(app) as public_client:
        assert public_client.get(f"/api/public/projects/{client_token}").json()[
            "project"
        ]["status"] == "completed"
        assert public_client.post(f"/api/projects/{project['id']}/close").status_code == 403
        assert public_client.post(f"/api/projects/{project['id']}/reopen").status_code == 403

    with TestClient(app) as investor:
        login(investor, "close-investor@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        investment = investor.post(
            "/api/projects",
            json={"name": "Inwestycja do zamkniecia", "template": "custom"},
        ).json()
        assert investor.post(f"/api/projects/{investment['id']}/close").json()[
            "status"
        ] == "completed"

    with TestClient(app) as independent:
        login(independent, "close-independent@example.com")
        independent.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        own_project = independent.post(
            "/api/projects",
            json={"name": "Wlasne do zamkniecia", "template": "custom"},
        ).json()
        assert independent.post(f"/api/projects/{own_project['id']}/close").json()[
            "status"
        ] == "completed"


def test_worker_with_email_requires_email_code_before_account_access():
    with TestClient(app) as owner:
        login(owner, "email-confirm-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma potwierdzeń",
            },
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Majster emailowy",
                "email": "majster-potwierdza@example.com",
            },
        )
        assert worker.status_code == 201
        worker_payload = worker.json()
        assert worker_payload["account_type"] == "account"
        assert "invitation_url" in worker_payload
        duplicate = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Majster emailowy drugi raz",
                "email": "majster-potwierdza@example.com",
            },
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["existing"] is True
        project = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie emailowe",
                "workspace_id": workspace_id,
                "worker_profile_id": worker_payload["id"],
                "template": "custom",
            },
        ).json()
        link = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={
                "worker_profile_id": worker_payload["id"],
                "kind": "worker",
                "permission": "history",
            },
        )
        assert link.status_code == 201
        assert link.json()["account_type"] == "account"

    with SessionLocal() as db:
        assert (
            db.scalar(
                select(models.User).where(
                    models.User.email == "majster-potwierdza@example.com"
                )
            )
            is None
        )
        assert db.scalar(
            select(models.Invitation).where(
                models.Invitation.email == "majster-potwierdza@example.com",
                models.Invitation.project_id == project["id"],
                models.Invitation.accepted_at.is_(None),
            )
        )

    with TestClient(app) as worker_client:
        worker_user = login(worker_client, "majster-potwierdza@example.com")
        assert worker_user["profile_type"] == "company_worker"
        projects = worker_client.get("/api/projects").json()
        assert [item["id"] for item in projects] == [project["id"]]
        assert projects[0]["role"] == "contributor"


def test_project_contract_terms_are_validated_visible_and_guarded():
    with TestClient(app) as owner:
        login(owner, "contract-owner@example.com")
        owner_user = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma 5D"},
        ).json()
        workspace_id = owner_user["workspaces"][0]["id"]
        worker = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik 5D",
                "email": "contract-worker@example.com",
            },
        ).json()
        created = owner.post(
            "/api/projects",
            json={
                "name": "Zlecenie z terminami",
                "workspace_id": workspace_id,
                "worker_profile_id": worker["id"],
                "template": "custom",
                "planned_start_date": "2026-06-20",
                "planned_end_date": "2026-06-30",
                "schedule_uncertainty_days": 3,
                "contract_amount": "12000",
            },
        )
        assert created.status_code == 201
        project = created.json()
        assert project["planned_start_date"] == "2026-06-20"
        assert project["planned_end_date"] == "2026-06-30"
        assert project["schedule_uncertainty_days"] == 3
        assert project["contract_amount"] == "12000.00"
        assert project["contract_currency"] == "PLN"
        listed = owner.get("/api/projects").json()[0]
        assert listed["planned_start_date"] == "2026-06-20"
        assert listed["contract_amount"] == "12000.00"
        details = owner.get(f"/api/projects/{project['id']}").json()
        assert details["planned_end_date"] == "2026-06-30"
        assert details["contract_currency"] == "PLN"

        patched = owner.patch(
            f"/api/projects/{project['id']}",
            json={
                "planned_end_date": "2026-07-02",
                "contract_amount": "13000.75",
            },
        )
        assert patched.status_code == 200
        assert patched.json()["planned_end_date"] == "2026-07-02"
        assert patched.json()["contract_amount"] == "13000.75"
        assert patched.json()["contract_currency"] == "PLN"

        invalid_dates = owner.post(
            "/api/projects",
            json={
                "name": "Zly termin",
                "template": "custom",
                "planned_start_date": "2026-07-10",
                "planned_end_date": "2026-07-09",
            },
        )
        assert invalid_dates.status_code == 400
        assert "Planowany koniec" in invalid_dates.json()["detail"]
        invalid_uncertainty = owner.post(
            "/api/projects",
            json={
                "name": "Zla niepewnosc",
                "template": "custom",
                "schedule_uncertainty_days": -1,
            },
        )
        assert invalid_uncertainty.status_code == 400
        invalid_amount = owner.post(
            "/api/projects",
            json={
                "name": "Zla kwota",
                "template": "custom",
                "contract_amount": "-1.00",
            },
        )
        assert invalid_amount.status_code == 400
        invalid_currency = owner.post(
            "/api/projects",
            json={
                "name": "Zla waluta",
                "template": "custom",
                "contract_amount": "10.00",
                "contract_currency": "PLNN",
            },
        )
        assert invalid_currency.status_code == 400

        guest_link = owner.post(
            f"/api/projects/{project['id']}/guest-links",
            json={"label": "Link 5D", "kind": "worker", "permission": "history"},
        ).json()
        client_token = owner.get(f"/api/projects/{project['id']}/client-link").json()[
            "url"
        ].rsplit("/", 1)[-1]

    with TestClient(app) as public_client:
        public_project = public_client.get(
            f"/api/public/projects/{client_token}"
        ).json()["project"]
        assert public_project["planned_start_date"] == "2026-06-20"
        assert public_project["planned_end_date"] == "2026-07-02"
        assert public_project["schedule_uncertainty_days"] == 3
        assert public_project["contract_amount"] == "13000.75"
        assert public_client.patch(
            f"/api/projects/{project['id']}",
            json={"contract_amount": "1.00"},
        ).status_code == 403

    with TestClient(app) as guest:
        guest_project = guest.get(
            f"/api/projects/{project['id']}",
            headers={"x-guest-token": guest_link["token"]},
        ).json()
        assert guest_project["contract_amount"] == "13000.75"
        assert guest.patch(
            f"/api/projects/{project['id']}",
            headers={"x-guest-token": guest_link["token"]},
            json={"contract_amount": "1.00"},
        ).status_code == 403

    with TestClient(app) as worker_client:
        worker_user = login(worker_client, "contract-worker@example.com")
        assert worker_user["profile_type"] == "company_worker"
        worker_project = worker_client.get(f"/api/projects/{project['id']}").json()
        assert worker_project["planned_end_date"] == "2026-07-02"
        assert worker_client.patch(
            f"/api/projects/{project['id']}",
            json={"contract_amount": "1.00"},
        ).status_code == 403


def test_frontend_project_forms_send_contract_terms_without_currency_field():
    app_source = Path("frontend/src/App.tsx").read_text(encoding="utf-8")
    manage_source = Path("frontend/src/ManageProjectModal.tsx").read_text(encoding="utf-8")
    create_block = app_source[
        app_source.index("function CreateProjectModal") : app_source.index("function Dashboard")
    ]
    manage_block = manage_source[
        manage_source.index("export function ManageProjectModal") :
    ]

    assert 'planned_start_date: formNullableString(data, "planned_start_date")' in create_block
    assert 'planned_end_date: formNullableString(data, "planned_end_date")' in create_block
    assert (
        'schedule_uncertainty_days: formOptionalNumber(data, "schedule_uncertainty_days")'
        in create_block
    )
    assert 'contract_amount: formMoneyString(data, "contract_amount")' in create_block

    assert "canEditContractTerms" in manage_block
    assert 'payload.planned_start_date = formNullableString(data, "planned_start_date")' in manage_block
    assert 'payload.planned_end_date = formNullableString(data, "planned_end_date")' in manage_block
    assert (
        'payload.schedule_uncertainty_days = formOptionalNumber(data, "schedule_uncertainty_days")'
        in manage_block
    )
    assert 'payload.contract_amount = formMoneyString(data, "contract_amount")' in manage_block
    assert "contractTermsReadonlyMessage" in manage_block
    assert "Dane do podgladu - zmienia je szef firmy." in manage_source

    for block in (create_block, manage_block):
        assert 'name="contract_amount"' in block
        assert "Kwota umowna (PLN)" in block
        assert 'name="contract_currency"' not in block
        assert "Waluta" not in block


def test_demo_seed_reset_creates_realistic_demo_data():
    with SessionLocal() as db:
        db.add(
            models.User(
                email="old-demo-noise@example.com",
                name="Nie demo",
                profile_type="investor",
            )
        )
        result = seed_demo_data(db, reset=True, yes=True)

        demo_users = db.scalars(
            select(models.User).where(
                models.User.email.in_(
                    [
                        "szef@majster.pl",
                        "inwestor@majster.pl",
                        "samodzielny@majster.pl",
                        "pracownik@majster.pl",
                        "pracownik2@majster.pl",
                    ]
                )
            )
        ).all()
        assert {user.email: user.profile_type for user in demo_users} == {
            "szef@majster.pl": "company_owner",
            "inwestor@majster.pl": "investor",
            "samodzielny@majster.pl": "independent_contractor",
            "pracownik@majster.pl": "company_worker",
            "pracownik2@majster.pl": "company_worker",
        }
        assert all(user.password_hash for user in demo_users)
        assert result.company_statuses["assigned"] == 1
        assert result.company_statuses["in_progress"] == 2
        assert result.company_statuses["completed"] == 2
        assert result.independent_statuses["assigned"] == 1
        assert result.independent_statuses["in_progress"] == 1
        assert result.independent_statuses["completed"] == 2
        assert result.investor_statuses["assigned"] == 1
        assert result.investor_statuses["in_progress"] == 2
        assert result.investor_statuses["completed"] == 1
        company = db.scalar(
            select(models.Workspace).where(
                models.Workspace.name == "MajsterPro Warszawa"
            )
        )
        assert company is not None
        company_workers = db.scalars(
            select(models.WorkerProfile).where(
                models.WorkerProfile.workspace_id == company.id
            )
        ).all()
        assert len(company_workers) == 6
        assert any(
            worker.label == "Malarz Nieaktywny Demo" and not worker.active
            for worker in company_workers
        )
        assert {worker.label for worker in company_workers} >= {
            "Paweł Glazurnik",
            "Marek Hydraulik",
            "Ekipa Glazurnicza Link",
            "Elektryk Link",
            "Hydraulik Link",
        }
        investor_space = db.scalar(
            select(models.Workspace).where(
                models.Workspace.name == "Wykonawcy Inwestora Demo"
            )
        )
        assert investor_space is not None
        assert (
            db.scalar(
                select(func.count(models.WorkerProfile.id)).where(
                models.WorkerProfile.workspace_id == investor_space.id
            )
        )
            == 4
        )
        assert result.guest_links >= 3
        assert result.client_links >= 13
        assert result.counts["media_assets"] >= 20
        assert db.scalar(
            select(models.User).where(models.User.email == "old-demo-noise@example.com")
        )
        assert db.scalar(
            select(models.Project).where(models.Project.name == "Remont łazienki — Mokotów")
        )
        assert db.scalar(
            select(models.Project).where(models.Project.name == "Awaria instalacji wodnej")
        )
        public_project = db.scalar(
            select(models.Project).where(models.Project.name == "Remont mieszkania pod wynajem")
        )
        assert public_project and public_project.client_share_token
        public_token = public_project.client_share_token

    with TestClient(app) as client:
        assert client.get(f"/api/public/projects/{public_token}").status_code == 200


def test_demo_admin_reset_endpoint_is_flagged_guarded_and_idempotent(monkeypatch):
    monkeypatch.setattr(api_module.settings, "demo_admin_enabled", False)
    monkeypatch.setattr(api_module.settings, "demo_admin_user", "Piotrek")
    monkeypatch.setattr(api_module.settings, "demo_admin_password", "Secret123")
    monkeypatch.setattr(api_module.settings, "allow_demo_reset", False)
    with TestClient(app) as client:
        disabled = client.post(
            "/api/demo-admin/login",
            json={"username": "Piotrek", "password": "Secret123"},
        )
        assert disabled.status_code == 403

        monkeypatch.setattr(api_module.settings, "demo_admin_enabled", True)
        assert (
            client.post(
                "/api/demo-admin/login",
                json={"username": "Piotrek", "password": "wrong"},
            ).status_code
            == 403
        )

        login_response = client.post(
            "/api/demo-admin/login",
            json={"username": "Piotrek", "password": "Secret123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["token"]
        assert login_response.json()["demo_accounts"]

        blocked = client.post(
            "/api/demo-admin/reset",
            headers={"Authorization": f"Bearer {token}"},
            json={"confirmation": "RESET DEMO"},
        )
        assert blocked.status_code == 403

        monkeypatch.setattr(api_module.settings, "allow_demo_reset", True)
        typo = client.post(
            "/api/demo-admin/reset",
            headers={"Authorization": f"Bearer {token}"},
            json={"confirmation": "reset demo"},
        )
        assert typo.status_code == 400

        with SessionLocal() as db:
            sentinel = models.User(
                email="demo-admin-sentinel@example.com",
                name="Sentinel",
                profile_type="investor",
            )
            db.add(sentinel)
            db.commit()

        first = client.post(
            "/api/demo-admin/reset",
            headers={"Authorization": f"Bearer {token}"},
            json={"confirmation": "RESET DEMO"},
        )
        assert first.status_code == 200
        second = client.post(
            "/api/demo-admin/reset",
            headers={"Authorization": f"Bearer {token}"},
            json={"confirmation": "RESET DEMO"},
        )
        assert second.status_code == 200
        first_json = first.json()
        second_json = second.json()
        assert first_json["counts"]["projects"] == second_json["counts"]["projects"]
        assert first_json["counts"]["entries"] == second_json["counts"]["entries"]
        assert first_json["guest_links"] >= 3
        assert first_json["client_links"] >= 13
        assert first_json["diagnostics"]["database_fingerprint"].startswith("db_")
        assert "DATABASE_URL" not in first_json["diagnostics"]["database_fingerprint"]
        assert first_json["diagnostics"]["demo_users_found"] == 5
        assert first_json["diagnostics"]["projects_visible_by_user"]["samodzielny@majster.pl"] >= 4
        assert first_json["diagnostics"]["projects_visible_by_user"]["szef@majster.pl"] >= 5
        assert first_json["diagnostics"]["projects_visible_by_user"]["inwestor@majster.pl"] >= 4
        assert first_json["diagnostics"]["projects_visible_by_user"]["pracownik@majster.pl"] >= 1
        assert first_json["diagnostics"]["projects_visible_by_user"]["pracownik2@majster.pl"] >= 1

        status = client.get(
            "/api/demo-admin/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status.status_code == 200
        status_json = status.json()
        assert status_json["diagnostics"]["database_fingerprint"] == first_json["diagnostics"]["database_fingerprint"]
        assert status_json["diagnostics"]["projects_visible_by_user"] == second_json["diagnostics"]["projects_visible_by_user"]
        assert status_json["diagnostics"]["entries_visible_by_user"] == second_json["diagnostics"]["entries_visible_by_user"]
        assert status_json["diagnostics"]["client_links"] == second_json["diagnostics"]["client_links"]
        assert status_json["diagnostics"]["guest_links"] == second_json["diagnostics"]["guest_links"]

        expected_visible_counts = {
            "samodzielny@majster.pl": 4,
            "szef@majster.pl": 5,
            "inwestor@majster.pl": 4,
            "pracownik@majster.pl": 1,
            "pracownik2@majster.pl": 1,
        }
        for email, minimum in expected_visible_counts.items():
            password_login(client, email, "test1234")
            projects = client.get("/api/projects")
            assert projects.status_code == 200
            assert len(projects.json()) >= minimum

    with SessionLocal() as db:
        for email in {
            "szef@majster.pl",
            "inwestor@majster.pl",
            "samodzielny@majster.pl",
            "pracownik@majster.pl",
            "pracownik2@majster.pl",
        }:
            assert (
                db.scalar(
                    select(func.count(models.User.id)).where(models.User.email == email)
                )
                == 1
            )
        assert db.scalar(
            select(models.User).where(models.User.email == "demo-admin-sentinel@example.com")
        )


def test_demo_seed_reset_requires_yes_confirmation():
    with SessionLocal() as db:
        sentinel = models.User(
            email="demo-reset-sentinel@example.com",
            name="Sentinel",
            profile_type="investor",
        )
        db.add(sentinel)
        db.commit()

        with pytest.raises(RuntimeError, match="--yes"):
            seed_demo_data(db, reset=True, yes=False)

        assert db.scalar(
            select(models.User).where(
                models.User.email == "demo-reset-sentinel@example.com"
            )
        )


def test_public_profile_realizations_are_backend_backed_and_public_visibility():
    with TestClient(app) as client:
        login(client, "portfolio-independent@example.com")
        client.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        project = client.post(
            "/api/projects",
            json={
                "name": "Portfolio bathroom",
                "client_name": "Anna",
                "address": "Warszawa",
                "template": "remont",
            },
        ).json()
        client.post(f"/api/projects/{project['id']}/close")

        profile_response = client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": True,
                "slug": "portfolio-test-profile",
                "display_name": "Portfolio Test",
                "contact_email": "kontakt@example.com",
                "specializations": ["remont-lazienki"],
            },
        )
        assert profile_response.status_code == 200
        assert profile_response.json()["contact_email"] == "kontakt@example.com"
        assert client.get("/api/me").json()["email"] == "portfolio-independent@example.com"

        created = client.post(
            "/api/public-profile/me/realizations?owner_type=independent_contractor",
            json={
                "project_id": project["id"],
                "title": "Lazienka pokazowa",
                "public_description": "Opis publiczny",
                "location_public": "Warszawa",
                "work_scope": ["Remont lazienki"],
                "completion_date": "2026-07-03",
                "amount": "1200.00",
                "show_amount": False,
                "status": "draft",
                "cover_image_url": "https://example.test/cover.jpg",
                "gallery_image_urls": ["https://example.test/one.jpg"],
            },
        )
        assert created.status_code == 201
        assert created.json()["status"] == "draft"

        public_profile = client.get("/api/public-profiles/portfolio-test-profile")
        assert public_profile.status_code == 200
        assert public_profile.json()["realizations"] == []

        item_id = created.json()["id"]
        published = client.patch(
            f"/api/public-profile/me/realizations/{item_id}?owner_type=independent_contractor",
            json={"status": "published"},
        )
        assert published.status_code == 200
        assert published.json()["published_at"]

        public_profile = client.get("/api/public-profiles/portfolio-test-profile")
        assert public_profile.status_code == 200
        realizations = public_profile.json()["realizations"]
        assert len(realizations) == 1
        assert realizations[0]["title"] == "Lazienka pokazowa"
        assert realizations[0]["amount"] is None


def test_public_profiles_list_returns_visible_contractor_profiles_with_filters():
    with TestClient(app) as client:
        login(client, "discovery-independent@example.com")
        client.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        project = client.post(
            "/api/projects",
            json={
                "name": "Discovery bathroom",
                "client_name": "Anna",
                "address": "Warszawa",
                "template": "remont",
            },
        ).json()
        client.post(f"/api/projects/{project['id']}/close")
        profile_response = client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": True,
                "slug": "alpha-discovery-profile",
                "display_name": "Alpha Discovery",
                "public_description": "Glazura i remonty lazienek",
                "specializations": ["remont-lazienki", "glazura"],
                "service_area": "Warszawa i Piaseczno",
            },
        )
        assert profile_response.status_code == 200
        published = client.post(
            "/api/public-profile/me/realizations?owner_type=independent_contractor",
            json={
                "project_id": project["id"],
                "title": "Publiczna realizacja",
                "public_description": "Opis publiczny",
                "location_public": "Warszawa",
                "work_scope": ["Glazura"],
                "completion_date": "2026-07-03",
                "status": "published",
            },
        )
        assert published.status_code == 201
        draft = client.post(
            "/api/public-profile/me/realizations?owner_type=independent_contractor",
            json={
                "project_id": project["id"],
                "title": "Szkic realizacji",
                "public_description": "Niepubliczny opis",
                "location_public": "Warszawa",
                "work_scope": ["Glazura"],
                "completion_date": "2026-07-03",
                "status": "draft",
            },
        )
        assert draft.status_code == 201

        login(client, "discovery-private@example.com")
        client.post(
            "/api/onboarding",
            json={"profile_type": "independent_contractor"},
        )
        private_profile = client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": False,
                "slug": "hidden-discovery-profile",
                "display_name": "Hidden Discovery",
                "public_description": "Ukryty profil glazura",
                "specializations": ["glazura"],
                "service_area": "Warszawa",
            },
        )
        assert private_profile.status_code == 200

        login(client, "discovery-company@example.com")
        company = client.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Beta Discovery",
            },
        )
        assert company.status_code == 200
        company_profile = client.patch(
            "/api/public-profile/me?owner_type=company",
            json={
                "is_public": True,
                "slug": "beta-discovery-profile",
                "display_name": "Beta Discovery",
                "public_description": "Firma od instalacji i elektryki",
                "specializations": ["elektryka"],
                "service_area": "Gdansk i okolice",
            },
        )
        assert company_profile.status_code == 200

        response = client.get("/api/public-profiles")
        assert response.status_code == 200
        profiles = response.json()
        names = {item["display_name"] for item in profiles}
        assert "Alpha Discovery" in names
        assert "Beta Discovery" in names
        assert "Hidden Discovery" not in names
        alpha = next(item for item in profiles if item["slug"] == "alpha-discovery-profile")
        assert alpha["owner_type"] == "independent_contractor"
        assert len(alpha["realizations"]) == 1
        assert alpha["realizations"][0]["title"] == "Publiczna realizacja"

        query_response = client.get("/api/public-profiles?q=glazura")
        assert query_response.status_code == 200
        query_slugs = {item["slug"] for item in query_response.json()}
        assert "alpha-discovery-profile" in query_slugs
        assert "hidden-discovery-profile" not in query_slugs

        specialization_response = client.get(
            "/api/public-profiles?specialization=elektryka"
        )
        assert specialization_response.status_code == 200
        specialization_slugs = {item["slug"] for item in specialization_response.json()}
        assert "beta-discovery-profile" in specialization_slugs
        assert "alpha-discovery-profile" not in specialization_slugs
        assert "hidden-discovery-profile" not in specialization_slugs

        area_response = client.get("/api/public-profiles?service_area=piaseczno")
        assert area_response.status_code == 200
        area_slugs = {item["slug"] for item in area_response.json()}
        assert "alpha-discovery-profile" in area_slugs
        assert "beta-discovery-profile" not in area_slugs
        assert "hidden-discovery-profile" not in area_slugs


def test_company_owner_can_manage_company_public_profile_realizations():
    with TestClient(app) as client:
        user = login(client, "company-profile-owner@example.com")
        onboarded = client.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma Profil",
            },
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]
        assert user["email"] == "company-profile-owner@example.com"

        project = client.post(
            "/api/projects",
            json={
                "name": "Firmowa realizacja",
                "client_name": "Klient",
                "address": "Krakow",
                "template": "remont",
                "workspace_id": workspace_id,
            },
        ).json()
        client.post(f"/api/projects/{project['id']}/close")

        profile_response = client.patch(
            "/api/public-profile/me?owner_type=company",
            json={
                "is_public": True,
                "slug": "firma-profile-test",
                "display_name": "Firma Profil",
            },
        )
        assert profile_response.status_code == 200

        created = client.post(
            "/api/public-profile/me/realizations?owner_type=company",
            json={
                "project_id": project["id"],
                "title": "Firmowa realizacja",
                "public_description": "Opis",
                "location_public": "Krakow",
                "work_scope": ["Remont mieszkan"],
                "completion_date": "2026-07-03",
                "amount": "5000.00",
                "show_amount": True,
                "status": "published",
            },
        )
        assert created.status_code == 201

        public_profile = client.get("/api/public-profiles/firma-profile-test")
        assert public_profile.status_code == 200
        body = public_profile.json()
        assert body["owner_type"] == "company"
        assert len(body["realizations"]) == 1
        assert body["realizations"][0]["amount"] == "5000.00"


def test_investor_cannot_manage_public_profile_realizations():
    with TestClient(app) as client:
        login(client, "portfolio-investor@example.com")
        client.post("/api/onboarding", json={"profile_type": "investor"})

        independent = client.get(
            "/api/public-profile/me/realizations?owner_type=independent_contractor"
        )
        company = client.get("/api/public-profile/me/realizations?owner_type=company")

        assert independent.status_code == 403
        assert company.status_code == 403


def test_investor_job_posting_draft_publish_and_public_visibility():
    with TestClient(app) as client:
        login(client, "job-posting-investor@example.com")
        client.post("/api/onboarding", json={"profile_type": "investor"})

        draft = client.post(
            "/api/job-postings/me",
            json={
                "title": "Remont kuchni w mieszkaniu",
                "description": "Potrzebna wymiana mebli i instalacji.",
                "location": "Warszawa Mokotow",
                "budget_label": "15 000 - 30 000 zl",
                "deadline": "sierpien 2026",
                "specializations": ["montaz-kuchni", "elektryka"],
                "current_state_description": "Stare meble sa juz zdemontowane.",
                "target_contractor_type": "company",
                "status": "draft",
            },
        )
        assert draft.status_code == 201
        body = draft.json()
        assert body["status"] == "draft"
        assert body["published_at"] is None
        assert body["specializations"] == ["montaz-kuchni", "elektryka"]

        my_postings = client.get("/api/job-postings/me")
        assert my_postings.status_code == 200
        assert [item["id"] for item in my_postings.json()] == [body["id"]]

        login(client, "job-posting-contractor@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        public_before = client.get("/api/job-postings/public")
        assert public_before.status_code == 200
        assert body["id"] not in {item["id"] for item in public_before.json()}

        blocked_create = client.post(
            "/api/job-postings/me",
            json={
                "title": "Nie moje ogloszenie",
                "location": "Krakow",
            },
        )
        assert blocked_create.status_code == 403

        login(client, "job-posting-investor@example.com")
        published = client.patch(
            f"/api/job-postings/me/{body['id']}",
            json={"status": "published"},
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"
        assert published.json()["published_at"]

        login(client, "job-posting-company-owner@example.com")
        client.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma od ogloszen",
            },
        )
        public_for_company = client.get("/api/job-postings/public")
        assert public_for_company.status_code == 200
        public_items = public_for_company.json()
        posted = next(item for item in public_items if item["id"] == body["id"])
        assert posted["title"] == "Remont kuchni w mieszkaniu"
        assert "investor_id" not in posted

        login(client, "job-posting-second-contractor@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        public_for_independent = client.get("/api/job-postings/public")
        assert public_for_independent.status_code == 200
        assert body["id"] in {item["id"] for item in public_for_independent.json()}


def create_job_posting(
    client: TestClient,
    email: str,
    *,
    title: str,
    status: str = "published",
) -> dict:
    login(client, email)
    client.post("/api/onboarding", json={"profile_type": "investor"})
    response = client.post(
        "/api/job-postings/me",
        json={
            "title": title,
            "description": "Opis publicznego ogloszenia testowego.",
            "location": "Warszawa",
            "budget_label": "10 000 - 15 000 zl",
            "deadline": "lipiec 2026",
            "specializations": ["remont-lazienki", "hydraulika"],
            "current_state_description": "Stan obecny do oceny na miejscu.",
            "target_contractor_type": "any",
            "status": status,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_job_posting_interest_flow_shares_public_profile_contact_only():
    with TestClient(app) as client:
        posting = create_job_posting(
            client,
            "interest-investor@example.com",
            title="Zlecenie z zainteresowaniami",
        )
        draft = create_job_posting(
            client,
            "interest-draft-investor@example.com",
            title="Szkic bez zainteresowan",
            status="draft",
        )

        login(client, "interest-contractor-login@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})

        blocked_profile = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Chce poznac szczegoly."},
        )
        assert blocked_profile.status_code == 422
        assert "publiczna wizytowke" in blocked_profile.json()["detail"]

        no_contact_profile = client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": True,
                "slug": "interest-independent-no-contact",
                "display_name": "Majster Bez Kontaktu",
                "specializations": ["remont-lazienki"],
                "contact_phone": "",
                "contact_email": "",
            },
        )
        assert no_contact_profile.status_code == 200
        blocked_contact = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Mam termin."},
        )
        assert blocked_contact.status_code == 422
        assert "telefon lub e-mail" in blocked_contact.json()["detail"]

        ready_profile = client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": True,
                "slug": "interest-independent-ready",
                "display_name": "Majster Kontaktowy",
                "public_description": "Remonty lazienek i hydraulika.",
                "specializations": ["remont-lazienki", "hydraulika"],
                "service_area": "Warszawa i okolice",
                "contact_phone": "500 100 200",
                "contact_email": "public-contact@example.com",
            },
        )
        assert ready_profile.status_code == 200
        context = client.get("/api/job-posting-interests/me/context")
        assert context.status_code == 200
        assert context.json()["can_submit"] is True

        blocked_draft = client.post(
            f"/api/job-postings/public/{draft['id']}/interest",
            json={"message": "Szkic tez widze?"},
        )
        assert blocked_draft.status_code == 422

        created = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Dzien dobry, jestem zainteresowany realizacja."},
        )
        assert created.status_code == 201
        interest = created.json()
        assert interest["status"] == "new"
        assert interest["contractor_owner_type"] == "independent_contractor"

        duplicate = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Drugie zgloszenie."},
        )
        assert duplicate.status_code == 409

        public_list = client.get("/api/job-postings/public")
        assert public_list.status_code == 200
        public_posting = next(item for item in public_list.json() if item["id"] == posting["id"])
        assert public_posting["my_interest"]["id"] == interest["id"]

        login(client, "interest-investor@example.com")
        my_postings = client.get("/api/job-postings/me")
        assert my_postings.status_code == 200
        investor_posting = next(item for item in my_postings.json() if item["id"] == posting["id"])
        assert investor_posting["interest_count"] == 1
        contractor = investor_posting["interests"][0]["contractor"]
        assert contractor["display_name"] == "Majster Kontaktowy"
        assert contractor["contact_phone"] == "500 100 200"
        assert contractor["contact_email"] == "public-contact@example.com"
        assert contractor["contact_email"] != "interest-contractor-login@example.com"
        assert contractor["slug"] == "interest-independent-ready"

        all_interests = client.get("/api/job-postings/me/interests")
        assert all_interests.status_code == 200
        assert [item["id"] for item in all_interests.json()] == [interest["id"]]

        updated = client.patch(
            f"/api/job-postings/me/interests/{interest['id']}",
            json={"status": "contact"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "contact"

        login(client, "interest-other-investor@example.com")
        client.post("/api/onboarding", json={"profile_type": "investor"})
        other_interests = client.get("/api/job-postings/me/interests")
        assert other_interests.status_code == 200
        assert other_interests.json() == []


def test_company_owner_can_interest_job_posting_as_company():
    with TestClient(app) as client:
        posting = create_job_posting(
            client,
            "company-interest-investor@example.com",
            title="Zlecenie dla firmy",
        )

        login(client, "company-interest-owner@example.com")
        client.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma Zainteresowana",
            },
        )
        profile = client.patch(
            "/api/public-profile/me?owner_type=company",
            json={
                "is_public": True,
                "slug": "firma-zainteresowana",
                "display_name": "Firma Zainteresowana",
                "specializations": ["hydraulika"],
                "service_area": "Mazowsze",
                "contact_phone": "600 200 300",
                "contact_email": "firma-public@example.com",
            },
        )
        assert profile.status_code == 200

        created = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Firma jest zainteresowana ogledzinami."},
        )
        assert created.status_code == 201
        interest = created.json()
        assert interest["contractor_owner_type"] == "company"

        login(client, "company-interest-investor@example.com")
        all_interests = client.get("/api/job-postings/me/interests")
        assert all_interests.status_code == 200
        body = all_interests.json()
        assert len(body) == 1
        assert body[0]["contractor"]["owner_type"] == "company"
        assert body[0]["contractor"]["contact_email"] == "firma-public@example.com"
        assert body[0]["contractor"]["contact_email"] != "company-interest-owner@example.com"


def test_investor_and_company_worker_cannot_interest_job_postings():
    with TestClient(app) as client:
        posting = create_job_posting(
            client,
            "blocked-role-investor@example.com",
            title="Zlecenie dla wykonawcy",
        )

        blocked_investor = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Nie jestem wykonawca."},
        )
        assert blocked_investor.status_code == 403

        login(client, "blocked-role-worker@example.com")
        client.post("/api/onboarding", json={"profile_type": "company_worker"})
        blocked_worker = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Pracownik nie zglasza."},
        )
        assert blocked_worker.status_code == 403


def test_job_posting_offer_flow_requires_interest_and_does_not_create_project():
    with TestClient(app) as client:
        posting = create_job_posting(
            client,
            "offer-investor@example.com",
            title="Zlecenie pod oferte",
        )
        draft = create_job_posting(
            client,
            "offer-draft-investor@example.com",
            title="Szkic bez ofert",
            status="draft",
        )

        login(client, "offer-contractor@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        profile = client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": True,
                "slug": "offer-independent-ready",
                "display_name": "Majster Ofertowy",
                "public_description": "Wyceny i remonty lazienek.",
                "specializations": ["remont-lazienki", "hydraulika"],
                "service_area": "Warszawa",
                "contact_phone": "501 111 222",
                "contact_email": "offer-public@example.com",
            },
        )
        assert profile.status_code == 200

        blocked_without_interest = client.post(
            f"/api/job-postings/public/{posting['id']}/offer",
            json={
                "title": "Oferta przed zainteresowaniem",
                "scope_summary": "Zakres",
                "status": "draft",
            },
        )
        assert blocked_without_interest.status_code == 422
        assert "zglos zainteresowanie" in blocked_without_interest.json()["detail"]

        blocked_draft_posting = client.post(
            f"/api/job-postings/public/{draft['id']}/offer",
            json={
                "title": "Oferta do szkicu",
                "scope_summary": "Zakres",
                "status": "draft",
            },
        )
        assert blocked_draft_posting.status_code == 422

        interest_response = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Chce przygotowac wstepna wycene."},
        )
        assert interest_response.status_code == 201
        interest = interest_response.json()

        draft_offer = client.post(
            f"/api/job-postings/public/{posting['id']}/offer",
            json={
                "title": "Oferta wstepna na remont",
                "scope_summary": "",
                "assumptions": "Po ogledzinach mozliwa korekta.",
                "estimated_price": "12500.00",
                "price_note": "Kwota orientacyjna brutto.",
                "planned_start": "sierpien 2026",
                "planned_end": "2 tygodnie",
                "status": "draft",
            },
        )
        assert draft_offer.status_code == 201
        offer = draft_offer.json()
        assert offer["status"] == "draft"
        assert offer["interest_id"] == interest["id"]
        assert offer["contractor_owner_type"] == "independent_contractor"
        assert offer["estimated_price"] == "12500.00"

        duplicate = client.post(
            f"/api/job-postings/public/{posting['id']}/offer",
            json={
                "title": "Druga oferta",
                "scope_summary": "Duplikat",
                "status": "draft",
            },
        )
        assert duplicate.status_code == 409

        public_list = client.get("/api/job-postings/public")
        assert public_list.status_code == 200
        public_posting = next(item for item in public_list.json() if item["id"] == posting["id"])
        assert public_posting["my_offer"]["id"] == offer["id"]
        assert public_posting["my_offer"]["status"] == "draft"

        login(client, "offer-investor@example.com")
        investor_postings_before_send = client.get("/api/job-postings/me")
        assert investor_postings_before_send.status_code == 200
        investor_posting_before_send = next(
            item for item in investor_postings_before_send.json() if item["id"] == posting["id"]
        )
        assert investor_posting_before_send["offer_count"] == 0
        assert investor_posting_before_send["offers"] == []

        login(client, "offer-contractor@example.com")
        sent = client.patch(
            f"/api/job-posting-offers/me/{offer['id']}",
            json={
                "scope_summary": "Demontaz, hydraulika, plytki i bialy montaz.",
                "status": "sent",
            },
        )
        assert sent.status_code == 200
        sent_offer = sent.json()
        assert sent_offer["status"] == "sent"
        assert sent_offer["sent_at"]

        blocked_edit_sent = client.patch(
            f"/api/job-posting-offers/me/{offer['id']}",
            json={"title": "Zmiana po wyslaniu"},
        )
        assert blocked_edit_sent.status_code == 422

        my_offers = client.get("/api/job-posting-offers/me")
        assert my_offers.status_code == 200
        assert my_offers.json()[0]["job_posting"]["id"] == posting["id"]

        login(client, "offer-investor@example.com")
        my_postings = client.get("/api/job-postings/me")
        assert my_postings.status_code == 200
        investor_posting = next(item for item in my_postings.json() if item["id"] == posting["id"])
        assert investor_posting["offer_count"] == 1
        contractor = investor_posting["offers"][0]["contractor"]
        assert contractor["display_name"] == "Majster Ofertowy"
        assert contractor["contact_email"] == "offer-public@example.com"
        assert contractor["contact_email"] != "offer-contractor@example.com"

        investor_offers = client.get("/api/job-postings/me/offers")
        assert investor_offers.status_code == 200
        assert [item["id"] for item in investor_offers.json()] == [offer["id"]]

        login(client, "offer-other-investor@example.com")
        client.post("/api/onboarding", json={"profile_type": "investor"})
        other_offers = client.get("/api/job-postings/me/offers")
        assert other_offers.status_code == 200
        assert other_offers.json() == []

        with SessionLocal() as db:
            project_count_before = db.scalar(select(func.count(models.Project.id)))

        login(client, "offer-investor@example.com")
        accepted = client.patch(
            f"/api/job-postings/me/offers/{offer['id']}",
            json={"status": "accepted"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["accepted_at"]

        with SessionLocal() as db:
            project_count_after = db.scalar(select(func.count(models.Project.id)))
        assert project_count_after == project_count_before


def test_company_owner_can_send_and_investor_can_reject_job_posting_offer():
    with TestClient(app) as client:
        posting = create_job_posting(
            client,
            "company-offer-investor@example.com",
            title="Zlecenie pod oferte firmy",
        )

        login(client, "company-offer-owner@example.com")
        client.post(
            "/api/onboarding",
            json={
                "profile_type": "company_owner",
                "company_name": "Firma Ofertowa",
            },
        )
        profile = client.patch(
            "/api/public-profile/me?owner_type=company",
            json={
                "is_public": True,
                "slug": "firma-ofertowa",
                "display_name": "Firma Ofertowa",
                "specializations": ["hydraulika"],
                "service_area": "Mazowsze",
                "contact_phone": "600 333 444",
                "contact_email": "firma-offer-public@example.com",
            },
        )
        assert profile.status_code == 200

        interest = client.post(
            f"/api/job-postings/public/{posting['id']}/interest",
            json={"message": "Firma przygotuje wycene."},
        )
        assert interest.status_code == 201
        assert interest.json()["contractor_owner_type"] == "company"

        offer = client.post(
            f"/api/job-postings/public/{posting['id']}/offer",
            json={
                "title": "Oferta firmy",
                "scope_summary": "Kompleksowa realizacja z materialem.",
                "estimated_price": "30000.00",
                "planned_start": "wrzesien 2026",
                "status": "sent",
            },
        )
        assert offer.status_code == 201
        body = offer.json()
        assert body["contractor_owner_type"] == "company"
        assert body["status"] == "sent"

        login(client, "company-offer-investor@example.com")
        all_offers = client.get("/api/job-postings/me/offers")
        assert all_offers.status_code == 200
        assert all_offers.json()[0]["contractor"]["owner_type"] == "company"
        assert all_offers.json()[0]["contractor"]["contact_email"] == "firma-offer-public@example.com"

        rejected = client.patch(
            f"/api/job-postings/me/offers/{body['id']}",
            json={"status": "rejected"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["rejected_at"]


def test_investor_and_company_worker_cannot_create_job_posting_offers():
    with TestClient(app) as client:
        posting = create_job_posting(
            client,
            "blocked-offer-investor@example.com",
            title="Zlecenie bez ofert z roli",
        )

        blocked_investor = client.post(
            f"/api/job-postings/public/{posting['id']}/offer",
            json={
                "title": "Nie jestem wykonawca",
                "scope_summary": "Zakres",
                "status": "sent",
            },
        )
        assert blocked_investor.status_code == 403

        login(client, "blocked-offer-worker@example.com")
        client.post("/api/onboarding", json={"profile_type": "company_worker"})
        blocked_worker = client.post(
            f"/api/job-postings/public/{posting['id']}/offer",
            json={
                "title": "Pracownik nie sklada",
                "scope_summary": "Zakres",
                "status": "sent",
            },
        )
        assert blocked_worker.status_code == 403


def test_independent_contractor_can_create_and_send_estimate():
    with TestClient(app) as client:
        user = login(client, "estimate-independent@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})

        created = client.post(
            "/api/estimates/me",
            json={
                "owner_type": "independent_contractor",
                "owner_id": user["id"],
                "recipient_type": "manual",
                "recipient_name": "Klient testowy",
                "recipient_email": "estimate-recipient@example.com",
                "recipient_phone": "500 000 100",
                "source_type": "manual",
                "title": "Wycena remontu lazienki",
                "scope_summary": "Demontaz, hydraulika i montaz.",
                "estimated_price": "12000.00",
                "status": "draft",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["owner_type"] == "independent_contractor"
        assert body["owner_id"] == user["id"]
        assert body["created_by_id"] == user["id"]
        assert body["recipient_email"] == "estimate-recipient@example.com"
        assert body["recipient_email"] != "estimate-independent@example.com"
        assert body["status"] == "draft"

        sent = client.patch(
            f"/api/estimates/me/{body['id']}/status",
            json={"status": "sent"},
        )
        assert sent.status_code == 200
        sent_body = sent.json()
        assert sent_body["status"] == "sent"
        assert sent_body["sent_at"]

        listed = client.get("/api/estimates/me")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [body["id"]]


def test_company_owner_can_create_and_send_company_estimate():
    with TestClient(app) as client:
        login(client, "estimate-owner@example.com")
        onboarded = client.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma Wycen"},
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]

        created = client.post(
            "/api/estimates/me",
            json={
                "owner_type": "company",
                "owner_id": workspace_id,
                "recipient_type": "client",
                "recipient_name": "Klient firmowy",
                "source_type": "manual",
                "title": "Oferta firmy",
                "scope_summary": "Zakres firmowej wyceny.",
                "status": "draft",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["owner_type"] == "company"
        assert body["owner_id"] == workspace_id
        assert body["status"] == "draft"

        sent = client.patch(
            f"/api/estimates/me/{body['id']}/status",
            json={"status": "sent"},
        )
        assert sent.status_code == 200
        sent_body = sent.json()
        assert sent_body["status"] == "sent"
        assert sent_body["approved_by_id"]
        assert sent_body["approved_at"]


def test_company_worker_estimate_requires_owner_approval_to_send():
    with TestClient(app) as owner:
        owner_user = login(owner, "estimate-worker-owner@example.com")
        onboarded = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma Zatwierdzen"},
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]

        with TestClient(app) as worker:
            login(worker, "estimate-worker@example.com")
            worker.post("/api/onboarding", json={"profile_type": "company_worker"})
            added = owner.post(
                "/api/workers",
                json={
                    "workspace_id": workspace_id,
                    "label": "Pracownik od wycen",
                    "profile_kind": "craftsman",
                    "email": "estimate-worker@example.com",
                    "phone": "500 200 300",
                },
            )
            assert added.status_code == 201

            created = worker.post(
                "/api/estimates/me",
                json={
                    "owner_type": "company",
                    "owner_id": workspace_id,
                    "recipient_type": "manual",
                    "recipient_name": "Klient szkicu",
                    "title": "Szkic pracownika",
                    "scope_summary": "Zakres do zatwierdzenia.",
                    "status": "pending_approval",
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["status"] == "pending_approval"
            assert body["owner_id"] == workspace_id

            worker_sent = worker.patch(
                f"/api/estimates/me/{body['id']}/status",
                json={"status": "sent"},
            )
            assert worker_sent.status_code == 403

            with TestClient(app) as other_worker:
                login(other_worker, "estimate-other-worker@example.com")
                other_worker.post("/api/onboarding", json={"profile_type": "company_worker"})
                owner.post(
                    "/api/workers",
                    json={
                        "workspace_id": workspace_id,
                        "label": "Drugi pracownik",
                        "profile_kind": "craftsman",
                        "email": "estimate-other-worker@example.com",
                    },
                )
                other_list = other_worker.get("/api/estimates/me")
                assert other_list.status_code == 200
                assert other_list.json() == []
                other_edit = other_worker.patch(
                    f"/api/estimates/me/{body['id']}",
                    json={"title": "Cudza zmiana"},
                )
                assert other_edit.status_code == 404

        owner_list = owner.get("/api/estimates/me")
        assert owner_list.status_code == 200
        assert body["id"] in {item["id"] for item in owner_list.json()}

        approved = owner.patch(
            f"/api/estimates/me/{body['id']}/status",
            json={"status": "approved_by_owner"},
        )
        assert approved.status_code == 200
        assert approved.json()["approved_by_id"] == owner_user["id"]

        sent = owner.patch(
            f"/api/estimates/me/{body['id']}/status",
            json={"status": "sent"},
        )
        assert sent.status_code == 200
        assert sent.json()["status"] == "sent"
        assert sent.json()["sent_at"]


def test_company_worker_can_prepare_project_estimate_draft_for_owner():
    with TestClient(app) as owner:
        login(owner, "estimate-project-worker-owner@example.com")
        onboarded = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma Wycen Projektowych"},
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]
        with TestClient(app) as worker:
            login(worker, "estimate-project-worker@example.com")
            worker.post("/api/onboarding", json={"profile_type": "company_worker"})
        worker_profile = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik od wycen projektowych",
                "profile_kind": "craftsman",
                "email": "estimate-project-worker@example.com",
            },
        ).json()
        assigned_project = owner.post(
            "/api/projects",
            json={
                "workspace_id": workspace_id,
                "worker_profile_id": worker_profile["id"],
                "name": "Zlecenie z dodatkowa wycena",
                "client_name": "Klient wyceny",
                "description": "Zakres bazowy zlecenia.",
                "template": "custom",
            },
        ).json()
        unassigned_project = owner.post(
            "/api/projects",
            json={
                "workspace_id": workspace_id,
                "name": "Nieprzypisane zlecenie firmowe",
                "client_name": "Inny klient",
                "template": "custom",
            },
        ).json()

        with TestClient(app) as worker:
            login(worker, "estimate-project-worker@example.com")
            created = worker.post(
                "/api/estimates/me",
                json={
                    "owner_type": "company",
                    "owner_id": workspace_id,
                    "recipient_type": "client",
                    "recipient_name": "Klient wyceny",
                    "source_type": "project",
                    "source_id": assigned_project["id"],
                    "title": "Szkic dodatkowych prac",
                    "scope_summary": "Dodatkowy zakres do zatwierdzenia.",
                    "status": "pending_approval",
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["status"] == "pending_approval"
            assert body["owner_type"] == "company"
            assert body["owner_id"] == workspace_id
            assert body["source_type"] == "project"
            assert body["source_id"] == assigned_project["id"]
            assert body["project_id"] is None

            blocked_unassigned = worker.post(
                "/api/estimates/me",
                json={
                    "owner_type": "company",
                    "owner_id": workspace_id,
                    "source_type": "project",
                    "source_id": unassigned_project["id"],
                    "title": "Cudzy szkic projektowy",
                    "scope_summary": "Nie powinno przejsc.",
                    "status": "pending_approval",
                },
            )
            assert blocked_unassigned.status_code == 403
            assert worker.patch(
                f"/api/estimates/me/{body['id']}/status",
                json={"status": "sent"},
            ).status_code == 403
            assert worker.post(f"/api/estimates/me/{body['id']}/project").status_code == 403

        owner_list = owner.get("/api/estimates/me")
        assert owner_list.status_code == 200
        owner_item = next(item for item in owner_list.json() if item["id"] == body["id"])
        assert owner_item["source_type"] == "project"
        assert owner_item["source_id"] == assigned_project["id"]

        sent = owner.patch(
            f"/api/estimates/me/{body['id']}/status",
            json={"status": "sent"},
        )
        assert sent.status_code == 200
        assert sent.json()["status"] == "sent"
        assert sent.json()["share_url"].startswith("/estimate/")


def test_estimate_access_blocks_investor_guest_and_other_company():
    with TestClient(app) as owner:
        login(owner, "estimate-access-owner@example.com")
        onboarded = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma Dostepu"},
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]
        created = owner.post(
            "/api/estimates/me",
            json={
                "owner_type": "company",
                "owner_id": workspace_id,
                "title": "Oferta firmy do ochrony",
                "scope_summary": "Zakres chroniony.",
                "status": "draft",
            },
        ).json()

        with TestClient(app) as investor:
            login(investor, "estimate-investor@example.com")
            investor.post("/api/onboarding", json={"profile_type": "investor"})
            blocked = investor.post(
                "/api/estimates/me",
                json={
                    "title": "Inwestor nie tworzy oferty",
                    "scope_summary": "Zakres",
                    "status": "draft",
                },
            )
            assert blocked.status_code == 403
            assert investor.get("/api/estimates/me").status_code == 403

        with TestClient(app) as other_owner:
            login(other_owner, "estimate-other-owner@example.com")
            other_owner.post(
                "/api/onboarding",
                json={"profile_type": "company_owner", "company_name": "Cudza Firma Wycen"},
            )
            other_status = other_owner.patch(
                f"/api/estimates/me/{created['id']}/status",
                json={"status": "sent"},
            )
            assert other_status.status_code == 404

        with TestClient(app) as public_client:
            assert public_client.get("/api/estimates/me").status_code == 401
            assert (
                public_client.get(
                    "/api/estimates/me",
                    headers={"x-guest-token": "guest-token"},
                ).status_code
                == 401
            )


def test_sent_estimate_has_public_link_and_public_decision_without_project():
    with TestClient(app) as client:
        user = login(client, "estimate-share-independent@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        public_profile = client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": True,
                "slug": "estimate-share-majster",
                "display_name": "Majster Publiczny",
                "contact_phone": "600 700 800",
                "contact_email": "public-estimate-contact@example.com",
            },
        )
        assert public_profile.status_code == 200

        draft = client.post(
            "/api/estimates/me",
            json={
                "owner_type": "independent_contractor",
                "owner_id": user["id"],
                "recipient_type": "manual",
                "recipient_name": "Klient linku",
                "recipient_email": "estimate-share-recipient@example.com",
                "recipient_phone": "500 400 300",
                "source_type": "manual",
                "title": "Oferta z linkiem",
                "scope_summary": "Zakres publicznej oferty.",
                "estimated_price": "18000.00",
                "status": "draft",
            },
        )
        assert draft.status_code == 201
        assert draft.json()["share_url"] is None

        sent = client.patch(
            f"/api/estimates/me/{draft.json()['id']}/status",
            json={"status": "sent"},
        )
        assert sent.status_code == 200
        sent_body = sent.json()
        assert sent_body["status"] == "sent"
        assert sent_body["share_active"] is True
        assert sent_body["share_url"].startswith("/estimate/")
        token = sent_body["share_url"].rsplit("/", maxsplit=1)[1]

        with TestClient(app) as public_client:
            public = public_client.get(f"/api/public/estimates/{token}")
            assert public.status_code == 200
            public_body = public.json()
            assert public_body["title"] == "Oferta z linkiem"
            assert public_body["number"]
            assert public_body["owner"]["display_name"] == "Majster Publiczny"
            assert public_body["owner"]["contact_phone"] == "600 700 800"
            assert public_body["owner"]["contact_email"] == "public-estimate-contact@example.com"
            assert public_body["owner"]["profile_url"] == "/public-profiles/estimate-share-majster"
            assert public_body["recipient_name"] == "Klient linku"
            assert public_body["recipient_email"] == "estimate-share-recipient@example.com"
            assert public_body["recipient_phone"] == "500 400 300"
            assert public_body["estimated_price"] == "18000.00"
            assert "created_by_id" not in public_body
            assert "owner_id" not in public_body
            assert "estimate-share-independent@example.com" not in repr(public_body)

            with SessionLocal() as db:
                project_count_before = db.scalar(select(func.count(models.Project.id)))

            accepted = public_client.post(
                f"/api/public/estimates/{token}/decision",
                json={"status": "accepted"},
            )
            assert accepted.status_code == 200
            assert accepted.json()["status"] == "accepted"
            assert accepted.json()["accepted_at"]

            rejected_after_accept = public_client.post(
                f"/api/public/estimates/{token}/decision",
                json={"status": "rejected"},
            )
            assert rejected_after_accept.status_code == 422

        with SessionLocal() as db:
            project_count_after = db.scalar(select(func.count(models.Project.id)))
        assert project_count_after == project_count_before
        accepted_estimate = client.get("/api/estimates/me").json()[0]
        assert accepted_estimate["status"] == "accepted"
        assert accepted_estimate["project_id"] is None

        second = client.post(
            "/api/estimates/me",
            json={
                "owner_type": "independent_contractor",
                "owner_id": user["id"],
                "recipient_type": "manual",
                "recipient_name": "Klient odrzucenia",
                "title": "Oferta do odrzucenia",
                "scope_summary": "Drugi zakres publicznej oferty.",
                "estimated_price": "9000.00",
                "status": "sent",
            },
        )
        assert second.status_code == 201
        reject_token = second.json()["share_url"].rsplit("/", maxsplit=1)[1]
        with TestClient(app) as public_client:
            rejected = public_client.post(
                f"/api/public/estimates/{reject_token}/decision",
                json={"status": "rejected"},
            )
            assert rejected.status_code == 200
            assert rejected.json()["status"] == "rejected"

        with SessionLocal() as db:
            assert db.scalar(select(func.count(models.Project.id))) == project_count_before


def test_accepted_independent_estimate_can_create_project_once():
    with TestClient(app) as client:
        user = login(client, "estimate-project-independent@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})

        created = client.post(
            "/api/estimates/me",
            json={
                "owner_type": "independent_contractor",
                "owner_id": user["id"],
                "recipient_type": "client",
                "recipient_name": "Klient projektu",
                "recipient_email": "estimate-project-client@example.com",
                "recipient_phone": "500 101 202",
                "title": "Remont z zaakceptowanej oferty",
                "scope_summary": "Demontaz, hydraulika i montaz plytek.",
                "assumptions": "Material po stronie klienta.",
                "estimated_price": "15400.00",
                "price_note": "Cena orientacyjna netto.",
                "planned_start": "2026-08-10",
                "planned_end": "2026-08-31",
                "status": "sent",
            },
        )
        assert created.status_code == 201
        estimate_id = created.json()["id"]
        accepted = client.patch(
            f"/api/estimates/me/{estimate_id}/status",
            json={"status": "accepted"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["project_id"] is None

        with SessionLocal() as db:
            project_count_before = db.scalar(select(func.count(models.Project.id)))

        response = client.post(f"/api/estimates/me/{estimate_id}/project")
        assert response.status_code == 201
        body = response.json()
        assert body["created"] is True
        assert body["estimate"]["project_id"] == body["project"]["id"]
        project = body["project"]
        assert project["name"] == "Remont z zaakceptowanej oferty"
        assert project["client_name"] == "Klient projektu"
        assert project["client_email"] == "estimate-project-client@example.com"
        assert project["status"] == "assigned"
        assert project["planned_start_date"] == "2026-08-10"
        assert project["planned_end_date"] == "2026-08-31"
        assert project["contract_amount"] == "15400.00"
        assert project["contract_currency"] == "PLN"
        assert "Utworzono z zaakceptowanej oferty/wyceny." in project["description"]
        assert "Telefon odbiorcy: 500 101 202" in project["description"]
        assert project["stages"]

        with SessionLocal() as db:
            project_count_after = db.scalar(select(func.count(models.Project.id)))
        assert project_count_after == project_count_before + 1

        second = client.post(f"/api/estimates/me/{estimate_id}/project")
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["created"] is False
        assert second_body["project"]["id"] == project["id"]
        with SessionLocal() as db:
            assert db.scalar(select(func.count(models.Project.id))) == project_count_after
            stored = db.get(models.Estimate, estimate_id)
            assert stored
            assert stored.project_id == project["id"]

        listed_projects = client.get("/api/projects")
        assert listed_projects.status_code == 200
        assert project["id"] in {item["id"] for item in listed_projects.json()}
        detail = client.get(f"/api/projects/{project['id']}")
        assert detail.status_code == 200
        assert detail.json()["id"] == project["id"]


def test_company_owner_can_create_company_project_from_accepted_estimate():
    with TestClient(app) as owner:
        login(owner, "estimate-project-owner@example.com")
        onboarded = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma Projektow"},
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]

        created = owner.post(
            "/api/estimates/me",
            json={
                "owner_type": "company",
                "owner_id": workspace_id,
                "recipient_type": "client",
                "recipient_name": "Klient firmy",
                "title": "Oferta firmy do zlecenia",
                "scope_summary": "Zakres firmowy.",
                "status": "sent",
            },
        )
        assert created.status_code == 201
        estimate_id = created.json()["id"]
        accepted = owner.patch(
            f"/api/estimates/me/{estimate_id}/status",
            json={"status": "accepted"},
        )
        assert accepted.status_code == 200

        response = owner.post(f"/api/estimates/me/{estimate_id}/project")
        assert response.status_code == 201
        body = response.json()
        assert body["created"] is True
        assert body["project"]["workspace_id"] == workspace_id
        assert body["estimate"]["project_id"] == body["project"]["id"]


def test_estimate_project_creation_blocks_status_worker_and_investor():
    with TestClient(app) as independent:
        user = login(independent, "estimate-project-blocked-independent@example.com")
        independent.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        draft = independent.post(
            "/api/estimates/me",
            json={
                "owner_type": "independent_contractor",
                "owner_id": user["id"],
                "title": "Niezaakceptowana oferta",
                "scope_summary": "Zakres.",
                "status": "draft",
            },
        ).json()
        assert independent.post(f"/api/estimates/me/{draft['id']}/project").status_code == 422

    with TestClient(app) as owner:
        login(owner, "estimate-project-blocked-owner@example.com")
        onboarded = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma Blokad"},
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]

        with TestClient(app) as worker:
            login(worker, "estimate-project-blocked-worker@example.com")
            worker.post("/api/onboarding", json={"profile_type": "company_worker"})
            owner.post(
                "/api/workers",
                json={
                    "workspace_id": workspace_id,
                    "label": "Pracownik blokowany",
                    "profile_kind": "craftsman",
                    "email": "estimate-project-blocked-worker@example.com",
                },
            )
            created = worker.post(
                "/api/estimates/me",
                json={
                    "owner_type": "company",
                    "owner_id": workspace_id,
                    "title": "Szkic pracownika bez tworzenia",
                    "scope_summary": "Zakres.",
                    "status": "pending_approval",
                },
            ).json()
            assert worker.post(f"/api/estimates/me/{created['id']}/project").status_code == 403

    with TestClient(app) as investor:
        login(investor, "estimate-project-blocked-investor@example.com")
        investor.post("/api/onboarding", json={"profile_type": "investor"})
        assert investor.post(f"/api/estimates/me/{draft['id']}/project").status_code == 403


def test_independent_project_contract_flow_is_idempotent_and_public():
    with TestClient(app) as client:
        login(client, "contract-independent@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        client.patch(
            "/api/public-profile/me?owner_type=independent_contractor",
            json={
                "is_public": True,
                "slug": "contract-independent-profile",
                "display_name": "Majster Umow",
                "contact_phone": "600 100 200",
                "contact_email": "public-contract@example.com",
            },
        )
        project = client.post(
            "/api/projects",
            json={
                "name": "Lazienka z umowa",
                "client_name": "Klient umowy",
                "client_email": "client-contract@example.com",
                "address": "Warszawa, Testowa 1",
                "description": "Remont lazienki bez danych prywatnych.",
                "planned_start_date": "2026-09-01",
                "planned_end_date": "2026-09-20",
                "contract_amount": "22000.00",
                "template": "custom",
            },
        ).json()

        with SessionLocal() as db:
            count_before = db.scalar(select(func.count(models.ProjectContract.id)))
            project_count_before = db.scalar(select(func.count(models.Project.id)))

        created = client.post(f"/api/projects/{project['id']}/contract")
        assert created.status_code == 201
        body = created.json()
        assert body["created"] is True
        contract = body["contract"]
        assert contract["project_id"] == project["id"]
        assert contract["owner_type"] == "independent_contractor"
        assert contract["status"] == "draft"
        assert contract["client_name"] == "Klient umowy"
        assert contract["client_email"] == "client-contract@example.com"
        assert contract["work_address"] == "Warszawa, Testowa 1"
        assert contract["price_amount"] == "22000.00"
        assert contract["share_url"] is None

        duplicate = client.post(f"/api/projects/{project['id']}/contract")
        assert duplicate.status_code == 200
        assert duplicate.json()["created"] is False
        assert duplicate.json()["contract"]["id"] == contract["id"]

        with SessionLocal() as db:
            assert db.scalar(select(func.count(models.ProjectContract.id))) == count_before + 1
            assert db.scalar(select(func.count(models.Project.id))) == project_count_before

        patched = client.patch(
            f"/api/contracts/me/{contract['id']}",
            json={
                "client_phone": "500 111 222",
                "scope_summary": "Zakres umowy po edycji.",
                "terms_summary": "Platnosc po odbiorze etapu.",
                "price_amount": "23000.00",
                "deposit_amount": "3000.00",
                "price_note": "Kwota brutto.",
                "attachments_note": "Brak zalacznikow.",
            },
        )
        assert patched.status_code == 200
        assert patched.json()["scope_summary"] == "Zakres umowy po edycji."
        assert patched.json()["deposit_amount"] == "3000.00"

        sent = client.patch(
            f"/api/contracts/me/{contract['id']}/status",
            json={"status": "sent"},
        )
        assert sent.status_code == 200
        sent_body = sent.json()
        assert sent_body["status"] == "sent"
        assert sent_body["share_url"].startswith("/contract/")
        token = sent_body["share_url"].rsplit("/", maxsplit=1)[1]

        assert client.get(f"/api/contracts/me/{contract['id']}").json()["status"] == "sent"

        with TestClient(app) as public_client:
            public = public_client.get(f"/api/contracts/public/{token}")
            assert public.status_code == 200
            public_body = public.json()
            assert public_body["project_name"] == "Lazienka z umowa"
            assert public_body["number"]
            assert public_body["owner"]["display_name"] == "Majster Umow"
            assert public_body["owner"]["contact_email"] == "public-contract@example.com"
            assert public_body["client_name"] == "Klient umowy"
            assert public_body["client_email"] == "client-contract@example.com"
            assert public_body["client_phone"] == "500 111 222"
            assert public_body["scope_summary"] == "Zakres umowy po edycji."
            assert public_body["price_amount"] == "23000.00"
            assert "project_id" not in public_body
            assert "owner_id" not in public_body
            assert "created_by_id" not in public_body
            assert "share_token" not in public_body
            assert "contract-independent@example.com" not in repr(public_body)

            accepted = public_client.post(f"/api/contracts/public/{token}/accept")
            assert accepted.status_code == 200
            assert accepted.json()["status"] == "accepted"
            assert accepted.json()["accepted_at"]
            assert public_client.post(f"/api/contracts/public/{token}/reject").status_code == 422

        with SessionLocal() as db:
            assert db.scalar(select(func.count(models.Project.id))) == project_count_before
            stored = db.get(models.ProjectContract, contract["id"])
            assert stored
            assert stored.status == "accepted"


def test_project_contract_public_reject_and_cancel_blocks_link():
    with TestClient(app) as client:
        login(client, "contract-reject@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        project = client.post(
            "/api/projects",
            json={"name": "Umowa do odrzucenia", "client_name": "Klient reject", "description": "Zakres.", "template": "custom"},
        ).json()
        contract = client.post(f"/api/projects/{project['id']}/contract").json()["contract"]
        sent = client.patch(f"/api/contracts/me/{contract['id']}/status", json={"status": "sent"}).json()
        token = sent["share_url"].rsplit("/", maxsplit=1)[1]

        with TestClient(app) as public_client:
            rejected = public_client.post(f"/api/contracts/public/{token}/reject")
            assert rejected.status_code == 200
            assert rejected.json()["status"] == "rejected"
            assert public_client.post(f"/api/contracts/public/{token}/accept").status_code == 422

        second_project = client.post(
            "/api/projects",
            json={"name": "Umowa anulowana", "client_name": "Klient cancel", "description": "Zakres.", "template": "custom"},
        ).json()
        second = client.post(f"/api/projects/{second_project['id']}/contract").json()["contract"]
        second_sent = client.patch(f"/api/contracts/me/{second['id']}/status", json={"status": "sent"}).json()
        second_token = second_sent["share_url"].rsplit("/", maxsplit=1)[1]
        cancelled = client.patch(f"/api/contracts/me/{second['id']}/status", json={"status": "cancelled"})
        assert cancelled.status_code == 200
        with TestClient(app) as public_client:
            assert public_client.get(f"/api/contracts/public/{second_token}").status_code == 404


def test_project_contract_blocks_company_worker_investor_and_other_owner():
    with TestClient(app) as owner:
        login(owner, "project-contract-owner@example.com")
        onboarded = owner.post(
            "/api/onboarding",
            json={"profile_type": "company_owner", "company_name": "Firma Umow"},
        ).json()
        workspace_id = onboarded["workspaces"][0]["id"]
        with TestClient(app) as worker:
            login(worker, "project-contract-worker@example.com")
            worker.post("/api/onboarding", json={"profile_type": "company_worker"})
        worker_profile = owner.post(
            "/api/workers",
            json={
                "workspace_id": workspace_id,
                "label": "Pracownik umow",
                "profile_kind": "craftsman",
                "email": "project-contract-worker@example.com",
            },
        ).json()
        project = owner.post(
            "/api/projects",
            json={
                "workspace_id": workspace_id,
                "worker_profile_id": worker_profile["id"],
                "name": "Projekt firmowej umowy",
                "client_name": "Klient firmy",
                "description": "Zakres firmy.",
                "template": "custom",
            },
        ).json()
        created = owner.post(f"/api/projects/{project['id']}/contract")
        assert created.status_code == 201
        contract = created.json()["contract"]
        assert contract["owner_type"] == "company"
        assert contract["company_id"] == workspace_id

        with TestClient(app) as worker:
            login(worker, "project-contract-worker@example.com")
            assert worker.post(f"/api/projects/{project['id']}/contract").status_code == 403
            assert worker.patch(f"/api/contracts/me/{contract['id']}/status", json={"status": "sent"}).status_code == 403

        with TestClient(app) as investor:
            login(investor, "contract-investor@example.com")
            investor.post("/api/onboarding", json={"profile_type": "investor"})
            assert investor.get("/api/contracts/me").status_code == 403
            assert investor.post(f"/api/projects/{project['id']}/contract").status_code in {403, 404}

        with TestClient(app) as other_owner:
            login(other_owner, "contract-other-owner@example.com")
            other_owner.post(
                "/api/onboarding",
                json={"profile_type": "company_owner", "company_name": "Inna Firma Umow"},
            )
            assert other_owner.patch(f"/api/contracts/me/{contract['id']}", json={"scope_summary": "Cudza zmiana"}).status_code == 404


def test_estimate_delete_and_cancel_are_owner_scoped():
    with TestClient(app) as client:
        user = login(client, "estimate-delete-owner@example.com")
        client.post("/api/onboarding", json={"profile_type": "independent_contractor"})
        draft = client.post(
            "/api/estimates/me",
            json={
                "owner_type": "independent_contractor",
                "owner_id": user["id"],
                "recipient_type": "manual",
                "recipient_name": "Klient szkicu",
                "title": "Szkic do usuniecia",
                "scope_summary": "Zakres szkicu.",
                "status": "draft",
            },
        ).json()

        with TestClient(app) as other:
            other_user = login(other, "estimate-delete-other@example.com")
            other.post("/api/onboarding", json={"profile_type": "independent_contractor"})
            blocked = other.delete(f"/api/estimates/me/{draft['id']}")
            assert blocked.status_code == 404
            foreign_create = other.post(
                "/api/estimates/me",
                json={
                    "owner_type": "independent_contractor",
                    "owner_id": other_user["id"],
                    "recipient_type": "manual",
                    "title": "Cudzy szkic",
                    "scope_summary": "Zakres cudzego szkicu.",
                    "status": "draft",
                },
            )
            assert foreign_create.status_code == 201

        deleted = client.delete(f"/api/estimates/me/{draft['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deleted"
        assert draft["id"] not in {item["id"] for item in client.get("/api/estimates/me").json()}

        sent = client.post(
            "/api/estimates/me",
            json={
                "owner_type": "independent_contractor",
                "owner_id": user["id"],
                "recipient_type": "manual",
                "recipient_name": "Klient anulowania",
                "title": "Oferta do anulowania",
                "scope_summary": "Zakres wyslanej oferty.",
                "status": "sent",
            },
        )
        assert sent.status_code == 201
        token = sent.json()["share_url"].rsplit("/", maxsplit=1)[1]
        cancelled = client.delete(f"/api/estimates/me/{sent.json()['id']}")
        assert cancelled.status_code == 200
        cancelled_body = cancelled.json()
        assert cancelled_body["status"] == "cancelled"
        assert cancelled_body["estimate"]["status"] == "cancelled"
        assert cancelled_body["estimate"]["share_active"] is False

        with TestClient(app) as public_client:
            assert public_client.get(f"/api/public/estimates/{token}").status_code == 404
