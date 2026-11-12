import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


class BusinessError(Exception):
    """Mensaje seguro que puede mostrarse al cliente."""


def normalize(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def phone_number(value: str) -> str:
    # Aceptamos E.164 del proveedor, no IDs de grupos ni números locales ambiguos.
    if not isinstance(value, str) or not re.fullmatch(r"\+?[1-9]\d{7,14}", value.strip()):
        raise BusinessError("El número de WhatsApp debe incluir el código de país.")
    return value.strip().lstrip("+")


def money(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise BusinessError("El precio configurado no es válido.")
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Product:
    id: str
    service_id: str
    platform: str
    kind: str
    months: int
    price: Decimal
    stock: int
    description: str = ""

    @property
    def label(self) -> str:
        duration = f"{self.months} {'mes' if self.months == 1 else 'meses'}"
        return f"{self.platform} · {'cuenta completa' if self.kind == 'cuenta' else 'perfil'} · {duration}"

    def public(self):
        return {
            "id": self.id,
            "name": self.label,
            "price": str(self.price),
            "stock": self.stock,
            "description": self.description,
        }


def affirmative(text: str) -> bool:
    # Una compra nunca depende de una interpretación de la IA.
    allowed = {
        "si",
        "si confirmo",
        "confirmo",
        "confirmar",
        "confirmo la compra",
        "si, confirmo",
        "comprar",
        "dale",
        "ok",
        "acepto",
        "si dale",
        "si, dale",
        "si por favor",
        "si, por favor",
        "si compralo",
        "compralo",
        "si activalo",
        "dale activalo",
        "activamelo",
        "me lo llevo",
    }
    return all(part.strip(" .!¡") in allowed for part in normalize(text).split(" / "))


def negative(text: str) -> bool:
    norm = normalize(text).strip(" .!¡")
    return norm in {
        "no",
        "no gracias",
        "no, gracias",
        "no confirmo",
        "no quiero",
        "no, ok",
    } or bool(re.match(r"^(cancel|mejor no\b|olvidalo\b|no quiero comprar\b)", norm))
