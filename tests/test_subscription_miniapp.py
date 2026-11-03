"""Telegram Mini App API: initData authentication and watch routes."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import pytest

from registrarmonitor.subscriptions.catalog import SubscriptionCatalog
from registrarmonitor.subscriptions.miniapp import (
    InvalidInitData,
    MiniAppApi,
    validate_init_data,
)
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.runtime import WebhookConfig, create_web_app
from registrarmonitor.subscriptions.store import SubscriptionStore

pytestmark = pytest.mark.unit

BOT_TOKEN = "123456:test-bot-token"
ORIGIN = "https://site.example.test"
USER_ID = 41


def sign_init_data(
    *, user_id=USER_ID, auth_date=None, signer=BOT_TOKEN, tamper=False
) -> str:
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": "Ada"}),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", signer.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["user"] = json.dumps({"id": user_id + 1, "first_name": "Ada"})
    return urlencode(fields)


def test_init_data_returns_the_signed_user_id():
    assert validate_init_data(sign_init_data(user_id=7), BOT_TOKEN) == 7


@pytest.mark.parametrize(
    "init_data",
    [
        sign_init_data(tamper=True),
        sign_init_data(signer="999:another-bot"),
        sign_init_data(auth_date=int(time.time()) - 25 * 3600),
        sign_init_data(auth_date=int(time.time()) + 3600),
        "",
        "user=%7B%7D&auth_date=1",
    ],
    ids=["tampered", "wrong-bot", "expired", "future", "empty", "unsigned"],
)
def test_init_data_rejects_forged_or_stale_values(init_data):
    with pytest.raises(InvalidInitData):
        validate_init_data(init_data, BOT_TOKEN)


@pytest.fixture
def store(tmp_path):
    return SubscriptionStore(tmp_path / "bot.db")


@pytest.fixture
def client(store, current_snapshot):
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    api = MiniAppApi(store, provide_catalog, BOT_TOKEN, ORIGIN)
    application = SimpleNamespace(bot=None, update_queue=None)
    app = create_web_app(
        cast(Any, application),
        WebhookConfig("https://bot.example.test/telegram", "secret"),
        miniapp=api,
        site_origin=ORIGIN,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://bot.example.test"
    )


def auth(**kwargs) -> dict[str, str]:
    return {"Authorization": f"tma {sign_init_data(**kwargs)}", "Origin": ORIGIN}


COURSE = {"semester": "Spring 2024", "course": "CS 101"}


@pytest.mark.asyncio
async def test_add_list_and_remove_watches_use_the_real_store(client, store):
    async with client:
        added = await client.post("/miniapp/watches", json=COURSE, headers=auth())
        section = await client.post(
            "/miniapp/watches", json={**COURSE, "section": "10L"}, headers=auth()
        )
        listed = await client.get("/miniapp/watches", headers=auth())

        assert added.status_code == 200
        assert added.json()["added"] is True
        # Same dedupe as the bot: a section under a whole-course watch is covered.
        assert section.json()["added"] is False
        body = listed.json()
        assert body["semester"] == "Spring 2024"
        assert [(w["course"], w["section"]) for w in body["watches"]] == [
            ("CS 101", "")
        ]
        assert store.list_subscriptions(USER_ID) == [
            SubscriptionTarget("Spring 2024", "CS 101")
        ]

        removed = await client.delete(
            f"/miniapp/watches/{body['watches'][0]['id']}", headers=auth()
        )
        assert removed.json()["removed"] is True
        assert removed.json()["watches"] == []
        assert store.list_subscriptions(USER_ID) == []


@pytest.mark.asyncio
async def test_watches_belong_to_the_signed_user_only(client, store):
    async with client:
        await client.post("/miniapp/watches", json=COURSE, headers=auth())
        other = await client.get("/miniapp/watches", headers=auth(user_id=99))

    assert other.json()["watches"] == []
    assert store.list_subscriptions(USER_ID) != []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"semester": "Spring 2024", "course": "NOPE 999"},
        {"semester": "Spring 2024", "course": "CS 101", "section": "99X"},
        {"semester": "Fall 2020", "course": "CS 101"},
    ],
)
async def test_unknown_targets_are_not_watchable(client, store, body):
    async with client:
        response = await client.post("/miniapp/watches", json=body, headers=auth())

    assert response.status_code == 404
    assert store.list_subscriptions(USER_ID) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, [], {"semester": 1, "course": "CS 101"}])
async def test_malformed_bodies_are_rejected(client, body):
    async with client:
        response = await client.post("/miniapp/watches", json=body, headers=auth())

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_requests_without_valid_init_data_are_unauthorized(client, store):
    async with client:
        missing = await client.get("/miniapp/watches", headers={"Origin": ORIGIN})
        forged = await client.post(
            "/miniapp/watches", json=COURSE, headers=auth(tamper=True)
        )
        expired = await client.get(
            "/miniapp/watches", headers=auth(auth_date=int(time.time()) - 90_000)
        )

    assert [r.status_code for r in (missing, forged, expired)] == [401, 401, 401]
    assert store.list_subscriptions(USER_ID) == []


@pytest.mark.asyncio
async def test_only_the_site_origin_may_call_the_api(client, store):
    async with client:
        foreign = await client.post(
            "/miniapp/watches",
            json=COURSE,
            headers={**auth(), "Origin": "https://evil.example.test"},
        )
        preflight = await client.options(
            "/miniapp/watches",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        rejected_preflight = await client.options(
            "/miniapp/watches",
            headers={
                "Origin": "https://evil.example.test",
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed = await client.get("/miniapp/watches", headers=auth())

    assert foreign.status_code == 403
    assert store.list_subscriptions(USER_ID) == []
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert rejected_preflight.status_code == 400
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
