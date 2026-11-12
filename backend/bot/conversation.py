import re

from bot.domain import money, normalize


def customer_context(customer):
    """No exponer ID de cliente, teléfono, correo ni credenciales al modelo."""
    return {
        "registered": bool(customer),
        "name": (customer.get("nombre") or "").strip().split(" ")[0] if customer else "",
        "balance": str(money(customer.get("saldo") or 0)) if customer else None,
        "premium": bool(customer and customer.get("premium")),
    }


def greeting(context, text, history):
    if history and not re.search(r"\b(hola|buenas|buenos dias)\b", normalize(text)):
        return ""
    return f"¡Hola, {context['name']}!" if context.get("name") else "¡Hola!"


def registration_note(store_url):
    return (
        "Todavía no encuentro una cuenta asociada a este WhatsApp. "
        f"Puedes crearla en {store_url}: pulsa «Ingresar / Registrar», luego «Registrarme» "
        "y usa este mismo número. Cuando termines, avísame y revisaré tu cuenta."
    )


def registration_completed(text):
    norm = normalize(text)
    return bool(
        re.search(
            r"\b(?:ya me registre|ya me he registrado|ya estoy registrad[oa]|"
            r"me acabo de registrar|termine de registrarme|ya cree mi cuenta|"
            r"ya tengo mi cuenta)\b",
            norm,
        )
    )


def mentioned_products(text, products):
    words = set(re.findall(r"[a-z0-9]+", normalize(text)))
    generic = {"premium", "standard", "standar", "estandar", "platino", "video", "plan", "cuenta"}
    matches = [
        p
        for p in products
        if any(
            word in words
            for word in re.findall(r"[a-z0-9]+", normalize(p.platform))
            if (len(word) >= 4 or word in {"hbo", "dgo", "vix"}) and word not in generic
        )
    ]
    if "premium" in words and not words.intersection({"standar", "standard", "estandar"}):
        matches = [p for p in matches if "premium" in normalize(p.platform)]
    elif words.intersection({"standar", "standard", "estandar"}) and "premium" not in words:
        matches = [
            p
            for p in matches
            if re.search(r"\b(?:standar|standard|estandar)\b", normalize(p.platform))
        ]
    elif "platino" in words and "no platino" not in normalize(text):
        matches = [p for p in matches if "platino" in normalize(p.platform)]
    return matches


def listed_answer(products, comparison=False):
    variants = {}
    for product in products:
        variants.setdefault(product.platform, []).append(product)
    details = []
    descriptions = {}
    for name, offers in variants.items():
        available = any(p.stock > 0 for p in offers)
        details.append(f"{name} ({'con stock' if available else 'sin stock ahora'})")
        description = next((p.description.strip() for p in offers if p.description.strip()), "")
        lines = [line.strip() for line in description.splitlines() if line.strip()]
        lines = [
            line
            for line in lines
            if "http" not in line.lower()
            and not any(word in normalize(line) for word in ("consulta", "codigo"))
            and "descripcion y uso" not in normalize(line)
        ]
        descriptions[name] = " ".join(lines[:5])[:400].strip()
    intro = "En el catálogo figuran " + ", ".join(details) + "."
    if not comparison:
        return intro + " Si quieres, puedo revisar otra plataforma disponible."
    distinct = {value for value in descriptions.values() if value}
    if len(distinct) > 1 and all(descriptions.values()):
        return (
            intro
            + " "
            + " ".join(f"{name}: {description}" for name, description in descriptions.items())
        )
    return (
        intro
        + " No tengo una diferencia de funciones verificada entre esos planes; un asesor puede confirmártela."
    )


def wallet_note(context, total=None, store_url=""):
    if not context["registered"]:
        return registration_note(store_url)
    balance = money(context["balance"])
    if total is None:
        return f"Tienes S/ {balance:.2f} en tu billetera."
    total = money(total)
    if balance >= total:
        return f"Tienes S/ {balance:.2f} en tu billetera, así que te alcanza."
    shortfall = f"Tienes S/ {balance:.2f}; te faltan S/ {total - balance:.2f}."
    return shortfall + (f" Puedes recargar en {store_url}." if store_url else "")


def recharge_note(settings):
    destination = (
        f" al Yape {settings.recharge_yape_phone}"
        if settings.recharge_yape_phone
        else " usando el número o QR mostrado allí"
    )
    receiver = (
        f" Comprueba que el receptor sea {settings.recharge_receiver}."
        if settings.recharge_receiver
        else " Comprueba el receptor antes de confirmar."
    )
    return (
        f"Entra a {settings.store_url}, inicia sesión y abre Billetera → Recarga automática. "
        f"Si aún no pagaste, envía S/ {settings.recharge_min_amount} o más{destination}."
        + receiver
        + " En el formulario indica el mismo monto y sube una captura clara donde se vean receptor, "
        f"monto, hora e ID de operación, dentro de {settings.recharge_window_minutes} minutos del pago. "
        "Si ya pagaste, no vuelvas a hacerlo: sube allí el comprobante. "
        "La tienda te avisará si acredita el saldo o si necesita revisión. "
        "Por ahora este chat no valida capturas ni acredita recargas."
    )


def expand_template(template, fragments):
    """Rechazar redacciones que omitan hechos o incorporen importes y acciones nuevas."""
    if not isinstance(template, str) or len(template) > 1500:
        raise ValueError("Redacción inválida")
    names = re.findall(r"\{([a-z_]+)\}", template)
    if set(names) != set(fragments) or len(names) != len(fragments):
        raise ValueError("La redacción cambió los datos necesarios")
    remaining = re.sub(r"\{[a-z_]+\}", "", template)
    if re.search(r"[{}\d$]|S\s*/|https?://|www\.|@", remaining):
        raise ValueError("La redacción agregó cifras")
    if re.search(
        r"\b(saldo|precio|soles?|cuesta|costo|pagado|pagaste|paga|pagar|cobre|cobrado|activado|activada|confirmado|confirmada|confirmas|confirmar|compra|comprar)\b",
        normalize(remaining),
    ):
        raise ValueError("La redacción agregó afirmaciones comerciales")
    return re.sub(r"\{([a-z_]+)\}", lambda match: fragments[match[1]], template).strip()
