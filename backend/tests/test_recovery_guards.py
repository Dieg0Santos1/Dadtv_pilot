import asyncio
from pathlib import Path

import yaml
from bot.assistant import RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.domain import affirmative
from bot.engine import Engine
from bot.repository import Repository
from bot.settings import Settings
from bot.transport import valid_secret


def test_failed_confirmation_blocks_later_messages_until_review(tmp_path):
    repository = Repository(str(tmp_path / "db"), max_attempts=1)
    repository.enqueue("confirmation", "51912345678", "confirmo")
    repository.fail_batch(repository.claim_batch())
    repository.enqueue("next", "51912345678", "otro pedido")
    assert repository.claim_batch() == []
    repository.retry_failed()
    assert [row["provider_id"] for row in repository.claim_batch()] == ["confirmation"]


def test_repeated_confirmation_in_a_batch_purchases_once():
    commerce = DemoCommerce()
    engine = Engine(Settings(), DemoCatalog(), commerce, RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "netflix", False, {}, [], "quote")
        result = await engine.handle(
            "51912345678", "confirmo / confirmo", False, quote.state, [], "confirm"
        )
        assert "SIMULADA" in result.text
        assert len(commerce.results) == 1

    asyncio.run(check())
    assert not affirmative("confirmo / no")


def test_unicode_secret_is_rejected_without_exception():
    assert not valid_secret("ñ", "secret")


def test_renewal_uses_existing_sale_and_is_idempotent():
    commerce = DemoCommerce()
    engine = Engine(Settings(), DemoCatalog(), commerce, RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "netflix", False, {}, [], "quote")
        renewal = await engine.handle(
            "51912345678", "quiero renovar netflix", False, quote.state, [], "renew"
        )
        assert "Renovar Netflix" in renewal.text and renewal.state["pending"] is None
        confirmed = await engine.handle(
            "51912345678", "confirmo", False, renewal.state, [], "confirm"
        )
        assert "SIMULADA" in confirmed.text and "Netflix" in confirmed.text
        assert len(commerce.results) == 1
        assert "sales" not in next(iter(commerce.results.values()))
        repeated = commerce.renew(renewal.state["pending_renewal"]["order_id"], "51912345678", renewal.state["pending_renewal"])
        assert repeated == next(iter(commerce.results.values()))
        assert len(commerce.results) == 1

    asyncio.run(check())


def test_renewal_does_not_charge_unavailable_duration():
    commerce = DemoCommerce()
    engine = Engine(Settings(), DemoCatalog(), commerce, RuleAssistant())

    async def check():
        offer = await engine.handle("51912345678", "renovar Netflix por 3 meses", False, {}, [], "renew")
        assert "1 mes" in offer.text
        assert not offer.state.get("pending_renewal")
        confirmation = await engine.handle("51912345678", "confirmo", False, offer.state, [], "confirm")
        assert "Primero elige" in confirmation.text
        assert not commerce.results

    asyncio.run(check())


def test_new_product_intent_cancels_old_renewal_quote():
    commerce = DemoCommerce()
    engine = Engine(Settings(), DemoCatalog(), commerce, RuleAssistant())

    async def check():
        renewal = await engine.handle("51912345678", "quiero renovar netflix", False, {}, [], "renew")
        assert renewal.state.get("pending_renewal")
        new_intent = await engine.handle("51912345678", "quiero comprar disney", False, renewal.state, [], "buy")
        assert not new_intent.state.get("pending_renewal")
        assert not commerce.results

    asyncio.run(check())


def test_render_blueprint_preserves_queue_and_disables_real_operations():
    blueprint = yaml.safe_load(
        (Path(__file__).parents[2] / "render.yaml").read_text(encoding="utf-8")
    )
    service = blueprint["services"][0]
    values = {var["key"]: var.get("value") for var in service["envVars"]}
    assert service["disk"]["mountPath"] == "/var/data"
    assert values["BOT_DATABASE_PATH"].startswith("/var/data/")
    assert values["BOT_SEND_ENABLED"] == values["BOT_COMMERCE_ENABLED"] == values["BOT_RENEWAL_ENABLED"] == "false"
    assert "--workers 1" in service["startCommand"] and "--no-access-log" in service["startCommand"]
