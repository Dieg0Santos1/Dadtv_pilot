import asyncio
import logging
import time

from bot.domain import BusinessError
from bot.recharge import WhatsAppRecharge
from bot.transport import contact_allowed

logger = logging.getLogger("dadtv.bot")


class BotService:
    def __init__(self, settings, repository, engine, transport):
        self.settings, self.repository, self.engine, self.transport = (
            settings,
            repository,
            engine,
            transport,
        )
        self.process_lock, self.poll_lock, self.demo_lock = (
            asyncio.Lock(),
            asyncio.Lock(),
            asyncio.Lock(),
        )

    async def process_once(self):
        async with self.process_lock:
            batch = await asyncio.to_thread(self.repository.claim_batch)
            if not batch:
                return False
            phone = batch[0]["phone"]
            state = {}
            try:
                state = await asyncio.to_thread(self.repository.session, phone)
                if self.settings.mode == "live" and not contact_allowed(phone, self.settings):
                    await asyncio.to_thread(self.repository.complete, batch, None, state)
                    return True
                # Pausar la conversacion tambien detiene comprobantes: la ruta de
                # imagenes se procesa antes de Engine.handle, que comprueba la pausa.
                if state.get("human_paused"):
                    await asyncio.to_thread(self.repository.complete, batch, None, state)
                    return True
                history = await asyncio.to_thread(self.repository.recent_history, phone)
                text = " / ".join(row["message"] for row in batch if row["message"])
                batch_id = (
                    self.repository.account + ":" + ":".join(row["provider_id"] for row in batch)
                )
                images = [row for row in batch if row.get("image_json")]
                if self.settings.whatsapp_recharge_enabled and images:
                    from bot.engine import Reply

                    if len(images) != 1:
                        reply = Reply("Env�ame un comprobante a la vez, por favor.", state)
                    elif not state.get("recharge_amount") or state.get("recharge_focus", 0) <= time.time():
                        reply = Reply("Para revisar tu comprobante, primero escribe �recargar� y dime el monto exacto; despu�s vuelve a enviar la foto.", state)
                    else:
                        receipt = WhatsAppRecharge(self.settings, self.engine.catalog)
                        answer = await receipt.process(phone, images[0], state["recharge_amount"])
                        state.pop("recharge_amount", None)
                        reply = Reply(answer, state)
                else:
                    reply = await self.engine.handle(
                        phone, text, any(row["attachment"] for row in batch), state, history, batch_id
                    )
                support_alert = None
                alert_phone = self.settings.support_alert_phone
                if (
                    alert_phone
                    and phone != alert_phone
                    and reply.state.get("human_paused")
                    and not state.get("human_paused")
                ):
                    support_alert = (
                        alert_phone,
                        f"DadTv: hay una consulta pendiente de atenci�n humana del n�mero +{phone}. "
                        "Abre el WhatsApp de DadTvPlay para revisarla.",
                        f"support:{batch[0]['id']}",
                    )
                await asyncio.to_thread(
                    self.repository.complete,
                    batch,
                    reply.text,
                    reply.state,
                    reply.history_text,
                    support_alert,
                )
            except BusinessError as error:
                await asyncio.to_thread(self.repository.complete, batch, str(error), state)
            except Exception as error:
                logger.error(
                    "Fall� procesamiento de lote %s: %s", batch[0]["id"], type(error).__name__
                )
                await asyncio.to_thread(self.repository.fail_batch, batch)
            return True

    async def send_once(self):
        if not self.settings.send_enabled:
            return False
        row = await asyncio.to_thread(self.repository.claim_outbox)
        if not row:
            return False
        # A manual takeover can happen after a reply was queued. An automatic
        # complaint handoff still sends its acknowledgement to the customer.
        if not row.get("dedupe_key") and (
            await asyncio.to_thread(self.repository.session, row["phone"])
        ).get("manual_paused"):
            await asyncio.to_thread(self.repository.cancel_outbox, row["id"])
            return True
        if not contact_allowed(row["phone"], self.settings):
            await asyncio.to_thread(self.repository.cancel_outbox, row["id"])
            return True
        accepted = False
        try:
            accepted = await self.transport.send(row["phone"], row["body"])
        except Exception as error:
            logger.error("Fall� env�o %s: %s", row["id"], type(error).__name__)
        await asyncio.to_thread(self.repository.finish_outbox, row["id"], accepted)
        return True

    async def poll_once(self):
        if self.poll_lock.locked():
            return
        async with self.poll_lock:
            from bot.transport import incoming_poll

            chats = await self.transport.received()
            if len(chats) == 30:
                logger.warning(
                    "Se alcanz� el l�mite del sondeo; verificar webhook y recuperaci�n de historial"
                )
            for chat in chats:
                message = incoming_poll(chat, self.settings)
                if message:
                    await asyncio.to_thread(
                        self.repository.enqueue, **message, delay=self.settings.debounce_seconds
                    )

    async def worker(self):
        while True:
            try:
                sent = await self.send_once()
                processed = await self.process_once()
                if not sent and not processed:
                    await asyncio.sleep(1)
            except Exception as error:
                logger.error("Fall� worker: %s", type(error).__name__)
                await asyncio.sleep(5)

    async def poller(self):
        while True:
            try:
                await self.poll_once()
            except Exception as error:
                logger.error("Fall� sondeo: %s", type(error).__name__)
            await asyncio.sleep(self.settings.poll_seconds)
