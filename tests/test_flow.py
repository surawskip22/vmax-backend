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
        "STORAGE_PROVIDER": "database",
        "MEDIA_ROOT": str(TEST_ROOT / "media"),
        "WORKER_ENABLED": "false",
        "ADMIN_EMAILS": "admin@example.com",
    }
)

from fastapi.testclient import TestClient
from sqlalchemy import select

from main import app
from panmajster import models
from panmajster.db import SessionLocal
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
        link = client.get(f"/api/projects/{project['id']}/client-link").json()
        token = link["url"].rsplit("/", 1)[-1]

        first_entry = client.post(
            f"/api/projects/{project['id']}/entries",
            json={"kind": "update", "body": "Pierwszy dzień"},
        )
        assert first_entry.status_code == 201
        public_before = client.get(f"/api/public/projects/{token}").json()
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
                json={"kind": "update", "body": "Drugi postęp od linku"},
            ).status_code
            == 201
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

    with TestClient(app) as worker_client:
        user = login(worker_client, "pracownik-firmy@example.com")
        assert user["profile_type"] == "company_worker"
        projects = worker_client.get("/api/projects").json()
        assert [item["id"] for item in projects] == [project["id"]]
        assert unassigned["id"] not in [item["id"] for item in projects]
        assert projects[0]["role"] == "contributor"
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
