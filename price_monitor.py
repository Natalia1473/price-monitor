import json
from pathlib import Path
import asyncio
import os
import pandas as pd
from playwright.async_api import async_playwright
from telegram import Bot

# Читаем порог и Telegram-конфиг из окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THRESHOLD = float(os.getenv("THRESHOLD", "1"))

# Путь к базе истории по каждому магазину
HISTORY_DIR = Path("history")
HISTORY_DIR.mkdir(exist_ok=True)

# Загружаем массив конфигов
with open("config.json", encoding="utf-8") as f:
    SITES = json.load(f)

async def scrape_site(url: str, sel_name: str, sel_price: str) -> pd.DataFrame:
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, timeout=60000, wait_until="networkidle")
        names = await page.locator(sel_name).all_text_contents()
        prices_raw = await page.locator(sel_price).all_text_contents()
        await browser.close()
    # чистим цены, как раньше
    prices = [float("".join(ch for ch in p if ch.isdigit() or ch==".").replace(",", ".")) for p in prices_raw]
    return pd.DataFrame({"name": names, "price": prices})

async def main():
    bot = Bot(BOT_TOKEN)
    all_changes = []

    for site in SITES:
        df = await scrape_site(site["url"], site["selector_name"], site["selector_price"])
        hist_file = HISTORY_DIR / f'{site["name"].replace(" ", "_")}.json'

        if hist_file.exists():
            prev = pd.read_json(hist_file)
            merged = df.merge(prev, on="name", suffixes=("", "_old"))
            merged["delta"] = (merged["price"] - merged["price_old"]) / merged["price_old"] * 100
            changed = merged[merged["delta"].abs() >= THRESHOLD].dropna()
            if not changed.empty:
                changed["site"] = site["name"]
                all_changes.append(changed[["site","name","price_old","price","delta"]])

        # обновляем историю
        df.to_json(hist_file, orient="records", force_ascii=False, indent=2)

    if all_changes:
        # собираем одно сообщение
        lines = []
        for df in all_changes:
            for r in df.itertuples():
                lines.append(f"💱 <b>{r.site}</b>: {r.name}\n   {r.price_old:.2f} → {r.price:.2f} ({r.delta:+.1f}%)")
        text = "⚠️ Изменения цен:\n\n" + "\n\n".join(lines)
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML")

if __name__ == "__main__":
    asyncio.run(main())
