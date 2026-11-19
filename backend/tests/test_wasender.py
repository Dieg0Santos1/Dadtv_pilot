import asyncio
import time

import httpx
import pytest
from bot.assistant import RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.engine import Engine
from bot.repository import Repository
from bot.service import BotService
from bot.settings import Settings
from bot.wasender import WaSender, incoming_webhook, outgoing_control
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


def test_outgoing_owner_command_is_explicit_and_authenticated():
    settings = Settings(wasender_webhook_secret="hook")
    sent = {
        "event": "message.sent", "timestamp": time.time(),
        "data": {"key": {"id": "manual-1", "fromMe": True,
                         "remoteJid": "51906462381@s.whatsapp.net"},
                 "message": {"conversation": "/bot off"}, "success": True},
    }
    assert outgoing_control(sent, "hook", settings) == ("51906462381", True)
    sent["data"]["message"]["conversation"] = "/bot on"
    assert outgoing_control(sent, "hook", settings) == ("51906462381", False)
    sent["data"]["message"]["conversation"] = "Hola, soy tu asesor"
    assert outgoing_control(sent, "hook", settings) is None
    sent["data"]["key"]["fromMe"] = False
    sent["data"]["message"]["conversation"] = "/bot off"
    assert outgoing_control(sent, "hook", settings) is None
    sent["data"]["key"]["fromMe"] = True
    sent["data"]["key"]["remoteJid"] = "group@g.us"
    assert outgoing_control(sent, "hook", settings) is None
    with pytest.raises(PermissionError):
        outgoing_control(sent, "wrong-secret", settings)


def test_outgoing_flat_wasender_payload_and_old_event():
    settings = Settings(wasender_webhook_secret="hook")
    sent = {"event": "message.sent", "timestamp": time.time(),
            "data": {"id": "manual-2", "to": "+51906462381", "status": "sent",
                     "content": "/bot off"}}
    assert outgoing_control(sent, "hook", settings) == ("51906462381", True)
    sent["timestamp"] -= settings.max_message_age + 60
    assert outgoing_control(sent, "hook", settings) is None


def test_outgoing_upsert_from_whatsapp_app_is_owner_control_only():
    settings = Settings(wasender_webhook_secret="hook")
    message = {
        "key": {"id": "app-1", "fromMe": True,
                "remoteJid": "51906462381@s.whatsapp.net"},
        "messageBody": "/bot off", "message": {"conversation": "/bot off"},
    }
    event = {"event": "messages.upsert", "timestamp": time.time(),
             "data": {"messages": [message]}}
    assert outgoing_control(event, "hook", settings) == ("51906462381", True)
    message["key"]["fromMe"] = False
    assert outgoing_control(event, "hook", settings) is None
    message["key"]["fromMe"] = True
    message["messageBody"] = "Hola"
    message["message"]["conversation"] = "Hola"
    assert outgoing_control(event, "hook", settings) is None
    event["data"]["messages"].append(message)
    assert outgoing_control(event, "hook", settings) is None


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


def test_owner_can_pause_and_resume_one_chat_without_affecting_others(tmp_path):
    settings = Settings(
        mode="live", provider="wasender", database_path=str(tmp_path / "db"),
        wasender_webhook_secret="hook", wasender_account_phone="51900000001",
        admin_token="test-admin", test_contacts_only=False, debounce_seconds=1000,
    )
    repo = Repository(settings.database_path)
    service = BotService(settings, repo, Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant()), WaSender(settings))
    sent = {"event": "message.sent", "timestamp": time.time(),
            "data": {"key": {"id": "manual-off", "fromMe": True,
                             "remoteJid": "+51906462381"},
                     "message": {"conversation": "/bot off"}, "success": True}}
    with TestClient(create_app(settings, service)) as client:
        headers = {"X-Webhook-Signature": "hook"}
        assert client.post("/webhook/wasender", json=sent, headers=headers).json()["manual_control"] == "paused"
        assert repo.session("51906462381")["human_paused"] is True
        assert not repo.session("51999999999").get("human_paused")
        assert client.post("/webhook/wasender", json=payload("51999999999"), headers=headers).json()["duplicate"] is False
        sent["data"]["key"]["id"] = "manual-on"
        sent["data"]["message"]["conversation"] = "/bot on"
        assert client.post("/webhook/wasender", json=sent, headers=headers).json()["manual_control"] == "resumed"
        assert repo.session("51906462381")["human_paused"] is False


def test_manual_pause_cancels_pending_customer_reply(tmp_path):
    phone = "51906462381"
    settings = Settings(database_path=str(tmp_path / "db"), send_enabled=True,
                        allowed_phones=(phone,))
    repo = Repository(settings.database_path)
    service = BotService(settings, repo, Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant()), object())
    repo.enqueue("hello", phone, "Hola")
    asyncio.run(service.process_once())
    assert repo.stats()["outbox"] == {"pending": 1}
    repo.pause(phone, True)
    assert repo.stats()["outbox"] == {"cancelled": 1}
    assert asyncio.run(service.send_once()) is False


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
