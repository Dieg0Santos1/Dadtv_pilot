import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from bot.assistant import OpenAIAssistant, RuleAssistant
from bot.catalog import DemoCatalog
from bot.commerce import DemoCommerce
from bot.conversation import customer_context, expand_template
from bot.domain import Product, money
from bot.engine import Engine
from bot.settings import Settings


class CustomerCatalog(DemoCatalog):
    def __init__(self, balance=25, registered=True):
        self.balance, self.registered = balance, registered
        self.lookups = []

    def customer(self, phone):
        self.lookups.append(phone)
        return (
            {
                "id": "private-client-id",
                "nombre": "Carlos Pérez",
                "telefono": phone,
                "saldo": self.balance,
                "premium": False,
                "correo": "private@example.test",
            }
            if self.registered
            else None
        )


def test_buy_disney_uses_sender_customer_balance_and_stock():
    catalog, commerce = CustomerCatalog(), DemoCommerce()
    engine = Engine(Settings(), catalog, commerce, RuleAssistant())
    reply = asyncio.run(
        engine.handle("51912345678", "Hola, quiero comprar Disney", False, {}, [], "1")
    )
    assert "Hola, Carlos" in reply.text
    assert "Disney+ Premium" in reply.text and "Netflix" not in reply.text
    assert "un perfil" in reply.text and "un mes" in reply.text
    assert "18.00" in reply.text and "25.00" in reply.text and "te alcanza" in reply.text
    assert "¿Te lo activo" in reply.text
    assert catalog.lookups == ["51912345678"] and not commerce.results


def test_shortfall_is_exact_and_customer_not_told_it_can_buy():
    engine = Engine(Settings(), CustomerCatalog(balance=5), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(engine.handle("51912345678", "quiero Disney", False, {}, [], "1"))
    assert "te faltan S/ 13.00" in reply.text and "te alcanza" not in reply.text


def test_confirmed_quote_with_shortfall_gives_wallet_top_up_instructions():
    settings = Settings(
        mode="live",
        commerce_enabled=False,
        whatsapp_recharge_enabled=True,
        recharge_yape_phone="51900000001",
        recharge_receiver="Titular de prueba",
        recharge_min_amount=20,
    )
    commerce = DemoCommerce()
    engine = Engine(settings, CustomerCatalog(balance=5), commerce, RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "quiero Disney", False, {}, [], "1")
        reply = await engine.handle("51912345678", "sí", False, quote.state, [], "2")
        assert "Disney+ Premium" in reply.text
        assert "S/ 18.00" in reply.text and "S/ 20.00" in reply.text
        assert "51900000001" in reply.text and "Titular de prueba" in reply.text
        assert reply.state["recharge_amount"] == "20.00"
        assert not commerce.results

    asyncio.run(check())


def test_ambiguous_yes_preserves_quote_without_buying():
    commerce = DemoCommerce()
    engine = Engine(Settings(), CustomerCatalog(balance=100), commerce, RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "quiero Disney", False, {}, [], "1")
        reply = await engine.handle("51912345678", "Sii", False, quote.state, [], "2")
        assert "confirmas" in reply.text
        assert reply.state["pending"]["order_id"] == quote.state["pending"]["order_id"]
        assert not commerce.results

    asyncio.run(check())


def test_price_followup_does_not_change_chosen_duration():
    engine = Engine(Settings(), CustomerCatalog(balance=100), DemoCommerce(), RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "quiero disney tres meses", False, {}, [], "1")
        question = await engine.handle(
            "51912345678", "y cuánto cuesta?", False, quote.state, [], "2"
        )
        assert question.state["pending"]["quote"]["items"][0]["months"] == 3
        assert question.state["pending"]["quote"]["total"] == "48.00"

    asyncio.run(check())


def test_relative_duration_and_thanks_preserve_conversation():
    commerce = DemoCommerce()
    engine = Engine(Settings(), CustomerCatalog(balance=100), commerce, RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "hola quiero disney", False, {}, [], "1")
        three = await engine.handle(
            "51912345678",
            "mejor el de tres meses",
            False,
            quote.state,
            [{"role": "assistant", "content": quote.text}],
            "2",
        )
        assert "3 meses" in three.text and three.state["pending"]["quote"]["total"] == "48.00"
        assert "Hola" not in three.text
        thanks = await engine.handle(
            "51912345678",
            "gracias",
            False,
            three.state,
            [{"role": "assistant", "content": three.text}],
            "3",
        )
        assert "De nada" in thanks.text
        assert thanks.state["pending"]["order_id"] == three.state["pending"]["order_id"]
        bought = await engine.handle("51912345678", "sí, dale", False, thanks.state, [], "4")
        assert "SIMULADA" in bought.text and len(commerce.results) == 1

    asyncio.run(check())


def test_single_option_selected_from_model_catalog_becomes_quote():
    class CatalogingAssistant:
        async def decide(self, text, products, history, pending, customer_context=None):
            if pending:
                return {
                    "action": "catalog",
                    "product_ids": ["demo-disney:perfil:3"],
                    "selections": [],
                }
            return {
                "action": "catalog",
                "product_ids": ["demo-disney:perfil:1", "demo-disney:perfil:3"],
                "selections": [],
            }

    engine = Engine(Settings(), DemoCatalog(), DemoCommerce(), CatalogingAssistant())

    async def check():
        options = await engine.handle("51912345678", "quiero Disney", False, {}, [], "1")
        assert options.state["pending"]["options"] == [
            "demo-disney:perfil:1",
            "demo-disney:perfil:3",
        ]
        quote = await engine.handle(
            "51912345678", "el de tres meses", False, options.state, [], "2"
        )
        assert quote.state["pending"]["quote"]["total"] == "48.00"
        assert "¿Te lo activo" in quote.text

    asyncio.run(check())


def test_model_cannot_bundle_plans_when_customer_asks_for_one_platform():
    class AppleCatalog(DemoCatalog):
        def products(self):
            return [
                Product("apple:1", "apple", "Apple TV", "cuenta", 1, money("11.70"), 9),
                Product("apple:3", "apple", "Apple TV", "cuenta", 3, money("31.19"), 9),
            ]

    class BundlingAssistant:
        async def decide(self, text, products, history, pending, customer_context=None):
            if pending:
                return {"action": "order", "selections": [{"product_id": "apple:1", "qty": 1}]}
            return {
                "action": "order",
                "selections": [
                    {"product_id": "apple:1", "qty": 1},
                    {"product_id": "apple:3", "qty": 1},
                ],
            }

    engine = Engine(Settings(), AppleCatalog(), DemoCommerce(), BundlingAssistant())

    async def check():
        options = await engine.handle("51912345678", "Quiero Apple TV", False, {}, [], "1")
        assert options.state["pending"]["options"] == ["apple:1", "apple:3"]
        assert "11.70" in options.text and "31.19" in options.text
        assert "42.89" not in options.text
        quote = await engine.handle("51912345678", "el de un mes", False, options.state, [], "2")
        assert quote.state["pending"]["quote"]["total"] == "11.70"
        assert "31.19" not in quote.text

    asyncio.run(check())


def test_natural_options_response_has_only_one_introduction():
    class CatalogAssistant:
        async def decide(self, *args):
            return {"action": "catalog", "product_ids": ["demo-disney:perfil:1", "demo-disney:perfil:3"], "selections": []}

        async def compose(self, event, fragments, customer_context, text, history):
            return "Tengo estas opciones: {options} {wallet} {ask}"

    engine = Engine(Settings(), DemoCatalog(), DemoCommerce(), CatalogAssistant())
    reply = asyncio.run(engine.handle("51912345678", "Quiero Disney", False, {}, [{"role": "user", "content": "hola"}], "1"))
    assert reply.text.count("Tengo estas opciones") == 1
    assert "1 mes" in reply.text and "3 meses" in reply.text


def test_model_cannot_pause_chat_for_an_ordinary_clarification():
    class OvereagerAssistant:
        async def decide(self, *args):
            return {"action": "support", "product_ids": [], "selections": []}

    engine = Engine(Settings(), DemoCatalog(), DemoCommerce(), OvereagerAssistant())
    state = {"pending": {"options": ["demo-disney:perfil:1"], "expires_at": float("inf")}}
    reply = asyncio.run(engine.handle("51912345678", "¿por qué?", False, state, [], "1"))
    assert "aclar" in reply.text
    assert not reply.state.get("human_paused")
    assert reply.state["pending"] == state["pending"]

    complaint = asyncio.run(
        engine.handle("51912345678", "Mi cuenta no funciona", False, state, [], "2")
    )
    assert complaint.state["human_paused"] is True


def test_old_recharge_does_not_capture_a_plan_number_or_survive_cancellation():
    settings = Settings(whatsapp_recharge_enabled=True, recharge_min_amount=20)
    engine = Engine(settings, DemoCatalog(), DemoCommerce(), RuleAssistant())
    state = {
        "pending": {"options": ["demo-disney:perfil:1"], "expires_at": float("inf")},
        "recharge_focus": float("inf"),
        "recharge_amount": "20.00",
    }

    async def check():
        chosen = await engine.handle("51912345678", "1", False, state, [], "1")
        assert chosen.state["pending"]["quote"]["total"] == "18.00"
        assert chosen.state["recharge_focus"] == 0
        assert "recharge_amount" not in chosen.state

        cancelled = await engine.handle("51912345678", "cancelar", False, state, [], "2")
        assert cancelled.state["pending"] is None
        assert cancelled.state["recharge_focus"] == 0
        assert "recharge_amount" not in cancelled.state

    asyncio.run(check())


@pytest.mark.parametrize("action", ["catalog", "unavailable"])
def test_missing_requested_platform_does_not_list_unrelated_products(action):
    class NoDisney(DemoCatalog):
        def products(self):
            return [p for p in super().products() if "Disney" not in p.platform]

    class Assistant:
        async def decide(self, *args):
            return {"action": action, "product_ids": [], "selections": []}

    engine = Engine(Settings(), NoDisney(), DemoCommerce(), Assistant())
    reply = asyncio.run(
        engine.handle("51912345678", "Hola, quiero comprar Disney", False, {}, [], "1")
    )
    assert "no veo disponible" in reply.text
    assert "Netflix" not in reply.text
    assert not reply.state.get("pending")


def test_no_balance_message_does_not_cancel_pending_order():
    engine = Engine(Settings(), CustomerCatalog(balance=5), DemoCommerce(), RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "disney", False, {}, [], "1")
        reply = await engine.handle("51912345678", "no tengo saldo", False, quote.state, [], "2")
        assert (
            "13.00" in reply.text
            and reply.state["pending"]["order_id"] == quote.state["pending"]["order_id"]
        )

    asyncio.run(check())


def test_switch_to_unavailable_product_clears_previous_quote():
    class NetflixOutOfStock(CustomerCatalog):
        def listed_products(self):
            return [
                Product(
                    p.id, p.service_id, p.platform, p.kind, p.months,
                    p.price, 0 if p.platform == "Netflix" else p.stock, p.description,
                )
                for p in super().products()
            ]

    engine = Engine(Settings(), NetflixOutOfStock(balance=100), DemoCommerce(), RuleAssistant())

    async def check():
        quote = await engine.handle("51912345678", "quiero Disney", False, {}, [], "1")
        changed = await engine.handle(
            "51912345678", "mejor Netflix", False, quote.state, [], "2"
        )
        assert "sin stock" in changed.text
        assert changed.state.get("pending") is None

    asyncio.run(check())


def test_switch_to_available_product_replaces_previous_quote():
    engine = Engine(Settings(), CustomerCatalog(balance=100), DemoCommerce(), RuleAssistant())

    async def check():
        first = await engine.handle("51912345678", "quiero Disney", False, {}, [], "1")
        changed = await engine.handle(
            "51912345678", "mejor Netflix", False, first.state, [], "2"
        )
        assert "Netflix" in changed.text
        assert changed.state["pending"]["quote"]["items"][0]["label"].startswith("Netflix")
        assert changed.state["pending"]["order_id"] != first.state["pending"]["order_id"]

    asyncio.run(check())


def test_paid_message_requests_receipt_without_new_payment():
    engine = Engine(
        Settings(whatsapp_recharge_enabled=True),
        CustomerCatalog(), DemoCommerce(), RuleAssistant(),
    )
    reply = asyncio.run(
        engine.handle("51912345678", "Ya pagué", False, {}, [], "1")
    )
    assert "no repitas el Yape" in reply.text
    assert "monto exacto" in reply.text


def test_non_catalog_goods_are_redirected_without_product_menu():
    engine = Engine(Settings(), CustomerCatalog(), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(
        engine.handle("51912345678", "Venden ropa", False, {}, [], "1")
    )
    assert "productos digitales" in reply.text
    assert "Tengo estas opciones" not in reply.text


def test_relative_plan_choice_keeps_only_disney_options():
    class Catalog(CustomerCatalog):
        def products(self):
            return [
                Product(
                    "standard", "disney-standard", "Disney+ Estándar", "perfil", 1, money(12), 2
                ),
                Product("premium", "disney-premium", "Disney+ Premium", "perfil", 1, money(18), 2),
                Product("other", "netflix", "Netflix", "perfil", 1, money(15), 3),
            ]

    engine = Engine(Settings(), Catalog(), DemoCommerce(), RuleAssistant())

    async def check():
        options = await engine.handle("51912345678", "quiero comprar disney", False, {}, [], "1")
        assert "Netflix" not in options.text
        quote = await engine.handle("51912345678", "el premium", False, options.state, [], "2")
        assert quote.state["pending"]["quote"]["items"][0]["product_id"] == "premium"

    asyncio.run(check())


def test_unknown_customer_gets_registration_without_invented_identity():
    engine = Engine(Settings(), CustomerCatalog(registered=False), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(engine.handle("51912345678", "hola quiero disney", False, {}, [], "1"))
    assert (
        "crear" in reply.text and "Carlos" not in reply.text and "billetera, así" not in reply.text
    )


def test_new_contact_can_register_on_site_then_bot_rechecks_account():
    catalog = CustomerCatalog(registered=False)
    engine = Engine(Settings(), catalog, DemoCommerce(), RuleAssistant())

    async def check():
        hello = await engine.handle("51912345678", "hola", False, {}, [], "1")
        assert "Todavía no encuentro una cuenta" in hello.text
        assert "https://dadtvplay.com" in hello.text
        register = await engine.handle(
            "51912345678", "quiero registrarme", False, hello.state, [], "2"
        )
        assert "Ingresar / Registrar" in register.text
        assert "Registrarme" in register.text
        assert "este mismo número" in register.text
        catalog.registered = True
        balance = await engine.handle("51912345678", "saldo", False, register.state, [], "3")
        assert "S/ 25.00" in balance.text
        assert "Todavía no encuentro" not in balance.text

    asyncio.run(check())


def test_registration_confirmation_rechecks_customer_without_keyword_saldo():
    catalog = CustomerCatalog(registered=False)
    engine = Engine(Settings(), catalog, DemoCommerce(), RuleAssistant())

    async def check():
        missing = await engine.handle("51912345678", "ya me registré", False, {}, [], "1")
        assert "Todavía no encuentro" in missing.text
        catalog.registered = True
        found = await engine.handle("51912345678", "ya me registré", False, missing.state, [], "2")
        assert "Ya encuentro tu cuenta" in found.text
        assert "S/ 25.00" in found.text
        assert catalog.lookups == ["51912345678", "51912345678"]

    asyncio.run(check())


def test_published_disney_variants_are_named_but_not_offered_without_stock():
    class PublishedCatalog(CustomerCatalog):
        def listed_products(self):
            return [
                Product("premium", "p", "Disney premium", "cuenta", 1, money(26), 0),
                Product("standard", "s", "Disney standar", "cuenta", 1, money(15.6), 0),
                Product("netflix", "n", "Netflix", "cuenta", 1, money(25), 2),
            ]

    engine = Engine(Settings(), PublishedCatalog(), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(engine.handle("51912345678", "quiero Disney", False, {}, [], "1"))
    assert "Disney premium" in reply.text and "Disney standar" in reply.text
    assert "sin stock" in reply.text and "Netflix" not in reply.text
    assert not reply.state.get("pending")

    comparison = asyncio.run(
        engine.handle(
            "51912345678",
            "¿y cuál es la diferencia?",
            False,
            reply.state,
            [],
            "2",
        )
    )
    assert "diferencia de funciones verificada" in comparison.text
    assert not comparison.state.get("pending")

    premium = asyncio.run(
        engine.handle("51912345678", "el premium", False, comparison.state, [], "3")
    )
    assert "Disney premium" in premium.text and "Disney standar" not in premium.text
    assert not premium.state.get("pending")


def test_unrelated_question_redirects_to_store_without_catalog_spam():
    engine = Engine(Settings(), CustomerCatalog(), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(
        engine.handle("51912345678", "¿cómo estará el clima mañana?", False, {}, [], "1")
    )
    assert "DadTv Store" in reply.text
    assert "Netflix" not in reply.text


def test_plan_comparison_uses_only_distinct_catalog_descriptions():
    class Catalog(CustomerCatalog):
        def listed_products(self):
            return [
                Product("p", "p", "Disney premium", "cuenta", 1, money(26), 0, "Incluye deportes."),
                Product("s", "s", "Disney standar", "cuenta", 1, money(16), 0, "Incluye series."),
            ]

    engine = Engine(Settings(), Catalog(), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(
        engine.handle(
            "51912345678", "diferencia entre Disney premium y standar", False, {}, [], "1"
        )
    )
    assert "Incluye deportes" in reply.text and "Incluye series" in reply.text
    assert not reply.state.get("pending")


def test_hbo_followup_compares_verified_descriptions_without_guessing():
    class Catalog(CustomerCatalog):
        def listed_products(self):
            return [
                Product(
                    "base", "base", "Hbo max", "cuenta", 1, money(10), 2, "Películas y series."
                ),
                Product(
                    "platino",
                    "platino",
                    "Hbo max platino",
                    "cuenta",
                    1,
                    money(18),
                    2,
                    "Incluye 4K.",
                ),
            ]

    engine = Engine(Settings(), Catalog(), DemoCommerce(), RuleAssistant())

    async def check():
        options = await engine.handle("51912345678", "Tienes disponible HBO?", False, {}, [], "1")
        comparison = await engine.handle(
            "51912345678",
            "cuál es la diferencia entre platino y no platino?",
            False,
            options.state,
            [],
            "2",
        )
        assert "Películas y series" in comparison.text and "Incluye 4K" in comparison.text
        assert "suele" not in comparison.text

    asyncio.run(check())


def test_recharge_question_and_followup_give_existing_store_steps():
    settings = Settings(recharge_yape_phone="900000001", recharge_receiver="Elmer San*")
    engine = Engine(settings, CustomerCatalog(balance=0), DemoCommerce(), RuleAssistant())

    async def check():
        first = await engine.handle(
            "51912345678", "No tengo saldo, ¿cómo puedo recargar?", False, {}, [], "1"
        )
        assert "900000001" in first.text and "Elmer San*" in first.text
        assert "Recarga automática" in first.text and "no valida capturas" in first.text
        followup = await engine.handle(
            "51912345678", "¿cuáles son los pasos?", False, first.state, [], "2"
        )
        assert "900000001" in followup.text
        attachment = await engine.handle("51912345678", "", True, followup.state, [], "3")
        assert "no confirma" in attachment.text and "no valida capturas" in attachment.text

    asyncio.run(check())


def test_minimal_customer_context_contains_no_private_identifiers():
    context = customer_context(CustomerCatalog().customer("51912345678"))
    assert context == {"registered": True, "name": "Carlos", "balance": "25.00", "premium": False}
    assert "private" not in json.dumps(context) and "519" not in json.dumps(context)


@pytest.mark.parametrize(
    "template",
    [
        "¡Hola! {offer}",
        "{offer} {wallet} {ask} {ask}",
        "{offer} {wallet} {ask} Solo S/ 1",
        "{offer} {wallet} {ask} Ya está activado",
        "{offer} {wallet} {ask} https://otro.test",
        "{offer} {wallet} {ask} ¿Confirmas la compra?",
    ],
)
def test_model_cannot_omit_confirmation_or_add_new_amounts(template):
    with pytest.raises(ValueError):
        expand_template(
            template, {"offer": "Disney S/ 18", "wallet": "Saldo S/ 25", "ask": "¿Compramos?"}
        )


def test_generative_wording_uses_verified_fragments_and_never_receives_credentials():
    def mock(request):
        payload = json.loads(request.content)
        data = json.loads(payload["input"][0]["content"])
        assert data["customer_context"]["name"] == "Carlos"
        assert "private-client-id" not in request.content.decode()
        if "tools" in payload:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "name": "proponer_accion",
                            "arguments": json.dumps(
                                {
                                    "action": "order",
                                    "selections": [
                                        {"product_id": "demo-disney:perfil:1", "qty": 1}
                                    ],
                                    "product_ids": [],
                                }
                            ),
                        }
                    ]
                },
            )
        assert payload["text"]["format"]["strict"]
        assert "contrasena" not in data
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "template": "{greeting} Claro, te ayudo 😊 {offer} {wallet} {ask}"
                                    }
                                ),
                            }
                        ]
                    }
                ]
            },
        )

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
            assistant = OpenAIAssistant("fake", "test-model", client)
            commerce = DemoCommerce()
            engine = Engine(Settings(), CustomerCatalog(), commerce, assistant)
            reply = await engine.handle("51912345678", "hola quiero disney", False, {}, [], "1")
            assert (
                "Claro, te ayudo" in reply.text and "18.00" in reply.text and "25.00" in reply.text
            )
            bought = await engine.handle("51912345678", "sí", False, reply.state, [], "2")
            assert "SIMULADA" in bought.text  # No llamada al modelo para redactar credenciales.

    asyncio.run(check())


def test_failed_wording_falls_back_without_losing_quote():
    class Assistant(RuleAssistant):
        async def compose(self, *args):
            raise httpx.ReadTimeout("test")

    engine = Engine(Settings(), CustomerCatalog(), DemoCommerce(), Assistant())
    reply = asyncio.run(engine.handle("51912345678", "disney", False, {}, [], "1"))
    assert "18.00" in reply.text and reply.state["pending"]["quote"]


def test_available_options_show_actual_premium_price():
    class Catalog(CustomerCatalog):
        def customer(self, phone):
            return {"nombre": "Carlos", "saldo": 100, "premium": True}

        def discount(self, customer):
            return Decimal(10)

        def products(self):
            return [
                Product("a", "a", "Disney+ Estándar", "perfil", 1, money(15), 2),
                Product("b", "b", "Disney+ Premium", "perfil", 1, money(20), 2),
            ]

    engine = Engine(Settings(), Catalog(), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(engine.handle("51912345678", "disney", False, {}, [], "1"))
    assert "14.00" in reply.text and "18.00" in reply.text


def test_information_comes_from_catalog_description():
    class Catalog(CustomerCatalog):
        def products(self):
            return [
                Product(
                    "p",
                    "s",
                    "Disney",
                    "perfil",
                    1,
                    money(18),
                    2,
                    "Incluye un perfil; uso según las condiciones del plan.",
                )
            ]

    engine = Engine(Settings(), Catalog(), DemoCommerce(), RuleAssistant())
    reply = asyncio.run(engine.handle("51912345678", "qué incluye disney", False, {}, [], "1"))
    assert "Incluye un perfil" in reply.text and not reply.state.get("pending")
