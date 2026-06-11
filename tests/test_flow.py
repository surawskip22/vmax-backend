from __future__ import annotations

import os
import tempfile
from pathlib import Path


TEST_ROOT = Path(tempfile.mkdtemp(prefix="panmajster-tests-"))
os.environ.update(
    {
        "APP_ENV": "development",
        "SECRET_KEY": "test-secret",
        "DATABASE_URL": f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}",
        "MEDIA_ROOT": str(TEST_ROOT / "media"),
        "WORKER_ENABLED": "false",
        "ADMIN_EMAILS": "admin@example.com",
    }
)

from fastapi.testclient import TestClient

from main import app
from panmajster.reporting import _merge_generated_content
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
        entry = entry_response.json()

        image = b"\x89PNG\r\n\x1a\n" + b"test-image"
        upload = client.post(
            f"/api/entries/{entry['id']}/media",
            files={"file": ("postep.png", image, "image/png")},
            data={"client_ref": "offline-file-1"},
        )
        assert upload.status_code == 201
        asset = upload.json()
        assert len(asset["sha256"]) == 64

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

        owner.delete(
            f"/api/projects/{project['id']}/guest-links/{invitation['id']}"
        )
        assert owner.get(f"/api/guest/{token}").status_code == 404


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
