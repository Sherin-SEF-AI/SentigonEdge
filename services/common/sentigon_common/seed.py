"""Configuration seed: the governed ontology root (site), the built-in signature
catalog (definition defaults), and one real admin credential.

    python -m sentigon_common.seed

This deliberately seeds NO runtime data. Cameras are registered through the real
API onboarding path (scripts/register_cameras.py -> `make cameras`); zones are
created through the real /zones API (the ROI editor or the same onboarding). Events
and incidents are produced only by real inference on real streams. Idempotent.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import yaml
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import sync_session_factory
from .db.models import Signature, Site, User
from .kafka import ensure_topics
from .logging import configure_logging, get_logger
from .schemas.bus import Topics
from .schemas.enums import DetectionMethod, Severity, UserRole
from .storage import get_store

REPO_ROOT = Path(__file__).resolve().parents[3]
log = get_logger("seed")
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

_SEVERITY = {s.value: s for s in Severity}
_METHOD = {
    "yolo": DetectionMethod.YOLO,
    "gemini": DetectionMethod.VLM,
    "hybrid": DetectionMethod.HYBRID,
    "pose": DetectionMethod.POSE,
    "audio": DetectionMethod.AUDIO,
}


def _load_catalog() -> list:
    path = REPO_ROOT / "configs" / "signatures" / "catalog.py"
    spec = importlib.util.spec_from_file_location("sentigon_signature_catalog", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return list(mod.THREAT_SIGNATURES)


def _load_yaml(rel: str) -> dict:
    with open(REPO_ROOT / rel) as fh:
        return yaml.safe_load(fh)


def seed_site(session: Session) -> Site:
    """Ontology root only. Buildings/zones/cameras are created at runtime."""
    s = _load_yaml("configs/ontology/zones.yaml")["site"]
    site = session.execute(select(Site).where(Site.name == s["name"])).scalar_one_or_none()
    if site is None:
        site = Site(
            name=s["name"],
            address=s.get("address"),
            timezone=s.get("timezone", "UTC"),
            center=s.get("center"),
        )
        session.add(site)
        session.flush()
    return site


def seed_signatures(session: Session) -> tuple[int, int]:
    created = updated = 0
    for d in _load_catalog():
        sev = _SEVERITY.get(d.severity, Severity.MEDIUM)
        method = _METHOD.get(d.detection_method, DetectionMethod.HYBRID)
        params = {
            "yolo_classes": d.yolo_classes,
            "keywords": d.gemini_keywords,
            "conditions": d.conditions,
        }
        existing = session.execute(
            select(Signature).where(Signature.name == d.name)
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Signature(
                    name=d.name,
                    category=d.category,
                    description=d.description,
                    severity=sev,
                    detection_method=method,
                    params=params,
                    source="built_in",
                )
            )
            created += 1
        else:
            existing.category = d.category
            existing.description = d.description
            existing.severity = sev
            existing.detection_method = method
            existing.params = params
            updated += 1
    return created, updated


def seed_admin(session: Session) -> bool:
    if not settings.default_admin_password:
        log.warning("admin.skipped", reason="DEFAULT_ADMIN_PASSWORD not set")
        return False
    existing = session.execute(
        select(User).where(User.email == settings.default_admin_email)
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(
        User(
            email=settings.default_admin_email,
            hashed_password=_pwd.hash(settings.default_admin_password),
            full_name="System Administrator",
            role=UserRole.ADMIN,
        )
    )
    return True


def main() -> None:
    configure_logging("seed")
    get_store().ensure_buckets()
    asyncio.run(ensure_topics(Topics.ALL))

    with sync_session_factory() as session:
        site = seed_site(session)
        created, updated = seed_signatures(session)
        admin = seed_admin(session)
        session.commit()
        site_name = site.name

    log.info(
        "seed.done",
        site=site_name,
        signatures_created=created,
        signatures_updated=updated,
        admin_created=admin,
    )
    print(
        f"seeded config only: site='{site_name}' "
        f"signatures(created={created}, updated={updated}) admin_created={admin} "
        f"(cameras + zones are registered via `make cameras`)"
    )


if __name__ == "__main__":
    main()
