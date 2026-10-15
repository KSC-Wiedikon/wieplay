"""Test setup: a Postgres test database migrated with Alembic, and a test bridge key.

Needs Postgres: `docker compose up -d` at the repo root (or NOMAL_TEST_DATABASE_URL).
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from nomal.api.app import create_app
from nomal.api.dev_bridge import FixtureBridgeClient
from nomal.core.config import Settings
from nomal.core.tokens import generate_keypair, sign_token

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB = os.environ.get(
    "NOMAL_TEST_DATABASE_URL", "postgresql+psycopg://nomal:nomal@127.0.0.1:55488/nomal_test"
)
PRIVATE_PEM, PUBLIC_PEM = generate_keypair()
OTHER_PRIVATE_PEM, _ = generate_keypair()
KID = "test"
ISSUER = "wiedisync-test"
PUBLIC_URL = "http://testserver"


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[Engine]:
    engine = create_engine(TEST_DB)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = TEST_DB
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_engine(migrated_db: Engine) -> Iterator[Engine]:
    yield migrated_db
    with migrated_db.begin() as conn:
        tables = (
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="local",
        database_url=TEST_DB,
        public_url=PUBLIC_URL,
        bridge_authorize_url="https://bridge.test/kscw/nomal/v1/authorize",
        bridge_issuer=ISSUER,
        bridge_public_keys={KID: PUBLIC_PEM},
        cookie_secure=False,
        sync_interval_minutes=0,
    )


@pytest.fixture
def app(settings: Settings, db_engine: Engine) -> FastAPI:
    app = create_app(settings)
    app.state.bridge_client_factory = FixtureBridgeClient
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as c:
        yield c


def make_token(
    claims: Mapping[str, Any],
    state: str,
    *,
    private_pem: str = PRIVATE_PEM,
    kid: str = KID,
    lifetime: int = 60,
    **overrides: Any,
) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": "nomal",
        "iat": now,
        "exp": now + lifetime,
        "jti": secrets.token_urlsafe(12),
        "state": state,
        **claims,
        **overrides,
    }
    return sign_token(payload, private_pem=private_pem, kid=kid)


def start_login(client: TestClient, next_path: str = "/") -> str:
    """GET /api/auth/login and return the state the bridge would echo back."""
    response = client.get("/api/auth/login", params={"next": next_path})
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query["state"][0]


def login(client: TestClient, claims: Mapping[str, Any], next_path: str = "/") -> None:
    state = start_login(client, next_path)
    response = client.get(
        "/api/auth/callback", params={"token": make_token(claims, state), "state": state}
    )
    assert response.status_code == 302, response.text
