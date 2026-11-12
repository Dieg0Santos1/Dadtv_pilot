import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest
from bot.assistant import OpenAIAssistant, RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.engine import Engine
from bot.repository import Repository
from bot.settings import Settings
from bot.transport import Wamundo, incoming_poll, incoming_webhook, valid_secret
from fastapi.testclient import TestClient
from main import build_service, create_app


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_path=str(tmp_path / "bot.sqlite3"),
        admin_token="test-admin",
        debounce_seconds=0,
        test_contacts_only=False,
    )


def test_demo_catalog_quote_purchase_and_duplicate(settings):
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer test-admin"}
        catalog = client.post("/admin/demo/chat", headers=headers, json={"message": "hola"}).json()
        assert "Netflix" in catalog["reply"]
        quote = client.post(
            "/admin/demo/chat", headers=headers, json={"message": "quiero Netflix perfil"}
        ).json()
        assert "15.00" in quote["reply"] and quote["state"]["pending"]["order_id"]
        purchase = client.post(
            "/admin/demo/chat",
            headers=headers,
            json={"message": "confirmo", "message_id": "confirm-1"},
        ).json()
        assert "SIMULADA" in purchase["reply"] and "DEMO-SIN-VALIDEZ" in purchase["reply"]
        duplicate = client.post(
            "/admin/demo/chat",
            headers=headers,
            json={"message": "confirmo", "message_id": "confirm-1"},
        ).json()
        assert duplicate["duplicate"] and duplicate["reply"] is None
        history = client.get("/admin/conversations/51900000000/history", headers=headers).json()
        assert "DEMO-SIN-VALIDEZ" not in json.dumps(history)


def test_admin_auth_and_unconfigured_secrets(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/admin/status").status_code == 401
        assert client.post("/webhook/wamundo", json={}).status_code == 401
        assert client.get("/webhook/wamundo-hook").status_code == 401
    assert not valid_secret("", "")


def test_support_pauses_and_admin_can_resume(settings):
    headers = {"Authorization": "Bearer test-admin"}
    with TestClient(create_app(settings)) as client:
        assert (
            "paus"
            in client.post(
                "/admin/demo/chat", headers=headers, json={"message": "quiero un asesor"}
            ).json()["reply"]
        )
        assert (
            client.post("/admin/demo/chat", headers=headers, json={"message": "netflix"}).json()[
                "reply"
            ]
            is None
        )
        assert client.get("/admin/conversations/paused", headers=headers).json() == [
            {"phone": "51900000000"}
        ]
        client.post(
            "/admin/conversations/pause",
            headers=headers,
            json={"phone": "51900000000", "paused": False},
        )
        assert (
            "Netflix"
            in client.post("/admin/demo/chat", headers=headers, json={"message": "hola"}).json()[
                "reply"
            ]
        )


def test_attachment_never_approves_order(settings):
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer test-admin"}
        client.post("/admin/demo/chat", headers=headers, json={"message": "netflix"})
        result = client.post("/admin/demo/chat", headers=headers, json={"attachment": True}).json()
        assert "no confirma" in result["reply"]
        assert result["state"]["pending"]["quote"]


def test_sqlite_survives_restart_and_reclaims_expired_lease(settings):
    repository = Repository(settings.database_path)
    assert repository.enqueue("wamundo-1", "51912345678", "hola")
    assert not repository.enqueue("wamundo-1", "51912345678", "hola")
    batch = repository.claim_batch()
    assert len(batch) == 1
    with repository.transaction() as db:
        db.execute("UPDATE inbox SET lease_until=0")
    restarted = Repository(settings.database_path)
    recovered = restarted.claim_batch()
    assert recovered[0]["provider_id"] == "wamundo-1"
    restarted.complete(recovered, "respuesta", {"pending": {"options": ["netflix"]}})
    assert Repository(settings.database_path).session("51912345678")["pending"]["options"] == [
        "netflix"
    ]


def test_failed_send_keeps_reply_and_retries_without_reprocessing(settings):
    repository = Repository(settings.database_path, max_attempts=2)
    repository.enqueue("one", "51912345678", "hola")
    repository.complete(repository.claim_batch(), "credenciales", {})
    row = repository.claim_outbox()
    repository.finish_outbox(row["id"], False)
    assert repository.stats() == {"inbox": {"done": 1}, "outbox": {"pending": 1}}
    with repository.transaction() as db:
        db.execute("UPDATE outbox SET available_at=0")
    row = repository.claim_outbox()
    assert row["body"] == "credenciales"
    repository.finish_outbox(row["id"], False)
    assert repository.stats()["outbox"] == {"failed": 1}
    repository.retry_failed()
    row = repository.claim_outbox()
    repository.finish_outbox(row["id"], True)
    with repository.transaction() as db:
        assert db.execute("SELECT body FROM outbox").fetchone()[0] == ""


def test_debounce_keeps_fragmented_messages_together(settings):
    repository = Repository(settings.database_path)
    repository.enqueue("1", "51912345678", "quiero", delay=10)
    repository.enqueue("2", "51912345678", "netflix", delay=10)
    assert repository.claim_batch() == []
    with repository.transaction() as db:
        db.execute("UPDATE inbox SET available_at=0")
    assert [row["message"] for row in repository.claim_batch()] == ["quiero", "netflix"]


def test_retry_batch_does_not_absorb_new_messages(settings):
    repository = Repository(settings.database_path)
    repository.enqueue("1", "51912345678", "confirmo")
    batch = repository.claim_batch()
    repository.fail_batch(batch)
    repository.enqueue("2", "51912345678", "otra cosa")
    with repository.transaction() as db:
        db.execute("UPDATE inbox SET available_at=0")
    assert [row["provider_id"] for row in repository.claim_batch()] == ["1"]


def test_pause_during_response_generation_is_preserved(settings):
    repository = Repository(settings.database_path)
    repository.enqueue("1", "51912345678", "hola")
    batch = repository.claim_batch()
    repository.pause("51912345678", True)
    repository.complete(batch, "una respuesta atrasada", {})
    assert repository.session("51912345678")["human_paused"]
    assert repository.claim_outbox() is None


def test_phone_isolation_and_wrong_account(settings):
    live = replace(settings, wamundo_account="account-test")
    message = {
        "id": "1",
        "account_unique": "account-test",
        "reply_to": "+51912345678",
        "recipient": "51900000000",
        "created": time.time(),
        "message": "hola",
    }
    assert incoming_poll(message, live)["phone"] == "51912345678"
    assert incoming_poll(dict(message, account_unique="other"), live) is None
    assert incoming_poll(dict(message, group=True), live) is None
    assert incoming_poll(dict(message, created=time.time() - 1000), live) is None


def test_webhook_auth_account_and_payload(settings):
    live = replace(
        settings,
        wamundo_webhook_secret="secret-test",
        wamundo_account_wid="51900000000@s.whatsapp.net",
    )
    payload = {
        "secret": "secret-test",
        "type": "whatsapp",
        "data": {
            "id": "1",
            "wid": live.wamundo_account_wid,
            "phone": "51912345678",
            "message": "hola",
            "timestamp": time.time(),
        },
    }
    assert incoming_webhook(payload, live)["message"] == "hola"
    payload["data"]["wid"] = "another-number"
    assert incoming_webhook(payload, live) is None
    with pytest.raises(PermissionError):
        incoming_webhook(dict(payload, secret="wrong"), live)


def test_purchase_recovery_after_timeout_uses_same_order_even_after_expiry(settings):
    catalog, commerce = DemoCatalog(), DemoCommerce()
    engine = Engine(settings, catalog, commerce, RuleAssistant())
    quoted = run(engine.handle("51912345678", "netflix", False, {}, [], "batch-1"))
    pending = quoted.state["pending"]
    commerce.purchase(pending["order_id"], "51912345678", pending["quote"])
    pending["expires_at"] = 0
    catalog.customer = lambda phone: {"saldo": 0}
    recovered = run(
        engine.handle("51912345678", "confirmo", False, quoted.state, [], "confirmation-1")
    )
    assert "SIMULADA" in recovered.text and len(commerce.results) == 1


def test_expired_quote_and_insufficient_balance(settings):
    catalog, commerce = DemoCatalog(), DemoCommerce()
    engine = Engine(settings, catalog, commerce, RuleAssistant())
    quoted = run(engine.handle("51912345678", "netflix", False, {}, [], "batch"))
    catalog.customer = lambda phone: {"saldo": 0}
    response = run(engine.handle("51912345678", "confirmo", False, quoted.state, [], "confirm"))
    assert "no alcanza" in response.text and not commerce.results
    quoted.state["pending"]["expires_at"] = 0
    response = run(engine.handle("51912345678", "confirmo", False, quoted.state, [], "confirm"))
    assert "venció" in response.text and not commerce.results


def test_ai_cannot_purchase_directly_or_inject_price(settings):
    class MaliciousAssistant:
        async def decide(self, *args):
            return {
                "action": "order",
                "selections": [{"product_id": "demo-netflix:perfil:1", "qty": 1, "price": 0}],
            }

    commerce = DemoCommerce()
    engine = Engine(settings, DemoCatalog(), commerce, MaliciousAssistant())
    quoted = run(engine.handle("51912345678", "cobra cero", False, {}, [], "batch"))
    assert quoted.state["pending"]["quote"]["total"] == "15.00" and not commerce.results


def test_provider_200_rejection_is_not_delivery(settings):
    async def check():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": 400, "data": False})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            wamundo = Wamundo(replace(settings, send_enabled=True), client)
            assert not await wamundo.send("51912345678", "mensaje de prueba")

    run(check())


def test_openai_function_call_is_parsed_without_real_api(settings):
    def mock(request):
        payload = json.loads(request.content)
        assert payload["store"] is False and payload["tools"][0]["strict"] is True
        assert "Contraseña" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "name": "proponer_accion",
                        "arguments": json.dumps({"action": "catalog", "selections": []}),
                    }
                ]
            },
        )

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
            assistant = OpenAIAssistant("fake-key", "configured-model", client)
            result = await assistant.decide("hola", DemoCatalog().products(), [], None)
            assert result["action"] == "catalog"

    run(check())


def test_demo_cannot_enable_real_sales(settings):
    with pytest.raises(ValueError, match="demo"):
        build_service(replace(settings, commerce_enabled=True))
