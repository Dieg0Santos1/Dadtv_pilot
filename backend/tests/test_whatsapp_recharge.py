import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from bot.assistant import RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.domain import BusinessError
from bot.engine import Engine
from bot.recharge import WhatsAppRecharge
from bot.repository import Repository
from bot.settings import Settings, legacy_service_role_key
from bot.wasender import incoming_webhook


class Catalog:
    def customer(self, phone):
        return {"id": "customer-id"} if phone == "51900000000" else None


def settings():
    return SimpleNamespace(
        tenant_id="tenant-id", wasender_token="token", supabase_url="https://example.supabase.co",
        supabase_key="service-key", wasender_webhook_secret="hook", max_message_age=600,
        test_contacts_only=True, allowed_phones=("51900000000",),
    )


def test_webhook_persists_image_and_deduplicates(tmp_path, monkeypatch):
    import time

    payload = {"event": "messages.received", "timestamp": time.time(), "data": {"messages": {
        "key": {"id": "wa-1", "fromMe": False, "remoteJid": "51900000000@s.whatsapp.net"},
        "message": {"imageMessage": {"url": "https://encrypted.example/file", "mediaKey": "key", "mimetype": "image/png"}},
        "messageBody": "",
    }}}
    parsed = incoming_webhook(payload, "hook", settings())
    assert parsed["image"]["mimetype"] == "image/png"
    repo = Repository(tmp_path / "bot.sqlite3")
    assert repo.enqueue(**parsed)
    assert not repo.enqueue(**parsed)
    batch = repo.claim_batch()
    assert json.loads(batch[0]["image_json"])["mediaKey"] == "key"
    repo.complete(batch, "ok", {})
    with repo.transaction() as db:
        assert db.execute("select image_json from inbox").fetchone()[0] is None


@pytest.mark.parametrize("media_path", ["/api/decrypted-media/wa-1", "/decrypted-media/wa-1"])
def test_receipt_uses_verifier_without_credentials_in_reply(media_path):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/decrypt-media":
            return httpx.Response(200, json={"publicUrl": f"https://www.wasenderapi.com{media_path}"})
        if request.url.path == media_path:
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nimage")
        if request.url.path.startswith("/storage/v1/object/receipts/"):
            return httpx.Response(200, json={})
        if request.url.path == "/functions/v1/verify-whatsapp-receipt":
            assert request.headers["authorization"] == "Bearer service-key"
            assert json.loads(request.content)["amountDeclared"] == "20.00"
            assert json.loads(request.content)["phone"] == "51900000000"
            return httpx.Response(200, json={"ok": True, "status": "pending"})
        raise AssertionError(request.url)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WhatsAppRecharge(settings(), Catalog(), client).process(
                "51900000000",
                {"provider_id": "wa-1", "image_json": json.dumps({
                    "url": "https://encrypted.example/file", "mediaKey": "key", "mimetype": "image/png"})},
                "20.00",
            )

    answer = asyncio.run(run())
    assert "pendiente" in answer
    assert len(calls) == 4


def test_rejects_untrusted_decrypted_media_url():
    async def handler(request):
        return httpx.Response(200, json={"publicUrl": "https://attacker.example/receipt"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await WhatsAppRecharge(settings(), Catalog(), client).process(
                "51900000000",
                {"provider_id": "wa-1", "image_json": json.dumps({
                    "url": "https://encrypted.example/file", "mediaKey": "key", "mimetype": "image/png"})},
                "20.00",
            )

    with pytest.raises(BusinessError, match="dirección válida"):
        asyncio.run(run())


def test_rejected_image_allows_customer_to_retry_without_restarting():
    def handler(request):
        if request.url.path == "/api/decrypt-media":
            return httpx.Response(200, json={"publicUrl": "https://www.wasenderapi.com/decrypted-media/wa-1"})
        if request.url.path == "/decrypted-media/wa-1":
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nimage")
        if request.url.path.startswith("/storage/v1/object/receipts/"):
            return httpx.Response(200, json={})
        if request.url.path == "/functions/v1/verify-whatsapp-receipt":
            return httpx.Response(200, json={"ok": False, "status": "rejected", "reason": "not_receipt", "error": "La imagen no parece ser un comprobante de pago."})
        raise AssertionError(request.url)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await WhatsAppRecharge(settings(), Catalog(), client).process(
                "51900000000",
                {"provider_id": "wa-1", "image_json": json.dumps({
                    "url": "https://encrypted.example/file", "mediaKey": "key", "mimetype": "image/png"})},
                "20.00",
            )

    with pytest.raises(BusinessError, match="no parece ser un comprobante"):
        asyncio.run(run())


def test_chat_collects_amount_before_receipt():
    engine = Engine(
        Settings(whatsapp_recharge_enabled=True, recharge_yape_phone="900000001",
                 recharge_receiver="Elmer San*"),
        DemoCatalog(), DemoCommerce(), RuleAssistant(),
    )

    async def run():
        first = await engine.handle("51900000000", "quiero recargar", False, {}, [], "first")
        second = await engine.handle("51900000000", "20", False, first.state, [], "second")
        return first, second

    first, second = asyncio.run(run())
    assert "900000001" in first.text
    assert second.state["recharge_amount"] == "20.00"
    assert "captura" in second.text


def test_recharge_requires_actual_service_role_key(tmp_path):
    def token(role):
        payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
        return f"header.{payload}.signature"

    assert not legacy_service_role_key(token("anon"))
    assert legacy_service_role_key(token("service_role"))
    with pytest.raises(ValueError, match="no la anon"):
        Settings(
            mode="live", provider="wasender", database_path=str(tmp_path / "db"),
            supabase_url="https://example.supabase.co", supabase_key=token("anon"),
            tenant_id="tenant", wasender_webhook_secret="hook",
            wasender_account_phone="51900000001", test_contacts_only=True,
            allowed_phones=("51900000000",), whatsapp_recharge_enabled=True,
        ).validate()


def test_real_renewal_cannot_be_enabled_before_commerce():
    with pytest.raises(ValueError, match="BOT_COMMERCE_ENABLED"):
        Settings(renewal_enabled=True).validate()
