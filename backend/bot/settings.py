import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from bot.domain import phone_number


def flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} debe ser true o false")
    return value in {"true", "1"}


def legacy_service_role_key(key: str) -> bool:
    """Rechaza una anon key pegada por error en el campo de servicio."""
    try:
        payload = key.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims.get("role") == "service_role"
    except (IndexError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Settings:
    mode: str = "demo"
    engine: str = "rules"
    provider: str = "wamundo"
    database_path: str = "data/bot.sqlite3"
    admin_token: str = ""
    supabase_url: str = ""
    supabase_key: str = ""
    tenant_id: str = ""
    wamundo_secret: str = ""
    wamundo_account: str = ""
    wamundo_hook_token: str = ""
    wamundo_webhook_secret: str = ""
    wamundo_account_wid: str = ""
    wasender_token: str = ""
    wasender_webhook_secret: str = ""
    wasender_account_phone: str = ""
    openai_key: str = ""
    openai_model: str = ""
    intent_model_enabled: bool = True
    intent_model_path: str = str(Path(__file__).resolve().parent / "model" / "intent_apf3.json")
    intent_confidence_threshold: float = 0.45
    intent_margin_threshold: float = 0.15
    send_enabled: bool = False
    commerce_enabled: bool = False
    renewal_enabled: bool = False
    test_contacts_only: bool = True
    allowed_phones: tuple[str, ...] = ()
    support_alert_phone: str = ""
    poll_seconds: float = 30
    debounce_seconds: float = 7
    max_message_age: int = 600
    store_url: str = "https://dadtvplay.com"
    recharge_yape_phone: str = ""
    recharge_receiver: str = ""
    recharge_min_amount: int = 20
    recharge_window_minutes: int = 10
    whatsapp_recharge_enabled: bool = False
    quote_ttl_seconds: int = 900
    max_attempts: int = 5

    @classmethod
    def from_env(cls):
        return cls(
            mode=os.getenv("BOT_MODE", "demo"),
            engine=os.getenv("BOT_ENGINE", "rules"),
            provider=os.getenv("BOT_PROVIDER", "wamundo"),
            database_path=os.getenv("BOT_DATABASE_PATH", "data/bot.sqlite3"),
            admin_token=os.getenv("ADMIN_API_TOKEN", ""),
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            tenant_id=os.getenv("TENANT_ID", ""),
            wamundo_secret=os.getenv("WAMUNDO_SECRET", ""),
            wamundo_account=os.getenv("WAMUNDO_ACCOUNT_ID", ""),
            wamundo_hook_token=os.getenv("WAMUNDO_HOOK_TOKEN", ""),
            wamundo_webhook_secret=os.getenv("WAMUNDO_WEBHOOK_SECRET", ""),
            wamundo_account_wid=os.getenv("WAMUNDO_ACCOUNT_WID", ""),
            wasender_token=os.getenv("WASENDER_API_TOKEN", ""),
            wasender_webhook_secret=os.getenv("WASENDER_WEBHOOK_SECRET", ""),
            wasender_account_phone=os.getenv("WASENDER_ACCOUNT_PHONE", ""),
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_BOT_MODEL", ""),
            intent_model_enabled=flag("BOT_INTENT_MODEL_ENABLED", True),
            intent_model_path=os.getenv(
                "BOT_INTENT_MODEL_PATH",
                str(Path(__file__).resolve().parent / "model" / "intent_apf3.json"),
            ),
            intent_confidence_threshold=float(os.getenv("BOT_INTENT_CONFIDENCE_THRESHOLD", "0.45")),
            intent_margin_threshold=float(os.getenv("BOT_INTENT_MARGIN_THRESHOLD", "0.15")),
            send_enabled=flag("BOT_SEND_ENABLED"),
            commerce_enabled=flag("BOT_COMMERCE_ENABLED"),
            renewal_enabled=flag("BOT_RENEWAL_ENABLED"),
            test_contacts_only=flag("BOT_TEST_CONTACTS_ONLY", True),
            allowed_phones=tuple(
                phone_number(value.strip())
                for value in os.getenv("BOT_ALLOWED_PHONES", "").split(",")
                if value.strip()
            ),
            support_alert_phone=(
                phone_number(os.getenv("SUPPORT_ALERT_PHONE", "").strip())
                if os.getenv("SUPPORT_ALERT_PHONE", "").strip()
                else ""
            ),
            poll_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "30")),
            debounce_seconds=float(os.getenv("DEBOUNCE_SECONDS", "7")),
            max_message_age=int(os.getenv("MAX_MESSAGE_AGE_SECONDS", "600")),
            store_url=os.getenv("STORE_URL", "https://dadtvplay.com").rstrip("/"),
            recharge_yape_phone=os.getenv("RECHARGE_YAPE_PHONE", "").strip(),
            recharge_receiver=os.getenv("RECHARGE_RECEIVER", "").strip(),
            recharge_min_amount=int(os.getenv("RECHARGE_MIN_AMOUNT", "20")),
            recharge_window_minutes=int(os.getenv("RECHARGE_WINDOW_MINUTES", "10")),
            whatsapp_recharge_enabled=flag("BOT_WHATSAPP_RECHARGE_ENABLED"),
            quote_ttl_seconds=int(os.getenv("QUOTE_TTL_SECONDS", "900")),
            max_attempts=int(os.getenv("BOT_MAX_ATTEMPTS", "5")),
        )

    def validate(self):
        if self.mode not in {"demo", "live"} or self.engine not in {"rules", "openai"}:
            raise ValueError("BOT_MODE: demo/live; BOT_ENGINE: rules/openai")
        if self.provider not in {"wamundo", "wasender"}:
            raise ValueError("BOT_PROVIDER: wamundo/wasender")
        if self.mode == "demo" and (self.send_enabled or self.commerce_enabled):
            raise ValueError("El modo demo no permite envíos ni compras reales")
        if self.mode == "live" and not all((self.supabase_url, self.supabase_key, self.tenant_id)):
            raise ValueError("Faltan SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY o TENANT_ID")
        if self.engine == "openai" and not all((self.openai_key, self.openai_model)):
            raise ValueError("Faltan OPENAI_API_KEY u OPENAI_BOT_MODEL")
        if self.intent_model_enabled and not Path(self.intent_model_path).is_file():
            raise ValueError("Falta el modelo de intenciones APF3")
        if not 0 < self.intent_confidence_threshold <= 1 or not 0 <= self.intent_margin_threshold < 1:
            raise ValueError("Umbrales del clasificador APF3 invalidos")
        if self.mode == "live" and self.provider == "wasender":
            if not self.wasender_webhook_secret or not self.wasender_account_phone:
                raise ValueError("Faltan WASENDER_WEBHOOK_SECRET o WASENDER_ACCOUNT_PHONE")
            phone_number(self.wasender_account_phone)
        if (
            self.send_enabled
            and self.provider == "wamundo"
            and not all((self.wamundo_secret, self.wamundo_account))
        ):
            raise ValueError("Faltan las credenciales de Wamundo")
        if self.send_enabled and self.provider == "wasender" and not self.wasender_token:
            raise ValueError("Falta WASENDER_API_TOKEN")
        if self.commerce_enabled and not self.send_enabled:
            raise ValueError("Las compras reales requieren el canal de envío activo")
        if self.renewal_enabled and not self.commerce_enabled:
            raise ValueError("Las renovaciones reales requieren BOT_COMMERCE_ENABLED=true")
        if self.send_enabled and self.test_contacts_only and not self.allowed_phones:
            raise ValueError("Configura BOT_ALLOWED_PHONES antes de activar envíos en pruebas")
        if self.support_alert_phone:
            phone_number(self.support_alert_phone)
            if self.test_contacts_only and self.support_alert_phone not in self.allowed_phones:
                raise ValueError("SUPPORT_ALERT_PHONE debe estar en BOT_ALLOWED_PHONES durante las pruebas")
        if self.max_attempts < 1 or self.quote_ttl_seconds < 1:
            raise ValueError("Intentos y vigencia deben ser positivos")
        if self.recharge_min_amount < 1 or self.recharge_window_minutes < 1:
            raise ValueError("Límites de recarga inválidos")
        if self.recharge_yape_phone and not self.recharge_yape_phone.isdigit():
            raise ValueError("RECHARGE_YAPE_PHONE debe contener solo dígitos")
        if self.whatsapp_recharge_enabled and (self.mode != "live" or self.provider != "wasender" or not self.test_contacts_only):
            raise ValueError("La recarga por WhatsApp requiere WaSender en modo live y contactos de prueba")
        if self.whatsapp_recharge_enabled and not legacy_service_role_key(self.supabase_key):
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY debe ser la clave legacy service_role, no la anon")
        if self.debounce_seconds < 0 or self.poll_seconds < 0 or self.max_message_age < 1:
            raise ValueError("Intervalos inválidos")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
