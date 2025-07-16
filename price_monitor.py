# price_monitor.py
"""
Отслеживает цены на нескольких сайтах и шлёт уведомление в Telegram
при изменении цены ≥ THRESHOLD%.

Файлы:
  - config.json    — массив конфигураций сайтов (URL + селекторы)
  - history/       — папка для исторических снимков цен по каждому сайту
  - price_monitor.py
  - requirements.txt
"""

import json
import os
import asyncio
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright
from telegram import Bot
from dotenv import load_dotenv

# ─────────────── Настройки ───────────────
load_dotenv()  # подхватит .env (локально) или Secrets (в Actions)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
THRESHOLD = float(os.getenv("THRESHOLD", "1"))  # проценты

if not all([BOT_TOKEN, CHAT_ID]):
    raise RuntimeError("Не заданы BOT_TOKEN или CHAT_ID")

# Папка для хранения каждого исторического файла
HISTORY_DIR = Path("history")
HISTORY_DIR.mkdir(exist_ok=True)

# Загружаем конфиг сайтов
with open("config.json", encoding="utf-8") as cfg:
    SITES = json.load(cfg)


async def scrape_site(url: str, sel_name: str, sel_price: str) -> pd.DataFrame:
    """Собирает DataFrame с колонками ['name','price'] для одной конфигурации."""
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        # Пытаемся дождаться загрузки минимум DOM, без долгого networkidle
        try:
            await page.goto(url, timeout=120_000, wait_until="domcontentloaded")
        except Exception:
            # если всё равно таймаут, продолжаем то, что успело прогрузиться
            pass

        names_raw  = await page.locator(sel_name).all_text_contents()
        prices_raw = await page.locator(sel_price).all_text_contents()
        await browser.close()

    # Очищаем цену вида "155 028 ₽" → 155028.0
    prices = [
        float("".join(ch for ch in txt if ch.isdigit() or ch in ".,").replace(",", ".") or 0)
        for txt in prices_raw
    ]
    # Стрипим имена от лишних пробелов
    names = [n.strip() for n in names_raw]
    return pd.DataFrame({"name": names, "price": prices})


async def main() -> None:
    bot = Bot(BOT_TOKEN)
    all_changes = []

    for site in SITES:
        df = await scrape_site(site["url"], site["selector_name"], site["selector_price"])
        hist_file = HISTORY_DIR / f"{site['name'].replace(' ', '_')}.json"

        # Если уже есть история — сравниваем
        if hist_file.exists():
            prev = pd.read_json(hist_file)
            merged = df.merge(prev, on="name", how="left", suffixes=("", "_old"))
            merged["delta"] = ((merged["price"] - merged["price_old"])
                               / merged["price_old"] * 100).round(2)
            changed = merged[merged["delta"].abs() >= THRESHOLD].dropna()
            if not changed.empty:
                changed["site"] = site["name"]
                all_changes.append(changed[["site","name","price_old","price","delta"]])

        # Сохраняем новый снимок
        df.to_json(hist_file, orient="records", force_ascii=False, indent=2)

    # Если есть хоть одно изменение — шлём единое сообщение
    if all_changes:
        lines = []
        for df in all_changes:
            for r in df.itertuples(index=False):
                lines.append(
                    f"💱 <b>{r.site}</b>: {r.name}\n"
                    f"   {r.price_old:.2f} → {r.price:.2f} ({r.delta:+.1f}%)"
                )
        text = "⚠️ Изменения цен:\n\n" + "\n\n".join(lines)
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML")


if __name__ == "__main__":
    asyncio.run(main())
