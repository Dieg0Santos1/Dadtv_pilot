from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bot.domain import BusinessError, Product, money, normalize, phone_number


def paged(query):
    """Supabase limita las lecturas; no truncar inventario a las primeras 1000 filas."""
    rows = []
    offset = 0
    while True:
        page = query.range(offset, offset + 499).execute().data or []
        rows.extend(page)
        if len(page) < 500:
            return rows
        offset += 500


class StoreCatalog:
    def __init__(self, client, tenant_id):
        self.client = client
        self.tenant_id = tenant_id

    def rows(self, table, columns, **filters):
        query = self.client.table(table).select(columns).eq("user_id", self.tenant_id)
        for key, value in filters.items():
            query = query.eq(key, value)
        return paged(
            query.order(
                "id" if table not in {"config_catalogo", "config_planes"} else "servicio_id"
            )
        )

    def listed_products(self) -> list[Product]:
        base = (
            self.client.rpc("get_catalogo_tienda", {"p_user_id": self.tenant_id}).execute().data
            or []
        )
        configs = {
            str(r["servicio_id"]): r
            for r in self.rows(
                "config_catalogo",
                "servicio_id,oculto,ocultar_perfiles,precio_cuenta,precio_perfil,modal_description",
            )
        }
        plans = {
            str(r["servicio_id"]): r
            for r in self.rows(
                "config_planes",
                "servicio_id,enable_plans,price_1m,price_3m,price_6m,price_12m,enable_1m,enable_3m,enable_6m,enable_12m",
            )
        }
        accounts = self.rows("cuentas_servicios", "id,servicio_id,fecha_vencimiento")
        profiles = self.rows("perfiles_cuentas", "id,cuenta_servicio_id")
        sales = self.rows("ventas", "id,cuenta_servicio_id,perfil_id", liberada=False)
        today = datetime.now(ZoneInfo("America/Lima")).date().isoformat()
        valid = {
            str(a["id"]): str(a["servicio_id"])
            for a in accounts
            if not a.get("fecha_vencimiento") or a["fecha_vencimiento"][:10] >= today
        }
        sold_accounts = {str(s["cuenta_servicio_id"]) for s in sales}
        full_accounts = {str(s["cuenta_servicio_id"]) for s in sales if not s.get("perfil_id")}
        sold_profiles = {str(s["perfil_id"]) for s in sales if s.get("perfil_id")}
        products = []
        for row in base:
            sid = str(row["servicio_id"])
            config, plan = configs.get(sid, {}), plans.get(sid, {})
            if config.get("oculto"):
                continue
            stocks = {
                "cuenta": sum(
                    1
                    for aid, service in valid.items()
                    if service == sid and aid not in sold_accounts
                ),
                "perfil": sum(
                    1
                    for p in profiles
                    if valid.get(str(p["cuenta_servicio_id"])) == sid
                    and str(p["cuenta_servicio_id"]) not in full_accounts
                    and str(p["id"]) not in sold_profiles
                ),
            }
            for kind in ("cuenta", "perfil"):
                if kind == "perfil" and config.get("ocultar_perfiles"):
                    continue
                base_price = config.get(f"precio_{kind}")
                if base_price is None:
                    base_price = row.get(f"precio_{kind}")
                for months in (1, 3, 6, 12):
                    enabled = bool(plan.get("enable_plans"))
                    if enabled and plan.get(f"enable_{months}m") is False:
                        continue
                    explicit = plan.get(f"price_{months}m") if enabled else None
                    if explicit is None and base_price is None:
                        continue
                    price = money(
                        explicit if explicit is not None else Decimal(str(base_price)) * months
                    )
                    if price <= 0:
                        continue
                    products.append(
                        Product(
                            f"{sid}:{kind}:{months}",
                            sid,
                            row["servicio_nombre"],
                            kind,
                            months,
                            price,
                            stocks[kind],
                            config.get("modal_description") or "",
                        )
                    )
        return sorted(products, key=lambda p: (normalize(p.platform), p.kind, p.months))

    def products(self) -> list[Product]:
        return [product for product in self.listed_products() if product.stock > 0]

    def customer(self, phone: str):
        number = phone_number(phone)
        variants = [number, f"+{number}"]
        if number.startswith("51") and len(number) == 11:
            variants.append(number[2:])
        rows = (
            self.client.table("clientes")
            .select("id,nombre,saldo,premium,estado,telefono")
            .eq("user_id", self.tenant_id)
            .in_("telefono", variants)
            .limit(3)
            .execute()
            .data
            or []
        )
        if len(rows) > 1:
            raise BusinessError(
                "Encontré registros duplicados con tu número. Necesitamos que un asesor lo revise."
            )
        if rows and rows[0].get("estado") not in {None, "activo"}:
            raise BusinessError("Tu cuenta necesita revisión de un asesor antes de comprar.")
        return rows[0] if rows else None

    def discount(self, customer) -> Decimal:
        if not customer or not customer.get("premium"):
            return Decimal(0)
        rows = (
            self.client.table("config_general")
            .select("descuento_premium")
            .eq("user_id", self.tenant_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        value = rows[0].get("descuento_premium") if rows else 0
        return min(max(Decimal(str(value or 0)), Decimal(0)), Decimal(100))

    def renewable_sales(self, customer):
        if not customer:
            return []
        sales = self.rows(
            "ventas", "id,cuenta_servicio_id,perfil_id,fecha_vencimiento",
            cliente_id=customer["id"], liberada=False,
        )
        today = datetime.now(ZoneInfo("America/Lima")).date().isoformat()
        sales = [sale for sale in sales if sale.get("fecha_vencimiento") and sale["fecha_vencimiento"][:10] >= today]
        account_ids = list({sale["cuenta_servicio_id"] for sale in sales if sale.get("cuenta_servicio_id")})
        if not account_ids:
            return []
        accounts = paged(
            self.client.table("cuentas_servicios")
            .select("id,servicio_id")
            .eq("user_id", self.tenant_id)
            .in_("id", account_ids)
            .order("id")
        )
        by_account = {str(account["id"]): str(account["servicio_id"]) for account in accounts}
        service_ids = list(set(by_account.values()))
        services = paged(
            self.client.table("servicios")
            .select("id,nombre")
            .eq("user_id", self.tenant_id)
            .in_("id", service_ids)
            .order("id")
        ) if service_ids else []
        names = {str(service["id"]): service["nombre"] for service in services}
        plans = self.rows(
            "config_planes", "servicio_id,enable_plans,enable_1m,enable_3m,enable_6m,enable_12m"
        )
        plans_by_service = {str(plan["servicio_id"]): plan for plan in plans}

        def allowed_months(service_id):
            plan = plans_by_service.get(service_id) or {}
            if not plan.get("enable_plans"):
                return [1]
            return [months for months in (1, 3, 6, 12) if plan.get(f"enable_{months}m") is not False]

        return [
            {
                "id": str(sale["id"]),
                "service_id": by_account[str(sale["cuenta_servicio_id"])],
                "service": names.get(by_account[str(sale["cuenta_servicio_id"])], "Servicio"),
                "kind": "perfil" if sale.get("perfil_id") else "cuenta",
                "expiration": sale["fecha_vencimiento"][:10],
                "allowed_months": allowed_months(by_account[str(sale["cuenta_servicio_id"])]),
            }
            for sale in sales if str(sale.get("cuenta_servicio_id")) in by_account
        ]


class DemoCatalog:
    """Datos ficticios, sin credenciales ni conexión externa."""

    def products(self):
        return [
            Product("demo-netflix:perfil:1", "demo-netflix", "Netflix", "perfil", 1, money(15), 3),
            Product(
                "demo-disney:perfil:1", "demo-disney", "Disney+ Premium", "perfil", 1, money(18), 2
            ),
            Product(
                "demo-disney:perfil:3", "demo-disney", "Disney+ Premium", "perfil", 3, money(48), 2
            ),
            Product("demo-prime:cuenta:1", "demo-prime", "Prime Video", "cuenta", 1, money(20), 1),
        ]

    def customer(self, phone):
        return {"id": "demo-client", "nombre": "Alex Demo", "saldo": 100, "premium": False}

    def discount(self, customer):
        return Decimal(0)

    def renewable_sales(self, customer):
        return [{"id": "demo-sale", "service_id": "demo-netflix", "service": "Netflix",
                 "kind": "perfil", "expiration": "2099-12-31", "allowed_months": [1]}]
