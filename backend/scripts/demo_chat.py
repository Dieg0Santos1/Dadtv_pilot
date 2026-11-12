"""Demo local. --ai activa únicamente OpenAI con catálogo ficticio, nunca compras reales."""

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.settings import Settings  # noqa: E402
from main import build_service  # noqa: E402


async def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    backend_dir = Path(__file__).resolve().parents[1]
    arguments = sys.argv[1:]
    use_ai = "--ai" in arguments
    if use_ai:
        from dotenv import load_dotenv

        load_dotenv(backend_dir / ".env")
    settings = replace(
        Settings.from_env() if use_ai else Settings(),
        mode="demo",
        engine="openai" if use_ai else "rules",
        send_enabled=False,
        commerce_enabled=False,
        database_path=str(backend_dir / "data" / "demo-cli.sqlite3"),
    )
    service = build_service(settings)
    phone = "51900000000"
    service.repository.pause(phone, False)
    arguments = [argument for argument in arguments if argument != "--ai"]
    messages = iter(arguments) if arguments else None
    print("DadTvPlay — DEMO LOCAL. Precios ficticios; no se envían mensajes ni se cobra dinero.")
    if use_ai:
        print("Interpretación y redacción mediante OpenAI: utiliza tu clave API y consume tokens.")
    print("Prueba: hola → Netflix perfil → confirmo. Escribe salir para cerrar.")
    while True:
        try:
            text = next(messages) if messages else input("Tú: ")
        except (StopIteration, EOFError):
            break
        if text.strip().lower() == "salir":
            break
        from uuid import uuid4

        service.repository.enqueue(str(uuid4()), phone, text)
        await service.process_once()
        while row := service.repository.claim_outbox():
            print(f"Bot: {row['body']}\n")
            service.repository.finish_outbox(row["id"], True)


if __name__ == "__main__":
    asyncio.run(main())
