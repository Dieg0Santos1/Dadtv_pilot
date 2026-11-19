import hmac
import time

import httpx

from bot.domain import BusinessError, phone_number


def valid_secret(received, expected):
    return (
        bool(expected)
        and isinstance(received, str)
        and hmac.compare_digest(received.encode(), expected.encode())
    )


def incoming_poll(chat, settings):
    if chat.get("group") or chat.get("account_unique") != settings.wamundo_account:
        return None
    created = chat.get("created")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return None
    if not -60 <= time.time() - created <= settings.max_message_age:
        return None
    if chat.get("id") is None:
        return None
    message = incoming_content(
        chat["id"], chat.get("reply_to"), chat.get("message"), chat.get("attachment")
    )
    return message if message and contact_allowed(message["phone"], settings) else None


def incoming_webhook(payload, settings):
    if not valid_secret(payload.get("secret"), settings.wamundo_webhook_secret):
        raise PermissionError("Webhook no autorizado")
    if payload.get("type") != "whatsapp":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BusinessError("Payload inv�lido")
    # wid es distinto de account_unique: configurarlo tras una prueba real.
    if not settings.wamundo_account_wid or data.get("wid") != settings.wamundo_account_wid:
        return None
    created = data.get("timestamp")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return None
    if not -60 <= time.time() - created <= settings.max_message_age or data.get("id") is None:
        return None
    message = incoming_content(
        data["id"], data.get("phone"), data.get("message"), data.get("attachment")
    )
    return message if message and contact_allowed(message["phone"], settings) else None


def contact_allowed(phone, settings):
    try:
        number = phone_number(phone)
    except BusinessError:
        return False
    return not settings.test_contacts_only or number in settings.allowed_phones


def incoming_content(provider_id, phone, message, attachment, allow_lid=False):
    try:
        number = phone if allow_lid and isinstance(phone, str) and phone.endswith("@lid") else phone_number(phone)
    except BusinessError:
        return None
    message = "" if message is None else message
    if (
        not isinstance(message, str)
        or len(message) > 4096
        or (not message.strip() and not attachment)
    ):
        return None
    if len(str(provider_id)) > 200:
        return None
    return {
        "provider_id": str(provider_id),
        "phone": number,
        "message": message.strip(),
        "attachment": bool(attachment),
    }


class Wamundo:
    API = "https://wamundo.com/api"

    def __init__(self, settings, client=None):
        self.settings, self.client = settings, client

    async def _request(self, method, path, **kwargs):
        if self.client:
            return await self.client.request(method, f"{self.API}/{path}", **kwargs)
        async with httpx.AsyncClient(timeout=20) as client:
            return await client.request(method, f"{self.API}/{path}", **kwargs)

    async def received(self):
        response = await self._request(
            "GET", "get/wa.received", params={"secret": self.settings.wamundo_secret, "limit": "30"}
        )
        response.raise_for_status()
        body = response.json()
        if (
            not isinstance(body, dict)
            or body.get("status") != 200
            or not isinstance(body.get("data"), list)
        ):
            raise ValueError("Respuesta inv�lida de Wamundo")
        return sorted(body["data"], key=lambda row: row.get("created") or 0)

    async def send(self, phone, text):
        if not self.settings.send_enabled or not contact_allowed(phone, self.settings):
            return False
        response = await self._request(
            "POST",
            "send/whatsapp",
            files={
                "secret": (None, self.settings.wamundo_secret),
                "account": (None, self.settings.wamundo_account),
                "recipient": (None, phone),
                "type": (None, "text"),
                "message": (None, text),
            },
        )
        response.raise_for_status()
        body = response.json()
        return (
            isinstance(body, dict) and body.get("status") == 200 and body.get("data") is not False
        )
