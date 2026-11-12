import asyncio
import time

import httpx
import pytest
from bot.repository import Repository
from bot.service import BotService
from bot.settings import Settings
from bot.transport import Wamundo, contact_allowed, incoming_poll, incoming_webhook


def test_sender_filter_applies_to_poll_and_webhook():
    settings = Settings(
        allowed_phones=("51906462381",),
        wamundo_account="bot",
        wamundo_webhook_secret="hook",
        wamundo_account_wid="bot-wid",
    )
    poll = {
        "id": "1",
        "account_unique": "bot",
        "reply_to": "+51906462381",
        "message": "hola",
        "created": time.time(),
    }
    assert incoming_poll(poll, settings)["phone"] == "51906462381"
    assert incoming_poll(dict(poll, reply_to="51999999999"), settings) is None
    webhook = {
        "type": "whatsapp",
        "secret": "hook",
        "data": {
            "id": "2",
            "wid": "bot-wid",
            "phone": "+51906462381",
            "message": "hola",
            "timestamp": time.time(),
        },
    }
    assert incoming_webhook(webhook, settings)["phone"] == "51906462381"
    webhook["data"]["phone"] = "51999999999"
    assert incoming_webhook(webhook, settings) is None


def test_empty_test_list_blocks_everyone():
    assert not contact_allowed("51906462381", Settings())
    assert contact_allowed("51906462381", Settings(test_contacts_only=False))
    assert not contact_allowed("group@g.us", Settings(test_contacts_only=False))


def test_env_normalizes_authorized_number(monkeypatch):
    monkeypatch.setenv("BOT_ALLOWED_PHONES", " +51906462381, ")
    monkeypatch.setenv("BOT_TEST_CONTACTS_ONLY", "true")
    settings = Settings.from_env()
    assert settings.allowed_phones == ("51906462381",)
    assert settings.test_contacts_only


def test_cannot_enable_test_sending_with_no_contacts():
    settings = Settings(
        mode="live",
        supabase_url="unused",
        supabase_key="unused",
        tenant_id="unused",
        send_enabled=True,
        wamundo_secret="fake",
        wamundo_account="fake",
    )
    with pytest.raises(ValueError, match="BOT_ALLOWED_PHONES"):
        settings.validate()


def test_transport_never_calls_provider_for_unauthorized_number():
    requests = []

    def mock(request):
        requests.append(request)
        return httpx.Response(200, json={"status": 200, "data": {}})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
            transport = Wamundo(
                Settings(send_enabled=True, allowed_phones=("51906462381",)), client
            )
            assert not await transport.send("51999999999", "no debe salir")
            assert requests == []
            assert await transport.send("51906462381", "prueba simulada")
            assert len(requests) == 1

    asyncio.run(check())


def test_preexisting_queue_cannot_bypass_contact_filter(tmp_path):
    repository = Repository(str(tmp_path / "bot.db"))
    settings = Settings(mode="live", send_enabled=True, allowed_phones=("51906462381",))

    class Engine:
        async def handle(self, *args):
            raise AssertionError("No consultar al cliente ni a la IA")

    class Transport:
        async def send(self, *args):
            raise AssertionError("No enviar")

    service = BotService(settings, repository, Engine(), Transport())
    repository.enqueue("old", "51999999999", "hola")
    asyncio.run(service.process_once())
    assert repository.stats() == {"inbox": {"done": 1}, "outbox": {}}
    repository.enqueue("queued", "51999999999", "pedido")
    repository.complete(repository.claim_batch(), "respuesta vieja", {})
    asyncio.run(service.send_once())
    assert repository.stats()["outbox"] == {"cancelled": 1}
