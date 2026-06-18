import asyncio
import aiohttp
import logging
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("ARB_BOT_TOKEN", "")
CHAT_ID = None
MIN_MARGIN = 1.0

SELLER_MIN_TRADES = 50
SELLER_MIN_COMPLETION = 98.0
SELLER_MIN_LIMIT_KZT = 10000

BUYER_MIN_TRADES = 30
BUYER_MIN_COMPLETION = 98.0

SEEN_DEALS = set()


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


async def binance_buy(session):
    """Ищем продавца USDT — у кого покупаем"""
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    try:
        async with session.post(url, json={
            "asset": "USDT", "fiat": "KZT",
            "tradeType": "BUY", "page": 1, "rows": 20,
            "merchantCheck": False
        }, headers={"Content-Type": "application/json"},
           timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            best = None
            for a in data.get("data", []):
                adv = a.get("adv", {})
                advertiser = a.get("advertiser", {})
                price = float(adv.get("price", 0))
                min_l = float(adv.get("minSingleTransAmount", 0))
                max_l = float(adv.get("maxSingleTransAmount", 0))
                trades = int(advertiser.get("monthOrderCount", 0))
                comp = float(advertiser.get("monthFinishRate", 0)) * 100
                nick = advertiser.get("nickName", "?")
                banks = set()
                for pm in adv.get("tradeMethods", []):
                    name = pm.get("tradeMethodName") or pm.get("identifier") or ""
                    if name:
                        banks.add(name.strip())
                if price <= 0: continue
                if trades < SELLER_MIN_TRADES: continue
                if comp < SELLER_MIN_COMPLETION: continue
                if min_l > SELLER_MIN_LIMIT_KZT: continue
                if max_l < SELLER_MIN_LIMIT_KZT: continue
                if not best or price < best[0]:
                    best = (price, min_l, max_l, banks, nick, trades, round(comp, 1))
            return best
    except Exception as e:
        logger.error(f"Binance buy error: {e}")
        return None


async def binance_sell(session):
    """Ищем покупателя USDT — кому продаём"""
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    try:
        async with session.post(url, json={
            "asset": "USDT", "fiat": "KZT",
            "tradeType": "SELL", "page": 1, "rows": 20,
            "merchantCheck": False
        }, headers={"Content-Type": "application/json"},
           timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            best = None
            for a in data.get("data", []):
                adv = a.get("adv", {})
                advertiser = a.get("advertiser", {})
                price = float(adv.get("price", 0))
                min_l = float(adv.get("minSingleTransAmount", 0))
                max_l = float(adv.get("maxSingleTransAmount", 0))
                trades = int(advertiser.get("monthOrderCount", 0))
                comp = float(advertiser.get("monthFinishRate", 0)) * 100
                nick = advertiser.get("nickName", "?")
                banks = set()
                for pm in adv.get("tradeMethods", []):
                    name = pm.get("tradeMethodName") or pm.get("identifier") or ""
                    if name:
                        banks.add(name.strip())
                if price <= 0: continue
                if trades < BUYER_MIN_TRADES: continue
                if comp < BUYER_MIN_COMPLETION: continue
                # Лимит покупателя не фильтруем здесь —
                # проверяем динамически против лимита продавца ниже
                if not best or price > best[0]:
                    best = (price, min_l, max_l, banks, nick, trades, round(comp, 1))
            return best
    except Exception as e:
        logger.error(f"Binance sell error: {e}")
        return None


async def scan(session):
    buy_data, sell_data = await asyncio.gather(
        binance_buy(session),
        binance_sell(session)
    )

    if not buy_data or not sell_data:
        return []

    buy_price, buy_min, buy_max, buy_banks, buy_nick, buy_trades, buy_comp = buy_data
    sell_price, sell_min, sell_max, sell_banks, sell_nick, sell_trades, sell_comp = sell_data

    # Общие банки
    common_banks = buy_banks & sell_banks
    if not common_banks:
        logger.info(f"No common banks. Buy: {buy_banks} | Sell: {sell_banks}")
        return []

    # Мин. лимит покупателя не выше макс. лимита продавца
    if sell_min > buy_max:
        logger.info(f"Limit mismatch: sell_min={sell_min} > buy_max={buy_max}")
        return []

    net = round(((sell_price - buy_price) / buy_price) * 100 - 0.6, 2)

    # Защита от повторов
    key = f"{buy_price}-{sell_price}-{sorted(common_banks)}"
    if key in SEEN_DEALS:
        return []
    SEEN_DEALS.add(key)

    signal = {
        "buy_price": buy_price,
        "sell_price": sell_price,
        "buy_nick": buy_nick,
        "sell_nick": sell_nick,
        "buy_trades": buy_trades,
        "buy_comp": buy_comp,
        "sell_trades": sell_trades,
        "sell_comp": sell_comp,
        "common_banks": common_banks,
        "buy_min": buy_min,
        "buy_max": buy_max,
        "sell_min": sell_min,
        "net": net,
        "profitable": net >= MIN_MARGIN
    }

    return [signal]


def format_signal(s):
    banks_str = ", ".join(sorted(s["common_banks"]))
    profit_100 = round((s["sell_price"] - s["buy_price"]) * 100 * 0.994, 0)
    profit_500 = round((s["sell_price"] - s["buy_price"]) * 500 * 0.994, 0)
    icon = "🚨" if s["profitable"] else "📊"
    return (
        f"{icon} *СИГНАЛ АРБИТРАЖА — Binance KZT*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 *КУПИТЬ USDT*\n"
        f"   Цена: `{s['buy_price']} KZT`\n"
        f"   Продавец: {s['buy_nick']}\n"
        f"   ✅ Сделок: {s['buy_trades']} | Рейтинг: {s['buy_comp']}%\n"
        f"   Лимит: {s['buy_min']:,.0f} — {s['buy_max']:,.0f} KZT\n\n"
        f"📤 *ПРОДАТЬ USDT*\n"
        f"   Цена: `{s['sell_price']} KZT`\n"
        f"   Покупатель: {s['sell_nick']}\n"
        f"   ✅ Сделок: {s['sell_trades']} | Рейтинг: {s['sell_comp']}%\n"
        f"   Мин. лимит: {s['sell_min']:,.0f} KZT\n\n"
        f"🏦 *Общие банки:* {banks_str}\n"
        f"💰 *Чистая маржа: {s['net']}%*\n"
        f"💵 Прибыль со 100 USDT: ~{profit_100} KZT\n"
        f"💵 Прибыль с 500 USDT: ~{profit_500} KZT\n\n"
        f"⚠️ Проверь имя плательщика!\n"
        f"⚠️ Жди реального зачисления!\n\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"
    )


def filters_text():
    return (
        f"⚙️ *ФИЛЬТРЫ*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 *ПРОДАВЦЫ* (покупаем USDT):\n"
        f"   • Сделок: {SELLER_MIN_TRADES}+\n"
        f"   • Рейтинг: {SELLER_MIN_COMPLETION}%+\n"
        f"   • Лимит от: {SELLER_MIN_LIMIT_KZT:,} KZT\n\n"
        f"📤 *ПОКУПАТЕЛИ* (продаём USDT):\n"
        f"   • Сделок: {BUYER_MIN_TRADES}+\n"
        f"   • Рейтинг: {BUYER_MIN_COMPLETION}%+\n"
        f"   • Мин. лимит ≤ макс. лимита продавца\n\n"
        f"📊 Площадка: Binance P2P\n"
        f"💱 Валюта: KZT\n"
        f"🏦 Только совпадающие банки\n"
    )


HELP_TEXT = """
🤖 *BuyerSearch-USDT-KZT-Bot*
━━━━━━━━━━━━━━━━━━━━━━

Команды:
/start — запустить мониторинг
/scan — сканировать прямо сейчас
/filters — текущие фильтры
/help — помощь

Бот мониторит Binance P2P каждые 5 минут.
Сигнал когда маржа ≥1% и банки совпадают.
"""


async def handle_command(session, text, chat_id):
    global CHAT_ID
    CHAT_ID = chat_id
    cmd = text.strip().lower().split()[0]

    if cmd == "/start":
        await send_message(session,
            "✅ *BuyerSearch-USDT-KZT-Bot запущен!*\n\n"
            "Мониторю Binance P2P каждые 5 минут.\n"
            "Сигнал когда маржа ≥1% и банки совпадают.\n\n"
            + filters_text()
        )

    elif cmd == "/scan":
        await send_message(session, "🔍 Сканирую Binance P2P...")
        signals = await scan(session)
        if not signals:
            await send_message(session,
                "😔 Нет сигналов.\n"
                "Либо маржа < 1%, либо банки не совпадают,\n"
                "либо лимиты не совместимы.\n\n"
                "Продолжаю мониторинг каждые 5 минут."
            )
        else:
            for s in signals:
                await send_message(session, format_signal(s))

    elif cmd == "/filters":
        await send_message(session, filters_text())

    elif cmd == "/help":
        await send_message(session, HELP_TEXT)


async def polling_loop(session):
    offset = 0
    while True:
        updates = await get_updates(session, offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            if msg:
                global CHAT_ID
                CHAT_ID = msg["chat"]["id"]
                text = msg.get("text", "")
                if text.startswith("/"):
                    await handle_command(session, text, CHAT_ID)
        await asyncio.sleep(1)


async def monitor_loop(session):
    await asyncio.sleep(30)
    while True:
        if CHAT_ID:
            try:
                signals = await scan(session)
                profitable = [s for s in signals if s["profitable"]]
                if profitable:
                    for s in profitable:
                        await send_message(session, format_signal(s))
                    logger.info(f"Signal sent: margin={profitable[0]['net']}%")
                else:
                    logger.info("No profitable signals")
            except Exception as e:
                logger.error(f"Monitor error: {e}")
        await asyncio.sleep(300)


async def main():
    if not TOKEN:
        logger.error("ARB_BOT_TOKEN не установлен!")
        return
    logger.info("BuyerSearch-USDT-KZT-Bot запущен | Binance P2P | KZT")
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(
            polling_loop(session),
            monitor_loop(session)
        )


if __name__ == "__main__":
    asyncio.run(main())
