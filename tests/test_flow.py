from __future__ import annotations

import os
import tempfile
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
            "Drugi dzień",
            "Pierwszy dzień",
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
        assert closed.json()["status"] == "completed"
        assert owner.post(f"/api/projects/{project['id']}/close").json()["status"] == "completed"

        reopened = owner.post(f"/api/projects/{project['id']}/reopen")
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "in_progress"

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
        assert worker_client.post(f"/api/projects/{project['id']}/reopen").status_code == 403
        assert worker_client.post(f"/api/projects/{unassigned['id']}/close").status_code == 403
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

    with TestClient(app) as public_client:
        assert public_client.get(f"/api/public/projects/{client_token}").json()[
            "project"
        ]["status"] == "completed"
        assert public_client.post(f"/api/projects/{project['id']}/close").status_code == 403

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
        assert result.company_statuses["in_progress"] == 4
        assert result.company_statuses["completed"] == 5
        assert result.independent_statuses["assigned"] == 1
        assert result.independent_statuses["in_progress"] == 2
        assert result.independent_statuses["completed"] == 4
        assert result.investor_statuses["assigned"] == 1
        assert result.investor_statuses["in_progress"] == 2
        assert result.investor_statuses["completed"] == 5
        company = db.scalar(
            select(models.Workspace).where(
                models.Workspace.name == "Firma Remontowo-Budowlana Majster Demo"
            )
        )
        assert company is not None
        company_workers = db.scalars(
            select(models.WorkerProfile).where(
                models.WorkerProfile.workspace_id == company.id
            )
        ).all()
        assert len(company_workers) == 8
        assert any(
            worker.label == "Staszek Malarz Nieaktywny" and not worker.active
            for worker in company_workers
        )
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
            == 5
        )
        assert result.guest_links >= 3
        assert result.client_links >= 25
        assert db.scalar(
            select(models.User).where(models.User.email == "old-demo-noise@example.com")
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
