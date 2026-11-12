from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pglast
import pytest
from bot.catalog import DemoCatalog, StoreCatalog, paged
from bot.commerce import StoreCommerce, quote_products
from bot.domain import BusinessError, affirmative, negative, phone_number
from conftest import FakeSupabase


def test_real_stock_excludes_full_sales_profiles_and_expired_accounts(supabase_fixture):
    products = StoreCatalog(supabase_fixture, "owner").products()
    assert {p.stock for p in products if p.kind == "cuenta"} == {1}
    assert {p.stock for p in products if p.kind == "perfil"} == {2}
    assert not any(p.months == 6 for p in products)
    assert next(p.price for p in products if p.kind == "perfil" and p.months == 3) == Decimal(
        "40.00"
    )
    assert next(p.price for p in products if p.kind == "perfil" and p.months == 1) == Decimal(
        "15.00"
    )
    assert all(
        "contrasena" not in columns and "pin" not in columns
        for _, columns in supabase_fixture.reads
    )


def test_hidden_products_and_profiles(supabase_fixture):
    catalog = StoreCatalog(supabase_fixture, "owner")
    supabase_fixture.tables["config_catalogo"][0]["ocultar_perfiles"] = True
    assert all(p.kind == "cuenta" for p in catalog.products())
    supabase_fixture.tables["config_catalogo"][0]["oculto"] = True
    assert catalog.products() == []


def test_published_out_of_stock_products_remain_visible_to_conversation(supabase_fixture):
    catalog = StoreCatalog(supabase_fixture, "owner")
    for account in supabase_fixture.tables["cuentas_servicios"]:
        account["fecha_vencimiento"] = "2020-01-01"
    assert catalog.products() == []
    listed = catalog.listed_products()
    assert listed and all(product.stock == 0 for product in listed)


def test_inventory_reads_more_than_supabase_default_limit():
    fake = FakeSupabase({"inventory": [{"id": i} for i in range(1234)]})
    assert len(paged(fake.table("inventory").select("id").order("id"))) == 1234


def test_customer_number_variants_and_duplicate_failure(supabase_fixture):
    catalog = StoreCatalog(supabase_fixture, "owner")
    assert catalog.customer("51912345678")["id"] == "customer"
    supabase_fixture.tables["clientes"].append(
        {"user_id": "owner", "id": "other", "telefono": "912345678"}
    )
    with pytest.raises(BusinessError, match="duplicados"):
        catalog.customer("51912345678")


def test_suspended_customer_not_allowed(supabase_fixture):
    supabase_fixture.tables["clientes"][0]["estado"] = "suspendido"
    with pytest.raises(BusinessError):
        StoreCatalog(supabase_fixture, "owner").customer("51912345678")


@pytest.mark.parametrize("qty", [0, -1, 21, True, "1", 1.2])
def test_invalid_quantity_cannot_create_quote(qty):
    with pytest.raises(BusinessError):
        quote_products(
            DemoCatalog().products(), [{"product_id": "demo-netflix:perfil:1", "qty": qty}]
        )


def test_unknown_product_and_duplicate_product_rejected():
    products = DemoCatalog().products()
    with pytest.raises(BusinessError):
        quote_products(products, [{"product_id": "inventado", "qty": 1}])
    with pytest.raises(BusinessError):
        quote_products(products, [{"product_id": products[0].id, "qty": 1}] * 2)


def test_durations_share_stock():
    with pytest.raises(BusinessError, match="stock"):
        quote_products(
            DemoCatalog().products(),
            [
                {"product_id": "demo-disney:perfil:1", "qty": 2},
                {"product_id": "demo-disney:perfil:3", "qty": 1},
            ],
        )


def test_discount_uses_postgres_rounding():
    quote = quote_products(
        DemoCatalog().products(), [{"product_id": "demo-netflix:perfil:1", "qty": 2}], Decimal(10)
    )
    assert quote["total"] == "28.00"  # PostgreSQL round(13.5,0)=14


@pytest.mark.parametrize(
    "text", ["no, ok", "sí pero no compres", "confirmo?", "no confirmo", "ok cuánto cuesta"]
)
def test_ambiguous_message_never_authorizes_purchase(text):
    assert not affirmative(text)


def test_confirmation_and_phone_are_explicit():
    assert affirmative("¡Sí!")
    assert negative("no, gracias")
    assert phone_number("+51912345678") == "51912345678"
    with pytest.raises(BusinessError):
        phone_number("120@g.us")


def test_commerce_uses_idempotent_wrapper_and_server_identity():
    fake = FakeSupabase(catalog={"ok": True, "sales": [{"servicio": "demo"}]})
    commerce = StoreCommerce(fake, "owner")
    quote = quote_products(
        DemoCatalog().products(), [{"product_id": "demo-netflix:perfil:1", "qty": 1}]
    )
    commerce.purchase(str(uuid4()), "51912345678", quote)
    name, args = fake.rpc_calls[0]
    assert name == "bot_procesar_compra"
    assert args["p_user_id"] == "owner" and args["p_phone"] == "51912345678"
    assert args["p_expected_total"] == "15.00"
    assert "price" not in args["p_items"][0] and "p_cliente_id" not in args


def test_sql_migration_parses():
    sql = (Path(__file__).parents[2] / "database" / "bot_commerce.sql").read_text(encoding="utf-8")
    assert pglast.parse_sql(sql)
    assert pglast.parse_plpgsql(sql)
