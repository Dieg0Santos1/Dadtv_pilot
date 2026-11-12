import asyncio
import json
import sqlite3
from dataclasses import replace

import httpx
from bot.assistant import RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.domain import Product, money
from bot.engine import Engine
from bot.repository import Repository
from bot.service import BotService
from bot.settings import Settings
from fastapi.testclient import TestClient
from main import create_app


def test_existing_bot_database_adds_alert_deduplication_without_losing_outbox(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE outbox (id INTEGER PRIMARY KEY, account TEXT NOT NULL, "
            "phone TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL, "
            "lease_until REAL NOT NULL DEFAULT 0)"
        )
        db.execute(
            "INSERT INTO outbox(account,phone,body,available_at) VALUES (?,?,?,?)",
            ("demo", "51911111111", "mensaje existente", 0),
        )
    Repository(str(path))
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(outbox)")}
        assert "dedupe_key" in columns
        assert db.execute("SELECT body FROM outbox").fetchone()[0] == "mensaje existente"


def test_full_pipeline_retry_never_runs_purchase_twice(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "db"), send_enabled=True, test_contacts_only=False
    )

    class Transport:
        def __init__(self):
            self.accepted = False
            self.messages = []

        async def send(self, phone, body):
            self.messages.append(body)
            return self.accepted

    transport = Transport()
    commerce = DemoCommerce()
    repository = Repository(settings.database_path)
    service = BotService(
        settings, repository, Engine(settings, DemoCatalog(), commerce, RuleAssistant()), transport
    )

    async def flow():
        repository.enqueue("offer", "51912345678", "netflix")
        await service.process_once()
        transport.accepted = True
        await service.send_once()
        repository.enqueue("confirm", "51912345678", "confirmo")
        await service.process_once()
        assert len(commerce.results) == 1
        transport.accepted = False
        await service.send_once()
        with repository.transaction() as db:
            db.execute("UPDATE outbox SET available_at=0 WHERE status='pending'")
        transport.accepted = True
        await service.send_once()
        assert len(commerce.results) == 1
        assert transport.messages[-1] == transport.messages[-2]
        assert repository.stats()["inbox"] == {"done": 2}

    asyncio.run(flow())


def test_support_alert_is_queued_once_without_exposing_customer_message(tmp_path):
    customer_phone, adviser_phone = "51911111111", "51900000000"
    settings = Settings(
        database_path=str(tmp_path / "db"),
        send_enabled=True,
        allowed_phones=(customer_phone, adviser_phone),
        support_alert_phone=adviser_phone,
    )

    class Transport:
        def __init__(self):
            self.messages = []

        async def send(self, phone, body):
            self.messages.append((phone, body))
            return True

    transport = Transport()
    repository = Repository(settings.database_path)
    service = BotService(
        settings, repository,
        Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant()), transport,
    )

    async def flow():
        repository.enqueue("complaint-1", customer_phone, "Mi cuenta no funciona, dato privado")
        await service.process_once()
        assert repository.stats()["outbox"] == {"pending": 2}
        await service.send_once()
        await service.send_once()
        adviser_messages = [body for phone, body in transport.messages if phone == adviser_phone]
        assert len(adviser_messages) == 1
        assert customer_phone in adviser_messages[0]
        assert "dato privado" not in adviser_messages[0]
        repository.enqueue("complaint-2", customer_phone, "Sigo esperando")
        await service.process_once()
        assert repository.stats()["outbox"] == {"sent": 2}

    asyncio.run(flow())


def test_support_alert_is_not_sent_to_same_customer_phone(tmp_path):
    phone = "51900000000"
    settings = Settings(
        database_path=str(tmp_path / "db"),
        support_alert_phone=phone,
        allowed_phones=(phone,),
    )
    repository = Repository(settings.database_path)
    service = BotService(
        settings, repository,
        Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant()),
        object(),
    )

    async def flow():
        repository.enqueue("complaint-self", phone, "Mi cuenta no funciona")
        await service.process_once()
        assert repository.stats()["outbox"] == {"pending": 1}

    asyncio.run(flow())


def test_paused_chat_does_not_verify_image_or_queue_reply(tmp_path, monkeypatch):
    phone = "51900000000"
    settings = Settings(
        database_path=str(tmp_path / "db"),
        whatsapp_recharge_enabled=True,
    )
    repository = Repository(settings.database_path)
    service = BotService(
        settings,
        repository,
        Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant()),
        object(),
    )

    async def unexpected_verification(*args):
        raise AssertionError("No se debe verificar una captura mientras el chat esta pausado")

    monkeypatch.setattr("bot.recharge.WhatsAppRecharge.process", unexpected_verification)
    repository.pause(phone, True)
    repository.enqueue(
        "paused-image", phone, "", attachment=True,
        image={"mimetype": "image/jpeg", "url": "https://example.invalid/image"},
    )

    asyncio.run(service.process_once())
    assert repository.stats() == {"inbox": {"done": 1}, "outbox": {}}
    assert repository.session(phone)["human_paused"] is True


def test_balance_different_customers_and_cannot_charge_without_confirmation(tmp_path):
    settings = Settings(database_path=str(tmp_path / "db"))

    class Catalog(DemoCatalog):
        def customer(self, phone):
            return {"saldo": 70 if phone.endswith("1") else 5}

    commerce = DemoCommerce()
    engine = Engine(settings, Catalog(), commerce, RuleAssistant())

    async def flow():
        first = await engine.handle("51912345671", "saldo", False, {}, [], "1")
        second = await engine.handle("51912345672", "saldo", False, {}, [], "2")
        assert "70.00" in first.text and "5.00" in second.text
        quote = await engine.handle("51912345671", "netflix", False, {}, [], "3")
        rejected = await engine.handle("51912345671", "no, ok", False, quote.state, [], "4")
        assert "cancelado" in rejected.text and not commerce.results

    asyncio.run(flow())


def test_quote_rounds_for_premium_even_when_discount_is_zero(tmp_path):
    class Catalog(DemoCatalog):
        def customer(self, phone):
            return {"saldo": 100, "premium": True}

        def products(self):
            return [Product("p", "s", "Netflix", "perfil", 1, money("15.50"), 2)]

    engine = Engine(Settings(), Catalog(), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(engine.handle("51912345678", "netflix", False, {}, [], "1"))
    assert reply.state["pending"]["quote"]["total"] == "16.00"


def test_webhook_duplicate_persists_before_acknowledgment(tmp_path):
    settings = Settings(
        mode="live",
        test_contacts_only=False,
        database_path=str(tmp_path / "db"),
        admin_token="admin",
        wamundo_webhook_secret="secret",
        wamundo_account_wid="test-wid",
        poll_seconds=0,
        debounce_seconds=1000,
    )
    repository = Repository(settings.database_path)

    class Transport:
        async def send(self, *args):
            raise AssertionError("No debe enviar")

    service = BotService(
        settings,
        repository,
        Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant()),
        Transport(),
    )
    import time

    payload = {
        "type": "whatsapp",
        "secret": "secret",
        "data": {
            "id": "wm-1",
            "wid": "test-wid",
            "phone": "51912345678",
            "timestamp": time.time(),
            "message": "hola",
        },
    }
    with TestClient(create_app(settings, service)) as client:
        first = client.post("/webhook/wamundo", json=payload)
        assert first.status_code == 200 and not first.json()["duplicate"]
        second = client.post("/webhook/wamundo", json=payload)
        assert second.json()["duplicate"]
        assert repository.stats()["inbox"] == {"pending": 1}
        assert (
            client.post("/webhook/wamundo", json=dict(payload, secret="wrong")).status_code == 401
        )


def test_openai_failure_keeps_incoming_message_for_retry(tmp_path):
    class FailedAssistant:
        async def decide(self, *args):
            raise httpx.ReadTimeout("no response")

    settings = Settings(database_path=str(tmp_path / "db"))
    repository = Repository(settings.database_path)
    service = BotService(
        settings,
        repository,
        Engine(settings, DemoCatalog(), DemoCommerce(), FailedAssistant()),
        None,
    )
    repository.enqueue("wm-1", "51912345678", "hola")
    asyncio.run(service.process_once())
    assert repository.stats() == {"inbox": {"pending": 1}, "outbox": {}}


def test_quote_state_has_no_credentials(tmp_path):
    engine = Engine(Settings(), DemoCatalog(), DemoCommerce(), RuleAssistant())
    quote = asyncio.run(engine.handle("51912345678", "netflix", False, {}, [], "1"))
    assert "contrasena" not in json.dumps(quote.state)


def test_live_commerce_needs_an_active_sender():
    settings = replace(
        Settings(),
        mode="live",
        supabase_url="http://unused",
        supabase_key="test",
        tenant_id="tenant",
        commerce_enabled=True,
    )
    import pytest

    with pytest.raises(ValueError, match="envío"):
        settings.validate()
