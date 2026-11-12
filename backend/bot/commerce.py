from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from bot.domain import BusinessError, money


def quote_products(products, selections, discount=Decimal(0), premium=False):
    by_id = {p.id: p for p in products}
    if not isinstance(selections, list) or not selections or len(selections) > 20:
        raise BusinessError("Elige entre 1 y 20 productos.")
    items = []
    used = set()
    stocks = {}
    total = Decimal(0)
    for selection in selections:
        if not isinstance(selection, dict):
            raise BusinessError("No pude identificar el producto solicitado.")
        product_id = selection.get("product_id")
        qty = selection.get("qty", 1)
        if type(qty) is not int or not 1 <= qty <= 20 or product_id in used:
            raise BusinessError("La cantidad debe ser de 1 a 20, sin repetir productos.")
        product = by_id.get(product_id)
        if not product:
            raise BusinessError(
                "Una opción ya no está disponible. Consulta nuevamente el catálogo."
            )
        used.add(product_id)
        # Diferentes duraciones comparten el mismo inventario.
        stock_key = (product.service_id, product.kind)
        stocks[stock_key] = stocks.get(stock_key, 0) + qty
        if stocks[stock_key] > product.stock:
            raise BusinessError(f"No hay stock suficiente de {product.platform}.")
        price = product.price
        if discount or premium:
            price = (price * (1 - discount / 100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        items.append(
            {
                "product_id": product.id,
                "service_id": product.service_id,
                "type": product.kind,
                "months": product.months,
                "qty": qty,
                "unit_price": str(price),
                "label": product.label,
            }
        )
        total += price * qty
    return {"items": items, "total": str(money(total))}


class StoreCommerce:
    def __init__(self, client, tenant_id):
        self.client, self.tenant_id = client, tenant_id

    def existing_result(self, order_id, phone):
        rows = (
            self.client.table("bot_orders")
            .select("result")
            .eq("id", order_id)
            .eq("user_id", self.tenant_id)
            .eq("phone", phone)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0]["result"] if rows else None

    def purchase(self, order_id, phone, quote):
        UUID(order_id)
        # El wrapper de staging aporta idempotencia en la misma transacción que la compra.
        # No llamar directamente procesar_compra_tienda: un timeout podría cobrar dos veces.
        try:
            result = (
                self.client.rpc(
                    "bot_procesar_compra",
                    {
                        "p_order_id": order_id,
                        "p_user_id": self.tenant_id,
                        "p_phone": phone,
                        "p_items": [
                            {k: item[k] for k in ("service_id", "type", "months", "qty")}
                            for item in quote["items"]
                        ],
                        "p_expected_total": quote["total"],
                    },
                )
                .execute()
                .data
            )
        except Exception as error:
            # Solo traducir errores comerciales conocidos; fallos de red se reintentan con el mismo pedido.
            from postgrest.exceptions import APIError

            if isinstance(error, APIError):
                message = str(error.message)
                for trigger, reply in (
                    (
                        "Saldo insuficiente",
                        "Tu saldo cambió. Consulta tu saldo y recarga antes de confirmar.",
                    ),
                    (
                        "Stock insuficiente",
                        "El producto se agotó. Consulta nuevamente el catálogo.",
                    ),
                    (
                        "El total cambió",
                        "El precio cambió. Consulta nuevamente el catálogo para cotizar.",
                    ),
                    ("cuenta del cliente necesita", "Tu cuenta necesita revisión de un asesor."),
                ):
                    if trigger in message:
                        raise BusinessError(reply) from None
            raise
        if not isinstance(result, dict) or not result.get("ok") or not result.get("sales"):
            raise RuntimeError("La compra no devolvió un resultado válido")
        return result

    def existing_renewal_result(self, order_id, phone):
        rows = (
            self.client.table("bot_renewals")
            .select("result")
            .eq("id", order_id)
            .eq("user_id", self.tenant_id)
            .eq("phone", phone)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0]["result"] if rows else None

    def renew(self, order_id, phone, quote):
        UUID(order_id)
        try:
            result = (
                self.client.rpc(
                    "bot_renovar_venta",
                    {
                        "p_order_id": order_id,
                        "p_user_id": self.tenant_id,
                        "p_phone": phone,
                        "p_sale_id": quote["sale_id"],
                        "p_months": quote["months"],
                        "p_expected_expiration": quote["expiration"],
                        "p_expected_total": quote["total"],
                    },
                )
                .execute()
                .data
            )
        except Exception as error:
            from postgrest.exceptions import APIError

            if isinstance(error, APIError):
                message = str(error.message)
                if "Saldo insuficiente" in message:
                    raise BusinessError("Tu saldo cambió. Recarga antes de confirmar la renovación.") from None
                if "precio cambió" in message or "fecha de vencimiento cambió" in message:
                    raise BusinessError("La compra cambió. Escribe «renovar» para recibir una nueva cotización.") from None
                if "ya no está disponible" in message or "plan ya no está disponible" in message:
                    raise BusinessError("Esta compra o plan ya no está disponible para renovar por WhatsApp.") from None
            raise
        if not isinstance(result, dict) or not result.get("ok") or not result.get("fecha_vencimiento"):
            raise RuntimeError("La renovación no devolvió un resultado válido")
        return result


class DemoCommerce:
    def __init__(self):
        self.results = {}

    def existing_result(self, order_id, phone):
        return self.results.get(order_id)

    def purchase(self, order_id, phone, quote):
        if order_id not in self.results:
            self.results[order_id] = {
                "ok": True,
                "demo": True,
                "total": quote["total"],
                "sales": [
                    {
                        "servicio": item["label"],
                        "correo": "ejemplo@invalid.test",
                        "contrasena": "DEMO-SIN-VALIDEZ",
                        "perfil": "Ejemplo",
                        "pin": None,
                        "fecha_vencimiento": "Solo demostración",
                    }
                    for item in quote["items"]
                    for _ in range(item["qty"])
                ],
            }
        return self.results[order_id]

    def existing_renewal_result(self, order_id, phone):
        return self.results.get(order_id)

    def renew(self, order_id, phone, quote):
        if order_id not in self.results:
            self.results[order_id] = {
                "ok": True, "demo": True, "servicio": quote["service"],
                "months": quote["months"], "total": quote["total"],
                "fecha_vencimiento": "2100-01-31", "current_balance": 85,
            }
        return self.results[order_id]
