import time

import httpx

from bot.domain import BusinessError, phone_number
from bot.transport import contact_allowed, incoming_content, valid_secret


def incoming_webhook(payload, signature, settings, on_ignored=None):
    def ignored(reason):
        if on_ignored is not None:
            on_ignored(reason)
        return None

    # WaSender documents the configured webhook secret in this header.
    if not valid_secret(signature, settings.wasender_webhook_secret):
        raise PermissionError("Webhook no autorizado")
    if payload.get("event") != "messages.received":
        return ignored("other_event")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BusinessError("Payload inv�lido")
    message = data.get("messages")
    if not isinstance(message, dict):
        raise BusinessError("Payload inv�lido")
    key = message.get("key")
    if not isinstance(key, dict):
        raise BusinessError("Payload inv�lido")
    if key.get("fromMe") is not False:
        return ignored("outgoing_or_missing_origin")
    jid = key.get("remoteJid")
    if not isinstance(jid, str) or jid.endswith(("@g.us", "@newsletter", "@broadcast")):
        return ignored("non_private_chat")
    created = payload.get("timestamp")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return ignored("invalid_timestamp")
    # Some WaSender examples use seconds, others milliseconds.
    if created > 10**11:
        created /= 1000
    if not -60 <= time.time() - created <= settings.max_message_age:
        return ignored("outside_time_window")
    # LIDs are not phone numbers. WaSender recommends cleanedSenderPn.
    sender = key.get("cleanedSenderPn") or key.get("senderPn")
    if not sender and jid.endswith("@s.whatsapp.net"):
        sender = jid.removesuffix("@s.whatsapp.net")
    if isinstance(sender, str) and sender.endswith("@s.whatsapp.net"):
        sender = sender.removesuffix("@s.whatsapp.net")
    try:
        number = phone_number(sender)
    except BusinessError:
        return ignored("missing_phone")
    if not contact_allowed(number, settings):
        return ignored("phone_not_allowed")
    raw = message.get("message")
    attachment = isinstance(raw, dict) and any(
        name in raw for name in ("imageMessage", "videoMessage", "audioMessage", "documentMessage")
    )
    result = incoming_content(key.get("id"), number, message.get("messageBody"), attachment)
    image = raw.get("imageMessage") if isinstance(raw, dict) else None
    if result and isinstance(image, dict):
        safe = {name: image.get(name) for name in ("url", "mimetype", "mediaKey", "fileSha256", "fileLength")}
        if all(isinstance(safe[name], str) and safe[name] for name in ("url", "mimetype", "mediaKey")):
            result["image"] = safe
    if not key.get("id") or not result:
        return ignored("missing_id_or_content")
    return result


def outgoing_control(payload, signature, settings):
    """Read an explicit command sent from the owner's WhatsApp session.

    Only ``message.sent`` from the authenticated session can pause or resume
    a private chat. Ordinary outgoing messages, including bot replies, are
    ignored. The command is visible in the chat so the owner can audit it.
    """
    if not valid_secret(signature, settings.wasender_webhook_secret):
        raise PermissionError("Webhook no autorizado")
    if payload.get("event") != "message.sent":
        return None
    created = payload.get("timestamp")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return None
    if created > 10**11:
        created /= 1000
    if not -60 <= time.time() - created <= settings.max_message_age:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("success") is False:
        return None
    key = data.get("key")
    if isinstance(key, dict):
        if key.get("fromMe") is not True:
            return None
        recipient = key.get("remoteJid") or data.get("to")
    else:
        # WaSender also documents a flat message.sent payload with to/content.
        if data.get("status") != "sent":
            return None
        recipient = data.get("to")
    if not isinstance(recipient, str) or recipient.endswith(("@g.us", "@newsletter", "@broadcast", "@lid")):
        return None
    recipient = recipient.removesuffix("@s.whatsapp.net")
    try:
        phone = phone_number(recipient)
    except BusinessError:
        return None
    message = data.get("message")
    text = message.get("conversation") if isinstance(message, dict) else None
    text = text or data.get("content") or data.get("messageBody")
    if not isinstance(text, str):
        return None
    command = text.strip().lower()
    if command == "/bot off":
        return phone, True
    if command == "/bot on":
        return phone, False
    return None


class WaSender:
    API = "https://www.wasenderapi.com/api/send-message"

    def __init__(self, settings, client=None):
        self.settings, self.client = settings, client

    async def send(self, phone, text):
        if not self.settings.send_enabled or not contact_allowed(phone, self.settings):
            return False
        kwargs = {
            "headers": {"Authorization": f"Bearer {self.settings.wasender_token}"},
            "json": {"to": f"+{phone}", "text": text},
        }
        if self.client:
            response = await self.client.post(self.API, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(self.API, **kwargs)
        response.raise_for_status()
        body = response.json()
        return isinstance(body, dict) and body.get("success") is True
