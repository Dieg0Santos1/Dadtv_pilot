import asyncio
import logging

from bot.assistant import RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.engine import Engine
from bot.intent_model import INTENTS, IntentModel
from bot.settings import Settings
from main import build_service

PHONE = "51912345678"


def engine(assistant=None, commerce=None):
    return Engine(
        Settings(), DemoCatalog(), commerce or DemoCommerce(),
        assistant or RuleAssistant(), IntentModel.load(),
    )


def reply(bot, text, state=None):
    return asyncio.run(bot.handle(PHONE, text, False, state or {}, [], "test-batch"))


def test_trained_model_has_six_classes_and_confidence():
    model = IntentModel.load()
    assert set(model.classes) == INTENTS
    examples = {
        "hola": "consulta",
        "quiero netflix": "pedido",
        "no puedo entrar a netflix": "reclamo",
        "venden ropa": "fuera_de_tema",
        "eres un inutil": "ofensivo",
        "test": "spam",
    }
    for text, expected in examples.items():
        result = model.predict(text)
        assert result.intent == expected
        assert result.confidence >= 0.45 and result.margin >= 0.15


def test_model_routes_order_and_complaint_through_existing_flows(caplog):
    bot = engine()
    with caplog.at_level(logging.INFO, logger="dadtv.bot"):
        order = reply(bot, "quiero netflix")
        complaint = reply(bot, "no puedo entrar a netflix")
    assert order.state["pending"]["quote"]["total"] == "15.00"
    assert complaint.state["human_paused"] is True
    assert "atención humana" in complaint.text
    assert "intent_ml=pedido" in caplog.text
    assert "route_ml=reclamo" in caplog.text


def test_model_controls_route_even_if_assistant_disagrees():
    class MisleadingAssistant:
        async def decide(self, text, products, history, pending, customer_context=None):
            if "quiero" in text:
                return {"action": "chat", "selections": [], "product_ids": []}
            return {
                "action": "order", "selections": [{"product_id": products[0].id, "qty": 1}],
                "product_ids": [],
            }

    bot = engine(MisleadingAssistant())
    consultation = reply(bot, "que planes tiene netflix")
    request = reply(bot, "quiero netflix")
    assert not (consultation.state.get("pending") or {}).get("quote")
    assert "Netflix" in consultation.text
    assert "Netflix" in request.text
    assert not (request.state.get("pending") or {}).get("quote")  # el asistente no eligio un plan


def test_low_confidence_asks_clarification_without_commercial_action():
    commerce = DemoCommerce()
    bot = engine(commerce=commerce)
    result = reply(bot, "premium")
    assert "No estoy seguro" in result.text
    assert not result.state.get("pending")
    assert not commerce.results


def test_payment_claim_is_only_a_signal_not_verification():
    commerce = DemoCommerce()
    bot = engine(commerce=commerce)
    result = reply(bot, "ya pague")
    assert "captura" in result.text
    assert "no repitas" in result.text
    assert "acredit" not in result.text.lower()
    assert not commerce.results


def test_service_loads_model_into_live_message_path(tmp_path):
    settings = Settings(database_path=str(tmp_path / "bot.sqlite3"))
    service = build_service(settings)
    assert service.engine.intent_model is not None
    service.repository.enqueue("ml-1", PHONE, "no puedo entrar a netflix")
    asyncio.run(service.process_once())
    assert service.repository.session(PHONE)["human_paused"] is True
    assert service.repository.stats()["outbox"] == {"pending": 1}
