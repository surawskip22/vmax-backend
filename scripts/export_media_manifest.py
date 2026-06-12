from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from panmajster import models
from panmajster.db import SessionLocal
from panmajster.storage import storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Eksport manifestu mediów Pan Majster")
    parser.add_argument("--output", default="media-manifest.json")
    args = parser.parse_args()

    with SessionLocal() as db:
        assets = db.scalars(select(models.MediaAsset).order_by(models.MediaAsset.created_at)).all()
        rows = []
        for asset in assets:
            rows.append(
                {
                    "id": asset.id,
                    "project_id": asset.project_id,
                    "storage_provider": asset.storage_provider,
                    "storage_key": asset.storage_key,
                    "size_bytes": asset.size_bytes,
                    "sha256": asset.sha256,
                    "exists": storage.exists(asset.storage_key),
                }
            )
    output = Path(args.output)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Zapisano {len(rows)} rekordów do {output}")


if __name__ == "__main__":
    main()
