import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from bot.assistant import OpenAIAssistant, RuleAssistant
from bot.catalog import DemoCatalog, StoreCatalog
from bot.commerce import DemoCommerce, StoreCommerce
from bot.domain import BusinessError, phone_number
from bot.engine import Engine
from bot.intent_model import IntentModel
from bot.repository import Repository
from bot.service import BotService
from bot.settings import Settings
from bot.transport import Wamundo, incoming_webhook, valid_secret
from bot.wasender import WaSender
from bot.wasender import incoming_webhook as incoming_wasender_webhook
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


def build_service(settings):
    settings.validate()
    if settings.mode == "demo":
        catalog, commerce = DemoCatalog(), DemoCommerce()
    else:
        from supabase import create_client

        client = create_client(settings.supabase_url, settings.supabase_key)
        catalog, commerce = (
            StoreCatalog(client, settings.tenant_id),
            StoreCommerce(client, settings.tenant_id),
        )
    assistant = (
        OpenAIAssistant(settings.openai_key, settings.openai_model)
        if settings.engine == "openai"
        else RuleAssistant()
    )
    intent_model = IntentModel.load(settings.intent_model_path) if settings.intent_model_enabled else None
    account = "demo"
    if settings.mode == "live":
        provider_account = (
            settings.wasender_account_phone
            if settings.provider == "wasender"
            else settings.wamundo_account
        )
        account = f"{settings.tenant_id}:{settings.provider}:{provider_account}"
    repository = Repository(settings.database_path, account, settings.max_attempts)
    transport = WaSender(settings) if settings.provider == "wasender" else Wamundo(settings)
    return BotService(
        settings, repository, Engine(settings, catalog, commerce, assistant, intent_model), transport
    )


class DemoMessage(BaseModel):
    phone: str = "51900000000"
    message: str = Field(default="", max_length=4096)
    attachment: bool = False
    message_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)


class PauseRequest(BaseModel):
    phone: str
    paused: bool


def create_app(settings=None, service=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        active = service or build_service(settings)
        app.state.bot = active
        tasks = []
        if settings.mode == "live":
            tasks.append(asyncio.create_task(active.worker()))
            if (
                settings.provider == "wamundo"
                and settings.poll_seconds > 0
                and settings.wamundo_secret
                and settings.wamundo_account
            ):
                tasks.append(asyncio.create_task(active.poller()))
        logging.getLogger("dadtv.bot").info(
            "Bot iniciado: modo=%s engine=%s envío=%s comercio=%s",
            settings.mode,
            settings.engine,
            settings.send_enabled,
            settings.commerce_enabled,
        )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="DadTvPlay Chatbot", lifespan=lifespan)
    app.state.webhook_diagnostics = {"last_at": None, "last_result": None, "counts": {}}

    def record_webhook(result):
        diagnostics = app.state.webhook_diagnostics
        diagnostics["last_at"] = datetime.now(timezone.utc).isoformat()
        diagnostics["last_result"] = result
        diagnostics["counts"][result] = diagnostics["counts"].get(result, 0) + 1

    async def admin(request: Request):
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not valid_secret(token, settings.admin_token):
            raise HTTPException(401, "Se requiere el token administrativo")

    @app.get("/health")
    def health():
        return {"ok": True, "mode": settings.mode}

    @app.get("/admin/status", dependencies=[Depends(admin)])
    async def status(request: Request):
        return {
            "mode": settings.mode,
            "engine": settings.engine,
            "provider": settings.provider,
            "send_enabled": settings.send_enabled,
            "commerce_enabled": settings.commerce_enabled,
            "intent_model_enabled": settings.intent_model_enabled,
            "intent_model_classes": list(app.state.bot.engine.intent_model.classes)
            if app.state.bot.engine.intent_model else [],
            "test_contacts_only": settings.test_contacts_only,
            "allowed_contact_count": len(settings.allowed_phones),
            "queue": await asyncio.to_thread(request.app.state.bot.repository.stats),
            "wasender_webhook": app.state.webhook_diagnostics if settings.provider == "wasender" else None,
        }

    @app.post("/admin/demo/chat", dependencies=[Depends(admin)])
    async def demo_chat(body: DemoMessage, request: Request):
        if settings.mode != "demo":
            raise HTTPException(403, "Disponible solo en modo demo")
        try:
            phone = phone_number(body.phone)
        except BusinessError as error:
            raise HTTPException(422, str(error)) from None
        if not body.message.strip() and not body.attachment:
            raise HTTPException(422, "Envía texto o un adjunto de prueba")
        active = request.app.state.bot
        async with active.demo_lock:
            inserted = await asyncio.to_thread(
                active.repository.enqueue, body.message_id, phone, body.message, body.attachment
            )
            if not inserted:
                return {"duplicate": True, "reply": None}
            await active.process_once()
            replies = []
            while row := await asyncio.to_thread(active.repository.claim_outbox):
                replies.append(row["body"])
                await asyncio.to_thread(active.repository.finish_outbox, row["id"], True)
            return {
                "duplicate": False,
                "reply": "\n".join(replies) or None,
                "state": await asyncio.to_thread(active.repository.session, phone),
            }

    @app.post("/admin/conversations/pause", dependencies=[Depends(admin)])
    async def pause(body: PauseRequest, request: Request):
        try:
            phone = phone_number(body.phone)
        except BusinessError as error:
            raise HTTPException(422, str(error)) from None
        await asyncio.to_thread(request.app.state.bot.repository.pause, phone, body.paused)
        return {"ok": True, "paused": body.paused}

    @app.get("/admin/conversations/paused", dependencies=[Depends(admin)])
    async def paused(request: Request):
        return await asyncio.to_thread(request.app.state.bot.repository.paused_conversations)

    @app.get("/admin/conversations/{phone}/history", dependencies=[Depends(admin)])
    async def history(phone: str, request: Request):
        try:
            phone = phone_number(phone)
        except BusinessError as error:
            raise HTTPException(422, str(error)) from None
        return await asyncio.to_thread(request.app.state.bot.repository.recent_history, phone, 30)

    @app.post("/admin/queue/retry", dependencies=[Depends(admin)])
    async def retry(request: Request):
        await asyncio.to_thread(request.app.state.bot.repository.retry_failed)
        return {"ok": True}

    @app.get("/admin/conversations/{phone}/state", dependencies=[Depends(admin)])
    async def conversation_state(phone: str, request: Request):
        try:
            phone = phone_number(phone)
        except BusinessError as error:
            raise HTTPException(422, str(error)) from None
        return await asyncio.to_thread(request.app.state.bot.repository.session, phone)

    @app.get("/webhook/wamundo-hook")
    async def hook(request: Request):
        if (
            settings.mode != "live"
            or settings.provider != "wamundo"
            or not valid_secret(request.query_params.get("token"), settings.wamundo_hook_token)
        ):
            raise HTTPException(401, "Hook no autorizado")
        try:
            await request.app.state.bot.poll_once()
        except Exception:
            raise HTTPException(503, "No pudimos consultar los mensajes") from None
        return {"ok": True}

    @app.post("/webhook/wamundo")
    async def webhook(request: Request):
        if settings.mode != "live" or settings.provider != "wamundo":
            raise HTTPException(401, "Webhook desactivado en demo")
        body = await request.body()
        if len(body) > 65536:
            raise HTTPException(413, "Payload demasiado grande")
        try:
            import json

            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError
            message = incoming_webhook(payload, settings)
        except PermissionError:
            raise HTTPException(401, "Webhook no autorizado") from None
        except (ValueError, BusinessError):
            raise HTTPException(422, "Payload inválido") from None
        if not message:
            return {"ok": True, "ignored": True}
        inserted = await asyncio.to_thread(
            request.app.state.bot.repository.enqueue, **message, delay=settings.debounce_seconds
        )
        return {"ok": True, "duplicate": not inserted}

    @app.post("/webhook/wasender")
    async def wasender_webhook(request: Request):
        if settings.mode != "live" or settings.provider != "wasender":
            raise HTTPException(401, "Webhook desactivado")
        body = await request.body()
        if len(body) > 65536:
            record_webhook("payload_too_large")
            raise HTTPException(413, "Payload demasiado grande")
        try:
            import json

            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError
            message = incoming_wasender_webhook(
                payload,
                request.headers.get("X-Webhook-Signature"),
                settings,
                on_ignored=record_webhook,
            )
        except PermissionError:
            record_webhook("unauthorized")
            raise HTTPException(401, "Webhook no autorizado") from None
        except (ValueError, BusinessError):
            record_webhook("invalid_payload")
            raise HTTPException(422, "Payload inválido") from None
        if not message:
            return {"ok": True, "ignored": True}
        inserted = await asyncio.to_thread(
            request.app.state.bot.repository.enqueue, **message, delay=settings.debounce_seconds
        )
        record_webhook("accepted" if inserted else "duplicate")
        return {"ok": True, "duplicate": not inserted}

    return app


app = create_app()
