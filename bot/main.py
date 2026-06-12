from __future__ import annotations

import asyncio
import logging
import os

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_URL = os.environ.get("API_URL", "http://localhost:8000")

CATEGORIES = ["еда", "кафе", "транспорт", "аренда", "развлечения", "прочее"]
PERIODS = {"today": "сегодня", "week": "за неделю", "month": "за месяц", "all": "за всё время"}

dp = Dispatcher(storage=MemoryStorage())


class AddExpense(StatesGroup):
    waiting_amount = State()


def categories_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"cat:{name}")]
        for name in CATEGORIES
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def parse_amount(text: str) -> float | None:
    try:
        amount = float(text.replace(",", "."))
    except ValueError:
        return None
    return amount if amount > 0 else None


def format_saved(data: dict) -> str:
    text = f"Записал: {data['amount']:.0f} - {data['category']}."
    budget = data.get("budget")
    if budget:
        text += f"\nБюджет «{data['category']}»: {budget['spent']:.0f}/{budget['limit']:.0f}."
        if budget["exceeded"]:
            text += " Лимит превышен!"
    return text


async def save_expense(user_id: int, amount: float, category: str) -> dict | None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_URL}/expenses",
                json={"user_id": user_id, "amount": amount, "category": category},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка сохранения расхода: %s", exc)
        return None


@dp.message(Command("start"))
async def start(message: Message) -> None:
    await message.answer(
        "Привет! Я помогаю считать расходы.\n\n"
        "Быстрый ввод: «300 еда».\n"
        "/add - добавить трату через кнопки.\n"
        "/stats [today|week|month|all] - сводка по категориям.\n"
        "/last - последние траты.\n"
        "/undo - удалить последнюю трату.\n"
        "/budget <категория> <сумма> - задать месячный лимит.\n"
        "/budgets - лимиты и остатки."
    )


@dp.message(Command("add"))
async def add_start(message: Message) -> None:
    await message.answer("Выбери категорию:", reply_markup=categories_keyboard())


@dp.callback_query(F.data.startswith("cat:"))
async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(AddExpense.waiting_amount)
    await callback.message.answer(f"Категория «{category}». Введи сумму:")
    await callback.answer()


@dp.message(AddExpense.waiting_amount, F.text & ~F.text.startswith("/"))
async def add_amount(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text.strip())
    if amount is None:
        await message.answer("Сумма должна быть положительным числом. Попробуй ещё раз:")
        return
    data = await state.get_data()
    await state.clear()
    saved = await save_expense(message.from_user.id, amount, data["category"])
    if saved is None:
        await message.answer("Не получилось сохранить расход, попробуй позже.")
        return
    await message.answer(format_saved(saved))


@dp.message(Command("stats"))
async def stats(message: Message, command: CommandObject) -> None:
    period = (command.args or "month").strip().lower()
    if period not in PERIODS:
        await message.answer("Период: today, week, month или all. Пример: /stats week")
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_URL}/stats",
                params={"user_id": message.from_user.id, "period": period},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка запроса статистики: %s", exc)
        await message.answer("Не получилось получить статистику, попробуй позже.")
        return

    if not data["by_category"]:
        await message.answer(f"Нет расходов {PERIODS[period]}.")
        return
    rows = sorted(data["by_category"].items(), key=lambda item: item[1], reverse=True)
    lines = [f"- {category}: {amount:.0f}" for category, amount in rows]
    await message.answer(
        f"Расходы {PERIODS[period]}:\n" + "\n".join(lines) + f"\n\nИтого: {data['total']:.0f}"
    )


@dp.message(Command("last"))
async def last(message: Message) -> None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_URL}/expenses",
                params={"user_id": message.from_user.id, "limit": 5},
                timeout=10,
            )
            resp.raise_for_status()
            rows = resp.json()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка запроса последних трат: %s", exc)
        await message.answer("Не получилось получить список, попробуй позже.")
        return

    if not rows:
        await message.answer("Пока нет расходов.")
        return
    lines = [f"- {row['amount']:.0f} - {row['category']}" for row in rows]
    await message.answer("Последние траты:\n" + "\n".join(lines))


@dp.message(Command("undo"))
async def undo(message: Message) -> None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{API_URL}/expenses/last",
                params={"user_id": message.from_user.id},
                timeout=10,
            )
            if resp.status_code == 404:
                await message.answer("Нет трат для удаления.")
                return
            resp.raise_for_status()
            deleted = resp.json()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка удаления траты: %s", exc)
        await message.answer("Не получилось удалить, попробуй позже.")
        return
    await message.answer(f"Удалил: {deleted['amount']:.0f} - {deleted['category']}.")


@dp.message(Command("budget"))
async def set_budget(message: Message, command: CommandObject) -> None:
    if not command.args or len(command.args.rsplit(maxsplit=1)) < 2:
        await message.answer("Формат: /budget <категория> <сумма>. Пример: /budget еда 5000")
        return
    category, amount_raw = command.args.rsplit(maxsplit=1)
    amount = parse_amount(amount_raw)
    if amount is None:
        await message.answer("Сумма лимита должна быть положительным числом.")
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_URL}/budgets",
                json={"user_id": message.from_user.id, "category": category.strip().lower(), "limit": amount},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка установки бюджета: %s", exc)
        await message.answer("Не получилось задать лимит, попробуй позже.")
        return
    await message.answer(f"Лимит «{data['category']}»: {data['limit']:.0f} в месяц.")


@dp.message(Command("budgets"))
async def budgets(message: Message) -> None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_URL}/budgets",
                params={"user_id": message.from_user.id},
                timeout=10,
            )
            resp.raise_for_status()
            rows = resp.json()
    except httpx.HTTPError as exc:
        logging.warning("Ошибка запроса бюджетов: %s", exc)
        await message.answer("Не получилось получить лимиты, попробуй позже.")
        return

    if not rows:
        await message.answer("Лимиты не заданы. Пример: /budget еда 5000")
        return
    lines = [
        f"- {row['category']}: {row['spent']:.0f}/{row['limit']:.0f} (остаток {row['remaining']:.0f})"
        for row in rows
    ]
    await message.answer("Лимиты на месяц:\n" + "\n".join(lines))


@dp.message(F.text, StateFilter(None))
async def quick_add(message: Message) -> None:
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: «сумма категория», например «300 еда». Или /add.")
        return
    amount = parse_amount(parts[0])
    if amount is None:
        await message.answer("Сумма должна быть положительным числом. Пример: «300 еда».")
        return
    saved = await save_expense(message.from_user.id, amount, parts[1].strip().lower())
    if saved is None:
        await message.answer("Не получилось сохранить расход, попробуй позже.")
        return
    await message.answer(format_saved(saved))


async def main() -> None:
    bot = Bot(BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
