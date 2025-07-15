"""
price_monitor.py
────────────────────────────────────────────────────────
Отслеживает цены на странице товара/каталога и шлёт
уведомление в Telegram при изменении ≥ THRESHOLD %.

• Условия (env-переменные, задаются в Secrets репозитория):
  BOT_TOKEN        – токен Telegram-бота
  CHAT_ID          – chat-id (integer) получателя уведомлений
  URL              – страница с товарами
  SELECTOR_NAME    – CSS-селектор названия товара
  SELECTOR_PRICE   – CSS-селектор цены
  THRESHOLD        – минимальное изменение цены в процентах
────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Final

import pandas as pd
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from telegram import Bot

# ──────────── конфиг ────────────
DATA_FILE: Final[Path] = Path("prices.json")
load_dotenv()  # читаем переменные окружения (.env или Secrets)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
URL = os.getenv("URL")
SEL_NAME = os.getenv("SELECTOR_NAME")
SEL_PRICE = os.getenv("SELECTOR_PRICE")
THRESHOLD = float(os.getenv("THRESHOLD", "1"))  # %


# ──────────── вспом-функции ────────────
def _clean_price(text: str) -> float:
    """Приводит '€ 1 234,56' → 1234.56"""
    digits = "".join(ch for ch in text if ch.isdigit() or ch in ",.")
    return float(digits.replace(",", ".") or 0)


async def scrape() -> pd.DataFrame:
    """Собирает названия и цены со страницы URL."""
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL, wait_until="networkidle", timeout=60_000)

        names = await page.locator(SEL_NAME).all_text_contents()
        prices_raw = await page.locator(SEL_PRICE).all_text_contents()
        await browser.close()

    df = pd.DataFrame(
        {
            "name": [n.strip() for n in names],
            "price": [_clean_price(p) for p in prices_raw],
        }
    )
    return df


def diff(prev: pd.DataFrame, curr: pd.DataFrame) -> pd.DataFrame:
    """Возвращает товары с изменённой ценой ≥ THRESHOLD %."""
    merged = curr.merge(prev, on="name", how="left", suffixes=("", "_old"))
    merged["delta"] = (
        (merged["price"] - merged["price_old"]) / merged["price_old"] * 100
    ).round(2)
    changed = merged[merged["delta"].abs() >= THRESHOLD].dropna()
    return changed[["name", "price_old", "price", "delta"]]


async def notify(bot: Bot, rows: pd.DataFrame) -> None:
    """Шлёт сообщение со списком изменённых цен."""
    lines = [
        f"💱 <b>{r.name}</b>\n   {r.price_old:.2f} → {r.price:.2f} "
        f"({r.delta:+.1f} %)"
        for r in rows.itertuples(index=False)
    ]
    text = "⚠️ Обнаружены изменения цен:\n\n" + "\n\n".join(lines)
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML")


# ──────────── точка входа ────────────
async def main() -> None:
    if not all([BOT_TOKEN, CHAT_ID, URL, SEL_NAME, SEL_PRICE]):
        raise RuntimeError("Не заданы все обязательные переменные окружения")

    bot = Bot(BOT_TOKEN, parse_mode=None)
    curr = await scrape()

    if DATA_FILE.exists():
        prev = pd.read_json(DATA_FILE)
        changes = diff(prev, curr)
        if not changes.empty:
            await notify(bot, changes)

    # сохраняем текущее состояние цен
    curr.to_json(DATA_FILE, orient="records", force_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
