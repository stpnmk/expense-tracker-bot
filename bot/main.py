from __future__ import annotations

import asyncio
import logging
import os

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_URL = os.environ.get("API_URL", "http://localhost:8000")

dp = Dispatcher()


@dp.message(Command("start"))
async def start(message: Message) -> None:
    await message.answer(
        "Привет! Я помогаю считать расходы.\n"
        "Запиши трату в формате «сумма категория», например «300 еда».\n"
        "Команда /stats покажет сводку по категориям."
    )


@dp.message(Command("stats"))
async def stats(message: Message) -> None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_URL}/stats",
                params={"user_id": message.from_user.id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка запроса статистики: %s", exc)
        await message.answer("Не получилось получить статистику, попробуй позже.")
        return

    if not data["by_category"]:
        await message.answer("Пока нет расходов. Запиши первый: «300 еда».")
        return

    rows = sorted(data["by_category"].items(), key=lambda item: item[1], reverse=True)
    lines = [f"- {category}: {amount:.0f}" for category, amount in rows]
    await message.answer(
        "Расходы по категориям:\n" + "\n".join(lines) + f"\n\nИтого: {data['total']:.0f}"
    )


@dp.message(F.text)
async def add_expense(message: Message) -> None:
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: «сумма категория», например «300 еда».")
        return

    try:
        amount = float(parts[0].replace(",", "."))
    except ValueError:
        await message.answer("Сумма должна быть числом. Пример: «300 еда».")
        return

    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return

    category = parts[1].strip().lower()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_URL}/expenses",
                json={"user_id": message.from_user.id, "amount": amount, "category": category},
                timeout=10,
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка сохранения расхода: %s", exc)
        await message.answer("Не получилось сохранить расход, попробуй позже.")
        return

    await message.answer(f"Записал: {amount:.0f} - {category}.")


async def main() -> None:
    bot = Bot(BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
