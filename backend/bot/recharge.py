"""Entrega comprobantes de WaSender al verificador de la tienda."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx

from bot.domain import BusinessError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
logger = logging.getLogger("dadtv.bot")


class WhatsAppRecharge:
    def __init__(self, settings, catalog, client=None):
        self.settings, self.catalog, self.client = settings, catalog, client

    async def process(self, phone, row, amount):
        customer = await asyncio.to_thread(self.catalog.customer, phone)
        if not customer:
            raise BusinessError("No encuentro tu cuenta con este WhatsApp. Regístrate primero en la tienda usando este mismo número.")
        image = json.loads(row["image_json"])
        mime = image.get("mimetype")
        if mime not in ALLOWED_MIMES:
            raise BusinessError("Envíame una captura en JPG, PNG o WebP.")
        try:
            declared_length = int(image.get("fileLength") or 0)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > MAX_IMAGE_BYTES:
            raise BusinessError("La captura supera 5 MB. Envíala más pequeña.")
        if not image.get("url") or not image.get("mediaKey"):
            raise BusinessError("No pude recibir la captura completa. Envíala de nuevo.")
        path = f"{customer['id']}/whatsapp-{uuid5(NAMESPACE_URL, self.settings.tenant_id + ':' + row['provider_id'])}.{ALLOWED_MIMES[mime]}"
        headers = {"Authorization": f"Bearer {self.settings.wasender_token}"}
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=25, follow_redirects=False)
        try:
            response = await client.post(
                "https://www.wasenderapi.com/api/decrypt-media",
                headers=headers,
                json={"data": {"messages": {"key": {"id": row["provider_id"]}, "message": {"imageMessage": image}}}},
            )
            response.raise_for_status()
            body = response.json()
            url = body.get("publicUrl") if isinstance(body, dict) else None
            parsed = urlparse(url) if isinstance(url, str) else None
            if (
                not parsed
                or parsed.scheme != "https"
                or parsed.hostname != "www.wasenderapi.com"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or not parsed.path.startswith(("/api/decrypted-media/", "/decrypted-media/"))
            ):
                logger.warning(
                    "WaSender decrypt-media response shape: type=%s keys=%s data_type=%s data_keys=%s public_url_type=%s host=%s path_prefix=%s",
                    type(body).__name__,
                    sorted(body.keys()) if isinstance(body, dict) else [],
                    type(body.get("data")).__name__ if isinstance(body, dict) else "none",
                    sorted(body["data"].keys()) if isinstance(body, dict) and isinstance(body.get("data"), dict) else [],
                    type(url).__name__,
                    parsed.hostname if parsed else None,
                    "/".join(parsed.path.split("/")[:2]) if parsed else None,
                )
                raise BusinessError("WaSender no devolvió una dirección válida para la captura.")
            chunks = []
            size = 0
            async with client.stream("GET", url) as media:
                media.raise_for_status()
                async for chunk in media.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise BusinessError("La captura supera 5 MB.")
                    chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise BusinessError("La captura está vacía o supera 5 MB.")
            if mime == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
                raise BusinessError("La captura no parece ser un JPG válido.")
            if mime == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise BusinessError("La captura no parece ser un PNG válido.")
            if mime == "image/webp" and not (content.startswith(b"RIFF") and content[8:12] == b"WEBP"):
                raise BusinessError("La captura no parece ser un WebP válido.")
            base = self.settings.supabase_url.rstrip("/")
            auth = {"Authorization": f"Bearer {self.settings.supabase_key}", "apikey": self.settings.supabase_key}
            upload = await client.post(
                f"{base}/storage/v1/object/receipts/{quote(path, safe='/')}",
                headers={**auth, "Content-Type": mime, "x-upsert": "false"},
                content=content,
            )
            # Reintentar el mismo mensaje usa la misma ruta: el archivo ya puede existir.
            if upload.status_code not in (200, 201, 409):
                upload.raise_for_status()
            result = await client.post(
                f"{base}/functions/v1/verify-whatsapp-receipt",
                headers={**auth, "Content-Type": "application/json"},
                json={
                    "userId": customer["id"],
                    "phone": phone,
                    "amountDeclared": amount,
                    "timestampLocal": datetime.now(timezone.utc).isoformat(),
                    "receiptPath": path,
                },
            )
            result.raise_for_status()
            body = result.json()
            if body.get("ok") and body.get("status") == "approved":
                try:
                    credited = Decimal(str(body["amountDetected"]))
                    balance = Decimal(str(body["currentBalance"]))
                    if credited > 0 and balance >= 0:
                        return f"¡Listo! Acredité S/ {credited:.2f}. Tu saldo actual es S/ {balance:.2f}. Ya puedes elegir qué comprar o renovar."
                except (KeyError, InvalidOperation, TypeError):
                    pass
                return "Tu recarga fue aprobada. Escribe «saldo» para consultar tu billetera."
            if body.get("ok") and body.get("status") == "pending":
                return "Recibí tu comprobante. Quedó pendiente de revisión; aún no se acreditó el saldo. Un asesor lo revisará."
            if body.get("reason") in {"duplicate_receipt", "duplicate_operation"}:
                return "Ese comprobante o código de operación ya está registrado. No vuelvas a pagar; escribe «asesor» si necesitas ayuda."
            raise BusinessError(body.get("error") or "No pude validar la captura. Envíala de nuevo o escribe «asesor».")
        finally:
            if owned_client:
                await client.aclose()
