from types import SimpleNamespace

import pytest


class FakeQuery:
    def __init__(self, rows, owner, table):
        self.rows, self.owner, self.table = list(rows), owner, table
        self.columns = "*"
        self.start, self.end = 0, None

    def select(self, columns):
        self.columns = columns
        self.owner.reads.append((self.table, columns))
        return self

    def eq(self, key, value):
        self.rows = [r for r in self.rows if r.get(key) == value]
        return self

    def in_(self, key, values):
        self.rows = [r for r in self.rows if r.get(key) in values]
        return self

    def order(self, key):
        self.rows.sort(key=lambda r: str(r.get(key, "")))
        return self

    def limit(self, limit):
        self.end = limit
        return self

    def range(self, start, end):
        self.start, self.end = start, end + 1
        return self

    def execute(self):
        return SimpleNamespace(
            data=[
                {k: v for k, v in r.items() if self.columns == "*" or k in self.columns.split(",")}
                for r in self.rows[self.start : self.end]
            ]
        )


class FakeSupabase:
    def __init__(self, tables=None, catalog=None):
        self.tables, self.catalog = tables or {}, catalog or []
        self.reads, self.rpc_calls = [], []

    def table(self, table):
        return FakeQuery(self.tables.get(table, []), self, table)

    def rpc(self, name, args):
        self.rpc_calls.append((name, args))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.catalog))


@pytest.fixture
def supabase_fixture():
    tenant = "owner"
    tables = {
        "config_catalogo": [
            {
                "user_id": tenant,
                "servicio_id": "netflix",
                "precio_perfil": "15",
                "precio_cuenta": "50",
            }
        ],
        "config_planes": [
            {
                "user_id": tenant,
                "servicio_id": "netflix",
                "enable_plans": True,
                "price_3m": "40",
                "enable_6m": False,
            }
        ],
        "cuentas_servicios": [
            {
                "user_id": tenant,
                "id": "full-sold",
                "servicio_id": "netflix",
                "fecha_vencimiento": None,
            },
            {
                "user_id": tenant,
                "id": "profile-sold",
                "servicio_id": "netflix",
                "fecha_vencimiento": None,
            },
            {
                "user_id": tenant,
                "id": "free",
                "servicio_id": "netflix",
                "fecha_vencimiento": "2099-01-01",
            },
            {
                "user_id": tenant,
                "id": "expired",
                "servicio_id": "netflix",
                "fecha_vencimiento": "2020-01-01",
            },
        ],
        "perfiles_cuentas": [
            {"user_id": tenant, "id": "p1", "cuenta_servicio_id": "full-sold"},
            {"user_id": tenant, "id": "p2", "cuenta_servicio_id": "profile-sold"},
            {"user_id": tenant, "id": "p3", "cuenta_servicio_id": "profile-sold"},
            {"user_id": tenant, "id": "p4", "cuenta_servicio_id": "free"},
            {"user_id": tenant, "id": "p5", "cuenta_servicio_id": "expired"},
        ],
        "ventas": [
            {
                "user_id": tenant,
                "id": "v1",
                "cuenta_servicio_id": "full-sold",
                "perfil_id": None,
                "liberada": False,
            },
            {
                "user_id": tenant,
                "id": "v2",
                "cuenta_servicio_id": "profile-sold",
                "perfil_id": "p2",
                "liberada": False,
            },
        ],
        "clientes": [
            {
                "user_id": tenant,
                "id": "customer",
                "telefono": "+51912345678",
                "saldo": 50,
                "estado": "activo",
            }
        ],
    }
    return FakeSupabase(
        tables,
        [
            {
                "servicio_id": "netflix",
                "servicio_nombre": "Netflix",
                "precio_cuenta": 60,
                "precio_perfil": 20,
                "stock_cuentas": 999,
                "stock_perfiles": 999,
            }
        ],
    )
