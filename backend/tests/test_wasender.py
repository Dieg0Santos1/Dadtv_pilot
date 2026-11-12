import asyncio
import time

import httpx
from bot.assistant import RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.engine import Engine
from bot.repository import Repository
from bot.service import BotService
from bot.settings import Settings
from bot.wasender import WaSender, incoming_webhook
from fastapi.testclient import TestClient
from main import create_app


def payload(phone="51906462381", from_me=False, event="messages.received"):
    return {
        "event": event,
        "timestamp": time.time(),
        "data": {
            "messages": {
                "key": {
                    "id": "wa-1",
                    "fromMe": from_me,
                    "remoteJid": f"{phone}@s.whatsapp.net",
                    "cleanedSenderPn": phone,
                },
                "messageBody": "Hola, quiero Disney",
                "message": {"conversation": "Hola, quiero Disney"},
            }
        },
    }


def test_wasender_ingress_filters_sender_origin_and_event():
    settings = Settings(wasender_webhook_secret="hook", allowed_phones=("51906462381",))
    assert incoming_webhook(payload(), "hook", settings)["phone"] == "51906462381"
    assert incoming_webhook(payload("51999999999"), "hook", settings) is None
    assert incoming_webhook(payload(from_me=True), "hook", settings) is None
    assert incoming_webhook(payload(event="message.sent"), "hook", settings) is None
    group = payload()
    group["data"]["messages"]["key"]["remoteJid"] = "group@g.us"
    assert incoming_webhook(group, "hook", settings) is None
    lid = payload()
    lid["data"]["messages"]["key"].update(remoteJid="12345@lid", cleanedSenderPn="", senderPn="")
    assert incoming_webhook(lid, "hook", settings) is None


def test_wasender_webhook_is_authenticated_and_deduplicated(tmp_path):
    settings = Settings(
        mode="live",
        provider="wasender",
        database_path=str(tmp_path / "db"),
        wasender_webhook_secret="hook",
        wasender_account_phone="51900000001",
        admin_token="test-admin",
        allowed_phones=("51906462381",),
        debounce_seconds=1000,
    )
    repository = Repository(settings.database_path)
    service = BotService(
        settings,
        repository,
        Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant()),
        WaSender(settings),
    )
    with TestClient(create_app(settings, service)) as client:
        assert client.post("/webhook/wasender", json=payload()).status_code == 401
        headers = {"X-Webhook-Signature": "hook"}
        first = client.post("/webhook/wasender", json=payload(), headers=headers)
        assert first.status_code == 200 and first.json()["duplicate"] is False
        second = client.post("/webhook/wasender", json=payload(), headers=headers)
        assert second.status_code == 200 and second.json()["duplicate"] is True
        assert client.post(
            "/webhook/wasender", json=payload("51999999999"), headers=headers
        ).json()["ignored"]
        diagnostics = client.get(
            "/admin/status", headers={"Authorization": "Bearer test-admin"}
        ).json()["wasender_webhook"]
        assert diagnostics["last_result"] == "phone_not_allowed"
        assert diagnostics["counts"] == {
            "unauthorized": 1,
            "accepted": 1,
            "duplicate": 1,
            "phone_not_allowed": 1,
        }
    assert repository.stats()["inbox"] == {"pending": 1}


def test_wasender_send_uses_session_token_and_allowlist():
    seen = []

    def mock(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True, "data": {"status": "in_progress"}})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
            sender = WaSender(
                Settings(
                    provider="wasender",
                    send_enabled=True,
                    wasender_token="local-test-token",
                    allowed_phones=("51906462381",),
                ),
                client,
            )
            assert not await sender.send("51999999999", "no sale")
            assert not seen
            assert await sender.send("51906462381", "prueba")
            assert seen[0].headers["Authorization"] == "Bearer local-test-token"
            assert b'"to":"+51906462381"' in seen[0].content

    asyncio.run(check())
