import asyncio
import copy
import logging
import re
import time
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid5

from bot.commerce import quote_products
from bot.conversation import (
    customer_context,
    expand_template,
    greeting,
    listed_answer,
    mentioned_products,
    recharge_note,
    registration_completed,
    registration_note,
    wallet_note,
)
from bot.domain import BusinessError, affirmative, money, negative, normalize


@dataclass
class Reply:
    text: str | None
    state: dict
    history_text: str | None = None
    fragments: dict | None = None
    event: str = ""


class Engine:
    def __init__(self, settings, catalog, commerce, assistant, intent_model=None):
        self.settings, self.catalog, self.commerce, self.assistant = (
            settings,
            catalog,
            commerce,
            assistant,
        )
        self.intent_model = intent_model

    async def handle(self, phone, text, attachment, state, history, batch_id):
        if state.get("human_paused"):
            return Reply(None, copy.deepcopy(state))
        prediction = None
        if self.intent_model and text.strip():
            try:
                prediction = self.intent_model.predict(text)
                logging.getLogger("dadtv.bot").info(
                    "intent_ml=%s confidence=%.3f margin=%.3f in_vocabulary=%s",
                    prediction.intent, prediction.confidence, prediction.margin,
                    prediction.in_vocabulary,
                )
            except Exception as error:
                logging.getLogger("dadtv.bot").error(
                    "Fallo clasificador APF3: %s", type(error).__name__
                )
        try:
            customer = await asyncio.to_thread(self.catalog.customer, phone)
            context = customer_context(customer)
        except BusinessError as error:
            return Reply(str(error), copy.deepcopy(state))
        reply = await self._handle(
            phone, text, attachment, state, history, batch_id, customer, context, prediction
        )
        if reply.fragments:
            fragments = dict(reply.fragments)
            if not context["registered"] and reply.event in {"greeting", "unavailable"}:
                fragments["registration"] = registration_note(self.settings.store_url)
            salute = greeting(context, text, history)
            if salute:
                fragments = {"greeting": salute, **fragments}
            reply.text = " ".join(fragments.values())
            compose = getattr(self.assistant, "compose", None)
            if compose:
                try:
                    template = await compose(reply.event, fragments, context, text, history)
                    reply.text = expand_template(template, fragments)
                except Exception as error:
                    # La operación ya se decidió con datos verificados: una redacción fallida
                    # no pierde el mensaje ni repite la acción comercial.
                    logging.getLogger("dadtv.bot").warning(
                        "Redacción alternativa no disponible: %s", type(error).__name__
                    )
            if self.settings.mode == "demo" and reply.event in {"quote", "options"}:
                reply.text = "[DEMOSTRACIÓN — datos ficticios]\n" + reply.text
        return reply

    @staticmethod
    def speech(state, event, **fragments):
        return Reply(
            None,
            state,
            fragments={key: value for key, value in fragments.items() if value},
            event=event,
        )

    async def _handle(self, phone, text, attachment, state, history, batch_id, customer, context, prediction):
        state = copy.deepcopy(state)
        if state.get("human_paused"):
            return Reply(None, state)
        pending = state.get("pending")
        if (
            pending
            and pending.get("expires_at", 0) <= time.time()
            and not (affirmative(text) and pending.get("quote"))
        ):
            state["pending"], pending = None, None
        if attachment:
            # No descargar URLs arbitrarias ni inferir que una imagen implica pago.
            return Reply(
                "Recibí tu archivo. Si es una nota de voz, cuéntame por texto qué necesitas. "
                "Si es un comprobante, la imagen por sí sola no confirma una recarga. "
                + (
                    recharge_note(self.settings)
                    if customer
                    else registration_note(self.settings.store_url)
                ),
                state,
            )
        norm = normalize(text).strip()
        if pending and pending.get("quote") and norm in {"sii", "siii", "sip", "sipi"}:
            return Reply(
                "¿Me confirmas que quieres seguir con esta opción? Responde «sí, confirmo» "
                "y revisaré tu saldo antes de completar la compra o indicarte cómo recargar.",
                state,
            )
        if (
            self.settings.whatsapp_recharge_enabled
            and customer
            and state.get("recharge_focus", 0) > time.time()
            and not (pending and pending.get("options") and norm.isdigit())
        ):
            amount_match = re.fullmatch(r"(?:s/?\.?\s*)?(\d{1,5}(?:[.,]\d{1,2})?)", text.strip(), re.I)
            if amount_match:
                try:
                    amount = Decimal(amount_match.group(1).replace(",", "."))
                except InvalidOperation:
                    amount = Decimal(0)
                if amount < self.settings.recharge_min_amount:
                    return Reply(f"La recarga mínima es S/ {self.settings.recharge_min_amount}. Dime el monto correcto antes de enviar la captura.", state)
                state["recharge_amount"] = str(amount.quantize(Decimal("0.01")))
                return Reply(f"Anoté S/ {amount:.2f}. Si ya pagaste, envíame ahora la captura completa del comprobante. No vuelvas a pagar si ya hiciste el Yape.", state)
        if registration_completed(text):
            if customer:
                return Reply(
                    f"¡Listo, {context['name']}! Ya encuentro tu cuenta con este número. "
                    f"Tienes S/ {money(context['balance']):.2f} de saldo. "
                    "Dime qué producto buscas y reviso la disponibilidad.",
                    state,
                )
            return Reply(
                "Todavía no encuentro una cuenta con este WhatsApp. "
                "Revisa que hayas usado este mismo número al registrarte en "
                f"{self.settings.store_url} y vuelve a escribirme; la consultaré de nuevo.",
                state,
            )
        renewal = state.get("pending_renewal")
        if renewal and re.search(r"\b(?:1|3|6|12)\s*mes(?:es)?\b", norm):
            sale = {
                "id": renewal["sale_id"], "service_id": renewal["service_id"],
                "service": renewal["service"], "kind": renewal["kind"],
                "expiration": renewal["expiration"], "allowed_months": renewal["allowed_months"],
            }
            return await self.quote_renewal(customer, sale, self.renewal_months(norm), state, batch_id)
        if renewal and affirmative(text):
            return await self.checkout_renewal(phone, renewal, state)
        if "renov" in norm:
            state["pending"] = None
            state["pending_renewal"] = None
            state["recharge_focus"] = 0
            if not customer:
                return Reply(registration_note(self.settings.store_url), state)
            sales = await asyncio.to_thread(self.catalog.renewable_sales, customer)
            if not sales:
                return Reply("No encuentro compras activas para renovar con este número. Si acabas de comprar, escribe «asesor» para revisarlo.", state)
            matched = [sale for sale in sales if normalize(sale["service"]) in norm]
            choices = matched or sales
            if len(choices) == 1:
                months = self.renewal_months(norm)
                return await self.quote_renewal(customer, choices[0], months, state, batch_id)
            state["renewal_options"] = {
                "sales": choices,
                "expires_at": time.time() + self.settings.quote_ttl_seconds,
            }
            lines = "\n".join(
                f"{index}. {sale['service']} · {sale['kind']} · vence {sale['expiration']}"
                for index, sale in enumerate(choices, 1)
            )
            return Reply(f"Estas son tus compras activas:\n{lines}\n¿Cuál deseas renovar? Dime el número de la opción.", state)
        renewal_options = state.get("renewal_options") or {}
        if renewal_options.get("expires_at", 0) > time.time():
            selected = re.fullmatch(r"(?:opcion\s*)?(\d{1,2})(?:\s*(?:por\s*)?(\d{1,2})\s*mes(?:es)?)?", norm)
            if selected:
                index = int(selected.group(1)) - 1
                choices = renewal_options["sales"]
                if 0 <= index < len(choices):
                    state["renewal_options"] = None
                    months = int(selected.group(2)) if selected.group(2) else 1
                    return await self.quote_renewal(customer, choices[index], months, state, batch_id)
        if renewal:
            state["pending_renewal"] = None
        if renewal_options:
            state["renewal_options"] = None
        direct_support = (
            "asesor", "humano", "reclamo", "hablar con alguien",
            "hablar con una persona", "agente de soporte",
        )
        legacy_support = ("no funciona", "problema", "contrasena") if not self.intent_model else ()
        if any(word in norm for word in direct_support + legacy_support):
            state.update(human_paused=True, pending=None)
            return Reply(
                "Dejé esta conversación pendiente de atención humana y pausé las respuestas automáticas. "
                "Un asesor revisará tu consulta y te responderá por este chat.",
                state,
            )
        if not customer and any(
            word in norm
            for word in (
                "registr",
                "crear cuenta",
                "abrir cuenta",
                "soy nuevo",
                "no tengo cuenta",
            )
        ):
            intro = greeting(context, text, history)
            return Reply(
                " ".join(
                    part for part in (intro, registration_note(self.settings.store_url)) if part
                ),
                state,
            )
        recharge_requested = any(
            word in norm for word in ("recarg", "yape", "plin", "comprobante", "ya pague")
        ) or bool(
            state.get("recharge_focus", 0) > time.time()
            and re.search(r"\b(?:pasos|como|donde|numero|captura)\b", norm)
        )
        if recharge_requested:
            if not customer:
                return Reply(registration_note(self.settings.store_url), state)
            state["recharge_focus"] = time.time() + self.settings.quote_ttl_seconds
            if "ya pague" in norm:
                amount = state.get("recharge_amount")
                if amount:
                    return Reply(
                        f"Si ya hiciste el Yape de S/ {money(amount):.2f}, no vuelvas a pagar. "
                        "Envíame la captura completa con monto, hora, receptor y código de operación; "
                        "revisaré el pago antes de acreditar tu billetera.",
                        state,
                    )
                return Reply(
                    "Si ya pagaste, no repitas el Yape. Dime el monto exacto que enviaste y "
                    "después mándame la captura para revisarla.",
                    state,
                )
            if self.settings.whatsapp_recharge_enabled:
                destination = f" al Yape {self.settings.recharge_yape_phone}" if self.settings.recharge_yape_phone else " al número indicado en la tienda"
                receiver = f" Verifica que el receptor sea {self.settings.recharge_receiver}." if self.settings.recharge_receiver else ""
                return Reply(
                    f"Para recargar, envía al menos S/ {self.settings.recharge_min_amount}{destination}.{receiver} "
                    "Dime aquí el monto exacto y luego envíame una captura clara con monto, receptor, hora y código de operación. "
                    "Si ya pagaste, solo dime cuánto fue: no vuelvas a transferir.", state
                )
            return self.speech(
                state,
                "recharge",
                wallet=wallet_note(context),
                steps=recharge_note(self.settings),
            )
        if negative(text):
            state["pending"] = None
            state["pending_renewal"] = None
            state["renewal_options"] = None
            state["recharge_focus"] = 0
            state.pop("recharge_amount", None)
            return Reply("Pedido cancelado. Puedes consultar el catálogo cuando quieras.", state)
        # Las confirmaciones, pagos y elecciones de un pedido en curso se
        # validan con reglas y estado. El modelo dirige consultas libres.
        if self.intent_model and not affirmative(text) and (
            not pending or (prediction and prediction.intent == "reclamo")
        ):
            confident = bool(
                prediction and prediction.in_vocabulary
                and prediction.confidence >= self.settings.intent_confidence_threshold
                and prediction.margin >= self.settings.intent_margin_threshold
            )
            route = prediction.intent if confident else "clarification"
            logging.getLogger("dadtv.bot").info("route_ml=%s", route)
            if not confident:
                return Reply(
                    "No estoy seguro de lo que necesitas. ¿Quieres consultar un producto, "
                    "hacer un pedido o hablar con un asesor?", state,
                )
            if route == "reclamo":
                state.update(human_paused=True, pending=None)
                return Reply(
                    "Entiendo que tienes un problema con el servicio. Dejé tu caso para "
                    "atención humana y pausé mis respuestas automáticas. Un asesor "
                    "revisará tu consulta por este chat.", state,
                )
            if route == "fuera_de_tema":
                return Reply(
                    "Puedo ayudarte con suscripciones y productos digitales de DadTv Store. "
                    "¿Buscas alguna plataforma?", state,
                )
            if route == "ofensivo":
                return Reply(
                    "Quiero ayudarte. Si tienes un problema con tu compra o cuenta, "
                    "cuéntame qué ocurrió o escribe «asesor».", state,
                )
            if route == "spam":
                return Reply(
                    "Si necesitas ayuda con una plataforma, compra o tu cuenta, "
                    "escríbeme qué buscas.", state,
                )
        try:
            if affirmative(text) and pending and pending.get("quote"):
                return await self.checkout(phone, pending, state)
            if affirmative(text):
                return Reply(
                    "Primero elige un producto y revisa el total antes de confirmar.", state
                )
            listed_method = getattr(self.catalog, "listed_products", self.catalog.products)
            listed = await asyncio.to_thread(listed_method)
            products = [product for product in listed if product.stock > 0]
            discount = await asyncio.to_thread(self.catalog.discount, customer)
            mentioned = mentioned_products(text, listed)
            available_mentioned = mentioned_products(text, products)
            if not mentioned and re.search(
                r"\b(?:ropa|zapatos?|comida|muebles?|electrodomesticos?)\b", norm
            ):
                state["pending"] = None
                return self.speech(
                    state,
                    "off_topic",
                    answer="Por ahora vendo productos digitales y suscripciones. ¿Buscas alguna plataforma en particular?",
                )
            comparing = bool(
                re.search(
                    r"\b(?:diferencia|diferente|comparar|comparacion|versus|vs|conviene)\b",
                    norm,
                )
            )
            focus = state.get("catalog_focus") or {}
            from_focus = False
            if (
                not mentioned
                and focus.get("expires_at", 0) > time.time()
                and (comparing or re.search(r"\b(?:premium|standar|standard|estandar)\b", norm))
            ):
                from_focus = True
                ids = set(focus.get("service_ids", []))
                mentioned = [p for p in listed if p.service_id in ids]
                available_mentioned = [p for p in products if p.service_id in ids]
                if "premium" in norm and "standar" not in norm:
                    mentioned = [p for p in mentioned if "premium" in normalize(p.platform)]
                    available_mentioned = [
                        p for p in available_mentioned if "premium" in normalize(p.platform)
                    ]
                elif (
                    re.search(r"\b(?:standar|standard|estandar)\b", norm) and "premium" not in norm
                ):
                    mentioned = [
                        p
                        for p in mentioned
                        if re.search(r"\b(?:standar|standard|estandar)\b", normalize(p.platform))
                    ]
                    available_mentioned = [
                        p
                        for p in available_mentioned
                        if re.search(r"\b(?:standar|standard|estandar)\b", normalize(p.platform))
                    ]
            if mentioned and not from_focus:
                state["catalog_focus"] = {
                    "service_ids": list(dict.fromkeys(p.service_id for p in mentioned)),
                    "expires_at": time.time() + self.settings.quote_ttl_seconds,
                }
            if comparing and len({p.platform for p in mentioned}) > 1:
                return self.speech(
                    state, "information", answer=listed_answer(mentioned, comparison=True)
                )
            if mentioned and not available_mentioned:
                state["pending"] = None
                return self.speech(state, "unavailable", answer=listed_answer(mentioned))
            # El modelo y las alternativas ven el precio que corresponde a este cliente.
            offered = [
                replace(
                    p,
                    price=money(
                        quote_products(
                            [p],
                            [{"product_id": p.id, "qty": 1}],
                            discount,
                            premium=context["premium"],
                        )["total"]
                    ),
                )
                for p in products
            ]
            decision = await self.assistant.decide(text, offered, history, pending, context)
            action = decision["action"]
            if self.intent_model and prediction and not pending:
                if prediction.intent == "consulta" and action == "order":
                    action = "catalog"
                    decision = {
                        "action": "catalog", "selections": [],
                        "product_ids": [p.id for p in available_mentioned],
                    }
                elif prediction.intent == "pedido" and action in {"chat", "support", "balance"}:
                    action = "catalog"
                    decision = {
                        "action": "catalog", "selections": [],
                        "product_ids": [p.id for p in available_mentioned],
                    }
            if action == "order":
                selections = decision.get("selections") or []
                requested_ids = {p.id for p in available_mentioned}
                # Una cotizacion de varios planes requiere una eleccion explicita del cliente.
                # La IA puede interpretar "quiero Apple TV" como comprar 1 y 3 meses.
                if len(selections) != 1 or (
                    requested_ids and selections[0].get("product_id") not in requested_ids
                ):
                    action = "catalog"
                    decision = {
                        "action": "catalog",
                        "product_ids": (
                            [p.id for p in available_mentioned]
                            if requested_ids
                            else [
                                item.get("product_id")
                                for item in selections
                                if item.get("product_id") in {p.id for p in offered}
                            ]
                        ),
                        "selections": [],
                    }
            if action == "unavailable":
                return self.speech(
                    state,
                    "unavailable",
                    answer="Ahora mismo no veo disponible lo que buscas. ¿Quieres que revise otra plataforma o prefieres hablar con un asesor?",
                )
            # Algunos modelos devuelven "catalog" al elegir una opción única.
            # Convertirla en cotización es seguro: el cobro exige otra confirmación.
            if action == "catalog" and pending and pending.get("options"):
                chosen = decision.get("product_ids", [])
                selecting = re.match(
                    r"^(?:el|la|ese|esa|quiero|prefiero|me quedo|dame|ponme|mejor)\b",
                    norm,
                )
                question = any(
                    word in norm
                    for word in ("cuanto", "que incluye", "como", "?", "precio", "informacion")
                )
                if (
                    selecting
                    and not question
                    and len(chosen) == 1
                    and chosen[0] in pending["options"]
                ):
                    action = "order"
                    decision = {
                        "action": "order",
                        "selections": [{"product_id": chosen[0], "qty": 1}],
                    }
            if action == "chat":
                ids = decision.get("product_ids", [])
                relevant = [p for p in offered if p.id in ids]
                if any(w in norm for w in ("gracias", "perfecto")):
                    return self.speech(
                        state, "thanks", answer="¡De nada! Aquí estoy si necesitas algo más."
                    )
                if any(
                    w in norm
                    for w in (
                        "dispositivo",
                        "garantia",
                        "pantalla",
                        "como funciona",
                        "que incluye",
                        "anuncios",
                        "televisor",
                        "mi tv",
                    )
                ):
                    descriptions = list(
                        dict.fromkeys(p.description for p in relevant if p.description)
                    )
                    return self.speech(
                        state,
                        "information",
                        answer=" ".join(descriptions)
                        if descriptions
                        else "No tengo verificado ese detalle todavía. ¿Quieres que un asesor te lo confirme?",
                    )
                if not any(
                    word in norm
                    for word in ("hola", "buenas", "catalogo", "plataforma", "venden", "tienen")
                ):
                    return self.speech(
                        state,
                        "off_topic",
                        answer="Te puedo ayudar con productos, disponibilidad o tu cuenta en DadTv Store. ¿Qué te gustaría consultar?",
                    )
                stock_by_platform = {}
                for product in products:
                    stock_by_platform[product.platform] = max(
                        stock_by_platform.get(product.platform, 0), product.stock
                    )
                names = sorted(
                    stock_by_platform,
                    key=lambda name: (-stock_by_platform[name], normalize(name)),
                )[:5]
                return self.speech(
                    state,
                    "greeting",
                    answer="¿Qué plataforma estás buscando? "
                    + (
                        f"Tenemos {', '.join(names)}."
                        if names
                        else "Puedo ayudarte a revisar disponibilidad."
                    ),
                )
            if action == "support":
                # Los reclamos y pedidos explícitos de asesor se atendieron arriba.
                # Una clasificación aislada de la IA (p. ej. "¿por qué?") no debe pausar el chat.
                return self.speech(
                    state,
                    "information",
                    answer="Claro, dime qué parte quieres que te aclare: el plan, el precio o la recarga.",
                )
            if action == "balance":
                if not customer:
                    return Reply(registration_note(self.settings.store_url), state)
                total = pending.get("quote", {}).get("total") if pending else None
                ask = (
                    "¿Quieres que active el pedido que elegiste?"
                    if total and money(context["balance"]) >= money(total)
                    else f"Para recargar, entra a {self.settings.store_url}, inicia sesión y abre Billetera → Recarga automática."
                )
                return self.speech(
                    state,
                    "balance",
                    wallet=wallet_note(context, total, self.settings.store_url),
                    ask=ask,
                )
            if action == "order":
                quote = quote_products(
                    products,
                    decision.get("selections"),
                    discount,
                    premium=bool(customer and customer.get("premium")),
                )
                order_id = str(
                    uuid5(NAMESPACE_URL, f"dadtv:{self.settings.tenant_id}:{phone}:{batch_id}")
                )
                state["recharge_focus"] = 0
                state.pop("recharge_amount", None)
                state["pending"] = {
                    "order_id": order_id,
                    "quote": quote,
                    "expires_at": time.time() + self.settings.quote_ttl_seconds,
                }
                by_id = {p.id: p for p in products}
                lines = []
                for item in quote["items"]:
                    p = by_id[item["product_id"]]
                    kind = (
                        ("un perfil" if p.kind == "perfil" else "una cuenta completa")
                        if item["qty"] == 1
                        else f"{item['qty']} {'perfiles' if p.kind == 'perfil' else 'cuentas completas'}"
                    )
                    duration = "un mes" if p.months == 1 else f"{p.months} meses"
                    lines.append(
                        f"{kind} de {p.platform} por {duration}, a S/ {money(item['unit_price']) * item['qty']:.2f}"
                    )
                offer = (
                    "Tengo " + lines[0] + "."
                    if len(lines) == 1
                    else "Te puedo preparar "
                    + "; ".join(lines)
                    + f". En total, S/ {money(quote['total']):.2f}."
                )
                ask = (
                    "¿Te lo activo con tu saldo?"
                    if context["registered"] and money(context["balance"]) >= money(quote["total"])
                    else "¿Quieres seguir con esta opción? Si te falta saldo, te paso los datos de Yape para recargar."
                )
                return self.speech(
                    state,
                    "quote",
                    offer=offer,
                    wallet=wallet_note(
                        context,
                        quote["total"],
                        "" if self.settings.whatsapp_recharge_enabled else self.settings.store_url,
                    ),
                    ask=ask,
                )
            selected_ids = decision.get("product_ids", [])
            requested = re.search(
                r"\b(?:quiero comprar|busco|necesito|tienen|hay)\s+([\w+.-]+)", norm
            )
            if (
                not selected_ids
                and requested
                and requested[1]
                not in {"algo", "un", "una", "cuentas", "perfiles", "productos", "planes"}
            ):
                return self.speech(
                    state,
                    "unavailable",
                    answer="Ahora mismo no veo disponible lo que buscas. ¿Quieres que revise otra plataforma o prefieres hablar con un asesor?",
                )
            options = (
                [p for p in offered if p.id in selected_ids]
                if selected_ids
                else [p for p in offered if p.months == 1]
            )
            if not options:
                return Reply(
                    "No encontré opciones disponibles en este momento. Puedes solicitar un asesor.",
                    state,
                )
            # Mantener el orden y los IDs ofrecidos para interpretar «el 2».
            options = options[:30]
            state["recharge_focus"] = 0
            state.pop("recharge_amount", None)
            state["pending"] = {
                "options": [p.id for p in options],
                "expires_at": time.time() + self.settings.quote_ttl_seconds,
            }
            lines = "\n".join(
                f"{index}. {p.label}: S/ {p.price:.2f}" for index, p in enumerate(options, 1)
            )
            return self.speech(
                state,
                "options",
                options=lines,
                wallet=wallet_note(context, store_url=self.settings.store_url),
                ask="¿Cuál te interesa? Puedes decirme el plan o el número de la opción.",
            )
        except BusinessError as error:
            return Reply(str(error), state)

    async def checkout(self, phone, pending, state):
        if self.settings.mode != "live" or self.settings.commerce_enabled:
            result = await asyncio.to_thread(
                self.commerce.existing_result, pending["order_id"], phone
            )
            if result:
                return self.delivery(result, pending, state)
        if pending["expires_at"] <= time.time():
            state["pending"] = None
            return Reply(
                "La cotización venció. Consulta el catálogo para revisar el precio actual.", state
            )
        customer = await asyncio.to_thread(self.catalog.customer, phone)
        if not customer:
            return Reply(registration_note(self.settings.store_url), state)
        quote = pending["quote"]
        balance, total = money(customer.get("saldo") or 0), money(quote["total"])
        if balance < total:
            if self.settings.whatsapp_recharge_enabled and self.settings.recharge_yape_phone:
                missing = money(total - balance)
                amount = max(missing, money(self.settings.recharge_min_amount))
                state["recharge_focus"] = time.time() + self.settings.quote_ttl_seconds
                state["recharge_amount"] = str(amount)
                names = ", ".join(item["label"] for item in quote["items"])
                return self.speech(
                    state,
                    "payment_instructions",
                    product=f"Elegiste {names} por S/ {total:.2f}.",
                    balance=f"Tienes S/ {balance:.2f}; te faltan S/ {missing:.2f}. La recarga mínima por Yape es S/ {self.settings.recharge_min_amount}, así que recarga S/ {amount:.2f} y el resto quedará en tu billetera.",
                    destination=f"Yape: {self.settings.recharge_yape_phone}. Titular: {self.settings.recharge_receiver}.",
                    next_step="Cuando hagas el pago, envíame la captura con el monto, la hora y el código de operación. Solo acreditaré el saldo si se valida; después vuelve a confirmar la compra. No vuelvas a pagar si ya hiciste el Yape.",
                )
            return Reply(
                f"Tu saldo no alcanza para S/ {total:.2f}. "
                f"Recarga en {self.settings.store_url} y luego escribe «confirmo».",
                state,
            )
        if self.settings.mode == "live" and not self.settings.commerce_enabled:
            return Reply(
                "Tu saldo alcanza, pero la compra por WhatsApp sigue en pruebas y no haré el cargo. "
                f"Puedes completar el pedido en {self.settings.store_url}.",
                state,
            )
        # La RPC valida total y stock nuevamente, sin confiar en la IA ni en esta lectura.
        result = await asyncio.to_thread(self.commerce.purchase, pending["order_id"], phone, quote)
        return self.delivery(result, pending, state)

    @staticmethod
    def renewal_months(text):
        match = re.search(r"\b(1|3|6|12)\s*mes(?:es)?\b", text)
        return int(match.group(1)) if match else 1

    async def quote_renewal(self, customer, sale, months, state, batch_id):
        allowed = sale.get("allowed_months", [1])
        if months not in allowed:
            choices = ", ".join(f"{value} {'mes' if value == 1 else 'meses'}" for value in allowed)
            return Reply(f"Para esta cuenta puedes renovar por {choices}.", state)
        listed = await asyncio.to_thread(self.catalog.listed_products if hasattr(self.catalog, "listed_products") else self.catalog.products)
        product = next((p for p in listed if p.service_id == sale["service_id"] and p.kind == sale["kind"] and p.months == months), None)
        if not product:
            return Reply("Ese plazo no está disponible para renovar esta cuenta. Puedes elegir otro plazo o escribir «asesor».", state)
        discount = await asyncio.to_thread(self.catalog.discount, customer)
        quote = quote_products([replace(product, stock=1)], [{"product_id": product.id, "qty": 1}], discount, premium=bool(customer.get("premium")))
        total = money(quote["total"])
        if total <= 0:
            return Reply("Esta renovación necesita revisión de un asesor.", state)
        order_id = str(uuid5(NAMESPACE_URL, self.settings.tenant_id + ":renewal:" + batch_id))
        state["pending_renewal"] = {
            "order_id": order_id,
            "sale_id": sale["id"],
            "service_id": sale["service_id"],
            "service": sale["service"],
            "kind": sale["kind"],
            "expiration": sale["expiration"],
            "allowed_months": allowed,
            "months": months,
            "total": quote["total"],
            "expires_at": time.time() + self.settings.quote_ttl_seconds,
        }
        balance = money(customer.get("saldo") or 0)
        duration = f"{months} {'mes' if months == 1 else 'meses'}"
        return Reply(
            f"Renovar {sale['service']} ({sale['kind']}) por {duration} cuesta S/ {total:.2f}. "
            f"Vence actualmente el {sale['expiration']} y tienes S/ {balance:.2f} de saldo. "
            "Si estás de acuerdo, responde «confirmo». No se cobrará hasta que confirmes.",
            state,
        )

    async def checkout_renewal(self, phone, pending, state):
        if self.settings.mode == "live" and not self.settings.renewal_enabled:
            return Reply("La renovación por WhatsApp está en pruebas. Aún no se descontó tu saldo.", state)
        result = await asyncio.to_thread(self.commerce.existing_renewal_result, pending["order_id"], phone)
        if not result:
            if pending["expires_at"] <= time.time():
                state["pending_renewal"] = None
                return Reply("La cotización venció. Escribe «renovar» para consultar el precio actual.", state)
            customer = await asyncio.to_thread(self.catalog.customer, phone)
            if not customer:
                return Reply(registration_note(self.settings.store_url), state)
            if money(customer.get("saldo") or 0) < money(pending["total"]):
                return Reply(f"Te faltan fondos para renovar por S/ {money(pending['total']):.2f}. Escribe «recargar» y te guío.", state)
            result = await asyncio.to_thread(self.commerce.renew, pending["order_id"], phone, pending)
        state["pending_renewal"] = None
        prefix = "[RENOVACIÓN SIMULADA] " if result.get("demo") else ""
        months = result["months"]
        duration = f"{months} {'mes' if months == 1 else 'meses'}"
        return Reply(
            prefix + f"Renové {result['servicio']} por {duration}. "
            f"Nuevo vencimiento: {result['fecha_vencimiento']}. "
            f"Desconté S/ {money(result['total']):.2f}; tu saldo es S/ {money(result['current_balance']):.2f}.",
            state,
            history_text="Renovación registrada y saldo actualizado.",
        )

    def delivery(self, result, pending, state):
        state["pending"] = None
        state["last_order_id"] = pending["order_id"]
        lines = []
        for sale in result["sales"]:
            line = (
                f"• {sale['servicio']}\nCorreo: {sale['correo']}\nContraseña: {sale['contrasena']}"
            )
            if sale.get("perfil") is not None:
                line += f"\nPerfil: {sale['perfil']}"
            if sale.get("pin") is not None:
                line += f"\nPIN: {sale['pin']}"
            line += f"\nVencimiento: {sale['fecha_vencimiento']}"
            lines.append(line)
        prefix = "[COMPRA SIMULADA — credenciales sin validez]\n" if result.get("demo") else ""
        return Reply(
            prefix + "Tu pedido quedó registrado.\n" + "\n\n".join(lines),
            state,
            history_text="Pedido registrado. Credenciales preparadas para entrega por WhatsApp.",
        )
