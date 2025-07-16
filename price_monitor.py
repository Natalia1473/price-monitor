# price_monitor.py
"""
Отслеживает цены на нескольких сайтах и шлёт уведомление в Telegram
при изменении цены ≥ THRESHOLD%.

Структура проекта:
  ├ config.json       – массив конфигураций сайтов
  ├ history/          – папка для истории цен каждого сайта
  ├ price_monitor.py  – этот файл
  └ requirements.txt
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
load_dotenv()  # локально читает .env, в Actions – Secrets

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
THRESHOLD = float(os.getenv("THRESHOLD", "1"))  # в процентах

if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Не заданы BOT_TOKEN или CHAT_ID")

# ───────────── История ─────────────
HISTORY_DIR = Path("history")
HISTORY_DIR.mkdir(exist_ok=True)

# ───────────── Конфиг сайтов ─────────────
with open("config.json", encoding="utf-8") as f:
    SITES = json.load(f)

# ───────────── Функция скрейпа ─────────────
async def scrape_site(name: str, url: str, sel_name: str, sel_price: str) -> pd.DataFrame:
    """Собирает DataFrame с колонками ['name','price'] для одного сайта."""
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, timeout=120_000, wait_until="domcontentloaded")
        except Exception:
            # если не успело загрузиться полностью — продолжаем парсинг
            pass

        raw_names  = await page.locator(sel_name).all_text_contents()
        raw_prices = await page.locator(sel_price).all_text_contents()
        await browser.close()

    # убираем лишние пробелы в названиях
    names  = [n.strip() for n in raw_names]
    # чистим цену вида "155 028 ₽" → 155028.0
    prices = [
        float("".join(ch for ch in txt if ch.isdigit() or ch in ".,").replace(",", ".") or 0)
        for txt in raw_prices
    ]

    # DEBUG: выводим в лог количество спаршенных элементов
    print(f"[{name}] найдено {len(names)} названий и {len(prices)} цен")

    return pd.DataFrame({"name": names, "price": prices})

# ───────────── Основной код ─────────────
async def main() -> None:
    bot = Bot(BOT_TOKEN)
    all_changes = []

    for site in SITES:
        df = await scrape_site(
            site["name"],
            site["url"],
            site["selector_name"],
            site["selector_price"],
        )

        hist_file = HISTORY_DIR / f"{site['name'].replace(' ', '_')}.json"
        if hist_file.exists():
            prev = pd.read_json(hist_file)
            merged = df.merge(prev, on="name", how="left", suffixes=("", "_old"))
            merged["delta"] = ((merged["price"] - merged["price_old"])
                               / merged["price_old"] * 100).round(2)
            changed = merged[merged["delta"].abs() >= THRESHOLD].dropna()

            if not changed.empty:
                changed["site"] = site["name"]
                all_changes.append(changed[["site", "name", "price_old", "price", "delta"]])

        # сохраняем новый снимок цен
        df.to_json(hist_file, orient="records", force_ascii=False, indent=2)

    # DEBUG: сколько изменений по каждому сайту
    for df in all_changes:
        print(f"{df.iloc[0]['site']}: найдено {len(df)} изменений")

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
