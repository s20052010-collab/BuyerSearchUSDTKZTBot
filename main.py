```python
import asyncio
import aiohttp
import logging
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("ARBITRAGE_BOT_TOKEN", "")
CHAT_ID = None
MIN_MARGIN = 1.0
CURRENCIES = ["KZT"]

# ═══════════════════════════════════════
# ФИЛЬТРЫ ПРОДАВЦОВ (покупаем USDT)
# ═══════════════════════════════════════
SELLER_MIN_TRADES = 50
SELLER_MIN_COMPLETION = 98.0
SELLER_MIN_LIMIT = 10000

# ═══════════════════════════════════════
# ФИЛЬТРЫ ПОКУПАТЕЛЕЙ (продаём USDT)
# ═══════════════════════════════════════
BUYER_MIN_TRADES = 30
BUYER_MIN_COMPLETION = 98.0
BUYER_MIN_LIMIT = 10000


async def send_message(session, text):
    if not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        await session.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        })
    except Exception as e:
        logger.error(f"Send error: {e}")


async def get_updates(session, offset=0):
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    try:
        async with session.get(url, params={"offset": offset, "timeout": 30}) as r:
            data = await r.json()
            return data.get("result", [])
    except:
        return []


async def get_binance_buy(session, fiat):
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    try:
        async with session.post(url, json={
            "asset": "USDT",
            "fiat": fiat,
            "tradeType": "BUY",
            "page": 1,
            "rows": 20,
            "merchantCheck": False
        }, headers={"Content-Type": "application/json"},
           timeout=aiohttp.ClientTimeout(total=10)) as r:

            if r.status != 200:
                return None, None, None, None

            data = await r.json()
            ads = data.get("data", [])

            best = None

            for a in ads:
                adv = a.get("adv", {})
                advertiser = a.get("advertiser", {})

                price = float(adv.get("price", 0))
                min_limit = float(adv.get("minSingleTransAmount", 0))
                max_limit = float(adv.get("maxSingleTransAmount", 0))

                trades = int(advertiser.get("monthOrderCount", 0))
                completion = float(advertiser.get("monthFinishRate", 0)) * 100
                nick = advertiser.get("nickName", "?")

                if price <= 0:
                    continue

                if trades < SELLER_MIN_TRADES:
                    continue
                if completion < SELLER_MIN_COMPLETION:
                    continue
                if min_limit < SELLER_MIN_LIMIT:
                    continue

                if not best or price < best[0]:
                    best = (price, nick, trades, round(completion, 1))

            return best if best else (None, None, None, None)

    except Exception as e:
        logger.error(f"Buy error {fiat}: {e}")
        return None, None, None, None


async def get_binance_sell(session, fiat):
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    try:
        async with session.post(url, json={
            "asset": "USDT",
            "fiat": fiat,
            "tradeType": "SELL",
            "page": 1,
            "rows": 20,
            "merchantCheck": False
        }, headers={"Content-Type": "application/json"},
           timeout=aiohttp.ClientTimeout(total=10)) as r:

            if r.status != 200:
                return None, None, None, None

            data = await r.json()
            ads = data.get("data", [])

            best = None

            for a in ads:
                adv = a.get("adv", {})
                advertiser = a.get("advertiser", {})

                price = float(adv.get("price", 0))
                min_limit = float(adv.get("minSingleTransAmount", 0))
                max_limit = float(adv.get("maxSingleTransAmount", 0))

                trades = int(advertiser.get("monthOrderCount", 0))
                completion = float(advertiser.get("monthFinishRate", 0)) * 100
                nick = advertiser.get("nickName", "?")

                if price <= 0:
                    continue

                if trades < BUYER_MIN_TRADES:
                    continue
                if completion < BUYER_MIN_COMPLETION:
                    continue
                if min_limit < BUYER_MIN_LIMIT:
                    continue

                if not best or price > best[0]:
                    best = (price, nick, trades, round(completion, 1))

            return best if best else (None, None, None, None)

    except Exception as e:
        logger.error(f"Sell error {fiat}: {e}")
        return None, None, None, None


async def scan(session):
    results = []

    for fiat in CURRENCIES:
        buy = await get_binance_buy(session, fiat)
        await asyncio.sleep(1)
        sell = await get_binance_sell(session, fiat)
        await asyncio.sleep(1)

        buy_price, buy_nick, buy_trades, buy_comp = buy
        sell_price, sell_nick, sell_trades, sell_comp = sell

        if not buy_price or not sell_price:
            continue

        gross = ((sell_price - buy_price) / buy_price) * 100
        net = gross - 0.6

        results.append({
            "fiat": fiat,
            "buy": buy_price,
            "sell": sell_price,
            "net": round(net, 2),
            "profitable": net >= MIN_MARGIN,
            "buy_nick": buy_nick,
            "sell_nick": sell_nick,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "buy_completion": buy_comp,
            "sell_completion": sell_comp
        })

    return results


def format_signal(r):
    profit_100 = (r["sell"] - r["buy"]) * 100 * 0.994
    profit_1000 = (r["sell"] - r["buy"]) * 1000 * 0.994

    return (
        f"🚨 *АРБИТРАЖ СИГНАЛ — {r['fiat']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 *КУПИТЬ USDT*\n"
        f"Цена: `{r['buy']}`\n"
        f"Профиль: {r['buy_nick']}\n"
        f"Сделки: {r['buy_trades']} | Рейтинг: {r['buy_completion']}%\n\n"
        f"📤 *ПРОДАТЬ USDT*\n"
        f"Цена: `{r['sell']}`\n"
        f"Профиль: {r['sell_nick']}\n"
        f"Сделки: {r['sell_trades']} | Рейтинг: {r['sell_completion']}%\n\n"
        f"💰 Маржа: {r['net']}%\n"
        f"💵 100 USDT: ~{round(profit_100, 2)}\n"
        f"💵 1000 USDT: ~{round(profit_1000, 2)}\n"
    )


def filters_text():
    return (
        "⚙️ *ФИЛЬТРЫ*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 ПРОДАВЦЫ (покупаем USDT):\n"
        f"• Сделок: {SELLER_MIN_TRADES}+\n"
        f"• Рейтинг: {SELLER_MIN_COMPLETION}%+\n"
        f"• Лимит от: {SELLER_MIN_LIMIT} KZT\n\n"
        "📤 ПОКУПАТЕЛИ (продаём USDT):\n"
        f"• Сделок: {BUYER_MIN_TRADES}+\n"
        f"• Рейтинг: {BUYER_MIN_COMPLETION}%+\n"
        f"• Мин. лимит от: {BUYER_MIN_LIMIT} KZT\n"
    )


async def handle_command(session, text):
    global CHAT_ID
    cmd = text.lower()

    if cmd == "/start":
        await send_message(session, "Бот запущен\n\n" + filters_text())

    elif cmd == "/scan":
        await send_message(session, "Сканирую...")
        results = await scan(session)

        good = [r for r in results if r["profitable"]]

        if not good:
            await send_message(session, "Нет связок")
            return

        for r in good:
            await send_message(session, format_signal(r))

    elif cmd == "/filters":
        await send_message(session, filters_text())


async def polling(session):
    offset = 0
    while True:
        updates = await get_updates(session, offset)

        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message", {})
            if not msg:
                continue

            CHAT_ID = msg["chat"]["id"]
            text = msg.get("text", "")

            if text.startswith("/"):
                await handle_command(session, text)

        await asyncio.sleep(1)


async def monitor(session):
    await asyncio.sleep(10)

    while True:
        if CHAT_ID:
            results = await scan(session)
            good = [r for r in results if r["profitable"]]

            for r in good:
                await send_message(session, format_signal(r))

        await asyncio.sleep(300)


async def main():
    if not TOKEN:
        return

    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(
            polling(session),
            monitor(session)
        )


if __name__ == "__main__":
    asyncio.run(main())
```
