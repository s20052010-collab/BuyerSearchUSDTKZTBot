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
# ФИЛЬТРЫ ПРОДАВЦОВ (у кого покупаем USDT)
# ═══════════════════════════════════════
SELLER_MIN_TRADES = 50          # Минимум сделок
SELLER_MIN_COMPLETION = 98.0     # Минимальный рейтинг %
SELLER_MIN_LIMIT = 10000         # Лимит от (KZT) — продавец должен принимать от 10,000 KZT

# ═══════════════════════════════════════
# ФИЛЬТРЫ ПОКУПАТЕЛЕЙ (кому продаём USDT)
# ═══════════════════════════════════════
BUYER_MIN_TRADES = 30           # Минимум сделок
BUYER_MIN_COMPLETION = 98.0      # Минимальный рейтинг %
BUYER_MAX_MIN_LIMIT = 500000     # Минимальный лимит покупателя не выше 500,000 KZT


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
    """
    Покупаем USDT — ищем продавца.
    Фильтры продавца:
      - Сделок 50+
      - Рейтинг 98%+
      - Минимальный лимит ордера ≤ 10,000 KZT (чтобы можно было войти с небольшой суммой)
    """
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    try:
        async with session.post(url, json={
            "asset": "USDT", "fiat": fiat,
            "tradeType": "BUY", "page": 1, "rows": 20,
            "merchantCheck": False,
            "transAmount": str(SELLER_MIN_LIMIT)
        }, headers={"Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                ads = data.get("data", [])
                filtered = []
                for a in ads:
                    adv = a.get("adv", {})
                    advertiser = a.get("advertiser", {})
                    price = float(adv.get("price", 0))
                    min_limit = float(adv.get("minSingleTransAmount", 0))
                    max_limit = float(adv.get("maxSingleTransAmount", 0))
                    month_trades = int(advertiser.get("monthOrderCount", 0))
                    completion = float(advertiser.get("monthFinishRate", 0)) * 100
                    nick = advertiser.get("nickName", "?")

                    if price <= 0:
                        continue
                    # Лимит: продавец принимает от ≤ SELLER_MIN_LIMIT KZT
                    if min_limit > SELLER_MIN_LIMIT:
                        continue
                    if max_limit < SELLER_MIN_LIMIT:
                        continue
                    # Рейтинг продавца
                    if month_trades < SELLER_MIN_TRADES:
                        continue
                    if completion < SELLER_MIN_COMPLETION:
                        continue

                    filtered.append((price, nick, month_trades, round(completion, 1)))

                if filtered:
                    filtered.sort(key=lambda x: x[0])  # Минимальная цена первой
                    return filtered[0]
    except Exception as e:
        logger.error(f"Buy {fiat}: {e}")
    return None, None, None, None


async def get_binance_sell(session, fiat):
    """
    Продаём USDT — ищем покупателя.
    Фильтры покупателя:
      - Сделок 30+
      - Рейтинг 98%+
      - Минимальный лимит покупки ≤ 500,000 KZT (чтобы не требовал огромную сумму)
    """
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    try:
        async with session.post(url, json={
            "asset": "USDT", "fiat": fiat,
            "tradeType": "SELL", "page": 1, "rows": 20,
            "merchantCheck": False,
        }, headers={"Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                ads = data.get("data", [])
                filtered = []
                for a in ads:
                    adv = a.get("adv", {})
                    advertiser = a.get("advertiser", {})
                    price = float(adv.get("price", 0))
                    min_limit = float(adv.get("minSingleTransAmount", 0))
                    max_limit = float(adv.get("maxSingleTransAmount", 0))
                    month_trades = int(advertiser.get("monthOrderCount", 0))
                    completion = float(advertiser.get("monthFinishRate", 0)) * 100
                    nick = advertiser.get("nickName", "?")

                    if price <= 0:
                        continue
                    # Минимальный лимит покупателя не выше BUYER_MAX_MIN_LIMIT
                    if min_limit > BUYER_MAX_MIN_LIMIT:
                        continue
                    if max_limit < min_limit:
                        continue
                    # Рейтинг покупателя
                    if month_trades < BUYER_MIN_TRADES:
                        continue
                    if completion < BUYER_MIN_COMPLETION:
                        continue

                    filtered.append((price, nick, month_trades, round(completion, 1)))

                if filtered:
                    filtered.sort(key=lambda x: x[0], reverse=True)  # Максимальная цена первой
                    return filtered[0]
    except Exception as e:
        logger.error(f"Sell {fiat}: {e}")
    return None, None, None, None


async def scan(session):
    results = []
    for fiat in CURRENCIES:
        buy_price, buy_nick, buy_trades, buy_completion = await get_binance_buy(session, fiat)
        await asyncio.sleep(1)
        sell_price, sell_nick, sell_trades, sell_completion = await get_binance_sell(session, fiat)
        await asyncio.sleep(1)

        if not buy_price or not sell_price:
            continue

        gross = ((sell_price - buy_price) / buy_price) * 100
        net = gross - 0.6  # комиссии ~0.6%

        results.append({
            "fiat": fiat,
            "buy": buy_price,
            "sell": sell_price,
            "gross": round(gross, 2),
            "net": round(net, 2),
            "profitable": net >= MIN_MARGIN,
            "buy_nick": buy_nick,
            "sell_nick": sell_nick,
            "buy_trades": buy_trades,
            "buy_completion": buy_completion,
            "sell_trades": sell_trades,
            "sell_completion": sell_completion,
        })

    return results


def format_rates(results):
    text = f"📊 *КУРСЫ USDT — {datetime.now().strftime('%H:%M')}*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for r in results:
        icon = "🟢" if r["profitable"] else "🔴"
        text += f"{icon} *{r['fiat']}*\n"
        text += f"  📥 Купить: `{r['buy']}`\n"
        text += f"  📤 Продать: `{r['sell']}`\n"
        text += f"  💰 Чистая маржа: *{r['net']}%*\n\n"
    return text


def format_signal(r):
    profit_1000 = round((r["sell"] - r["buy"]) * 1000 * 0.994, 2)
    profit_100 = round((r["sell"] - r["buy"]) * 100 * 0.994, 2)
    return (
        f"🚨 *СИГНАЛ АРБИТРАЖА — {r['fiat']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 *КУПИТЬ USDT*\n"
        f"   Цена: `{r['buy']} {r['fiat']}`\n"
        f"   У продавца: {r['buy_nick']}\n"
        f"   ✅ Сделок: {r['buy_trades']} | Рейтинг: {r['buy_completion']}%\n\n"
        f"📤 *ПРОДАТЬ USDT*\n"
        f"   Цена: `{r['sell']} {r['fiat']}`\n"
        f"   Покупателю: {r['sell_nick']}\n"
        f"   ✅ Сделок: {r['sell_trades']} | Рейтинг: {r['sell_completion']}%\n\n"
        f"💰 *Чистая маржа: {r['net']}%*\n"
        f"💵 Прибыль со 100 USDT: ~{profit_100} {r['fiat']}\n"
        f"💵 Прибыль с 1000 USDT: ~{profit_1000} {r['fiat']}\n\n"
        f"⚠️ Проверь имя плательщика!\n"
        f"⚠️ Жди реального зачисления!\n\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"
    )


HELP_TEXT = """
🤖 *USDT АРБИТРАЖ МОНИТОР*
━━━━━━━━━━━━━━━━━━━━━━

Команды:
/start — запустить мониторинг
/scan — сканировать сейчас
/rates — текущие курсы
/filters — текущие фильтры
/safety — правила безопасности
/help — помощь

Бот мониторит Binance P2P каждые 5 минут.
Присылает сигнал когда маржа ≥1%.
"""

SAFETY_TEXT = """
🛡 *ПРАВИЛА БЕЗОПАСНОСТИ*
━━━━━━━━━━━━━━━━━━━━━━

✅ ВСЕГДА:
• Ждать реального зачисления в банке
• Проверять имя отправителя = имя на бирже
• Работать только через escrow биржи

❌ НИКОГДА:
• Не отпускать USDT по скрину
• Не принимать деньги от третьих лиц
• Не делать сделки в Telegram

⚠️ ЛИМИТЫ:
• Неделя 1: 1 сделка/день, до 200 USDT
• Неделя 2: до 2 сделок, до 500 USDT
• После 20+ сделок — масштабировать
"""


def filters_text():
    return (
        f"⚙️ *ТЕКУЩИЕ ФИЛЬТРЫ*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 *ПРОДАВЦЫ* (покупаем USDT):\n"
        f"   • Сделок: {SELLER_MIN_TRADES}+\n"
        f"   • Рейтинг: {SELLER_MIN_COMPLETION}%+\n"
        f"   • Лимит от: {SELLER_MIN_LIMIT:,} KZT\n\n"
        f"📤 *ПОКУПАТЕЛИ* (продаём USDT):\n"
        f"   • Сделок: {BUYER_MIN_TRADES}+\n"
        f"   • Рейтинг: {BUYER_MIN_COMPLETION}%+\n"
        f"   • Мин. лимит не выше: {BUYER_MAX_MIN_LIMIT:,} KZT\n"
    )


async def handle_command(session, text):
    global CHAT_ID
    cmd = text.strip().lower()

    if cmd == "/start":
        await send_message(session,
            "✅ *Мониторинг запущен!*\n\n"
            "Сканирую Binance P2P каждые 5 минут.\n"
            "Пришлю сигнал когда маржа ≥1%\n\n"
            + filters_text()
        )

    elif cmd == "/scan":
        await send_message(session, "🔍 Сканирую... подожди 30 сек")
        results = await scan(session)
        if not results:
            await send_message(session,
                "❌ Надёжных контрагентов не найдено.\n\n"
                + filters_text() +
                "\nПопробуй позже."
            )
            return
        profitable = [r for r in results if r["profitable"]]
        if profitable:
            for r in profitable:
                await send_message(session, format_signal(r))
        else:
            await send_message(session,
                "😔 Прибыльных связок нет.\n"
                f"Лучшая маржа: {max(r['net'] for r in results):.2f}%\n"
                "Продолжаю мониторинг каждые 5 минут."
            )

    elif cmd == "/rates":
        await send_message(session, "📊 Получаю курсы...")
        results = await scan(session)
        if results:
            await send_message(session, format_rates(results))
        else:
            await send_message(session, "❌ Не удалось получить данные.")

    elif cmd == "/filters":
        await send_message(session, filters_text())

    elif cmd == "/safety":
        await send_message(session, SAFETY_TEXT)

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
                    await handle_command(session, text)
        await asyncio.sleep(1)


async def monitor_loop(session):
    await asyncio.sleep(30)
    while True:
        if CHAT_ID:
            try:
                results = await scan(session)
                profitable = [r for r in results if r["profitable"]]
                if profitable:
                    for r in profitable:
                        await send_message(session, format_signal(r))
                    logger.info(f"Signals sent: {len(profitable)}")
                else:
                    logger.info(f"No signals. Best: {max((r['net'] for r in results), default=0):.2f}%")
            except Exception as e:
                logger.error(f"Monitor error: {e}")
        await asyncio.sleep(300)


async def main():
    if not TOKEN:
        logger.error("TOKEN не установлен!")
        return

    logger.info(
        f"Бот запущен | "
        f"Продавцы: {SELLER_MIN_TRADES}+ сделок, {SELLER_MIN_COMPLETION}%+, от {SELLER_MIN_LIMIT:,} KZT | "
        f"Покупатели: {BUYER_MIN_TRADES}+ сделок, {BUYER_MIN_COMPLETION}%+, мин.лимит ≤{BUYER_MAX_MIN_LIMIT:,} KZT"
    )
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(
            polling_loop(session),
            monitor_loop(session)
        )


if __name__ == "__main__":
    asyncio.run(main())
