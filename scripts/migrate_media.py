from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from sqlalchemy import select

from panmajster import models
from panmajster.db import SessionLocal
from panmajster.storage import storage


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kopiuje media do nowego katalogu i weryfikuje integralność."
    )
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--provider", default="filesystem_export")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Po weryfikacji aktualizuje provider i klucz w bazie.",
    )
    args = parser.parse_args()
    target_root = Path(args.target_dir).resolve()
    target_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    with SessionLocal() as db:
        assets = db.scalars(
            select(models.MediaAsset).order_by(models.MediaAsset.created_at)
        ).all()
        for asset in assets:
            try:
                content = storage.read_bytes(asset.storage_key)
            except FileNotFoundError:
                raise RuntimeError(f"Brak źródła: {asset.id}")
            suffix = Path(asset.original_name).suffix.lower()
            target_key = f"{asset.project_id}/{asset.id}{suffix}"
            target = target_root / target_key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            if target.stat().st_size != asset.size_bytes or digest != asset.sha256:
                target.unlink(missing_ok=True)
                raise RuntimeError(f"Weryfikacja nie powiodła się: {asset.id}")
            if args.commit:
                asset.storage_provider = args.provider
                asset.storage_key = target_key
            copied += 1
        if args.commit:
            db.commit()
    print(f"Skopiowano i zweryfikowano {copied} plików.")


if __name__ == "__main__":
    main()
