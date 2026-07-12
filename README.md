<div align="center">

# ⚡ PolyPulse

**Умные алерты по аномалиям Polymarket прямо в Telegram • Smart Polymarket anomaly alerts in your Telegram**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](requirements.txt)
[![Stack](https://img.shields.io/badge/Stack-aiogram%20%C2%B7%20SQLite%20%C2%B7%20APScheduler-lightgrey.svg)](#)
[![LLM: DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-blue.svg)](#)

[![Канал: @rupolynews](https://img.shields.io/badge/Канал-@rupolynews-26A5E4.svg?logo=telegram&logoColor=white)](https://t.me/rupolynews)
[![Бот: @polypulse_ru_bot](https://img.shields.io/badge/Бот-@polypulse__ru__bot-26A5E4.svg?logo=telegram&logoColor=white)](https://t.me/polypulse_ru_bot)

**Живая демка:** алерты в канале [@rupolynews](https://t.me/rupolynews) | Бот - [@polypulse_ru_bot](https://t.me/polypulse_ru_bot)

[Русский](#-русский) • [English](#-english)

</div>

---

## 🇷🇺 Русский

**PolyPulse** следит за рынками предсказаний [Polymarket](https://polymarket.com) и публикует в Telegram-канал события, которые легко пропустить, листая сайт руками: резкие движения вероятности, приток крупных денег, свежие рынки с аномальным объёмом. К каждому алерту DeepSeek пишет короткий комментарий на русском — сдержанный, без выдуманных причин и финансовых советов.

Весь сервис — один Python-процесс на дешёвом VPS: без Docker, без Redis, без очередей. SQLite вместо базы данных, systemd вместо оркестратора.

### Типы алертов

| | Тип | Условие |
|---|---|---|
| ⚡ | **Сдвиг вероятности** | рынок сдвинулся на ≥ 7 п.п. за час |
| 🐋 | **Всплеск объёма** | объём за окно в 4+ раза выше типичного для этого рынка |
| 🆕 | **Крупный новый рынок** | создан менее 48 ч назад и уже собрал $100k+ |
| ⏰ | **Скоро развязка** | рынок закрывается в ближайшие 48 ч, а исход спорный (35–65%) |

Против спама — кулдаун 6 часов на пару «рынок + тип алерта» и лимит публикаций в час: при переборе выживают алерты с наибольшим весом (величина движения × объём).

### Как это работает

Каждые 10 минут планировщик забирает топ-100 событий Polymarket через публичный Gamma API (~1500 рынков), пишет снимок каждого рынка в SQLite и сравнивает с историей. Найденные аномалии уходят в DeepSeek за комментарием и публикуются в канал. Если LLM недоступен или вернул мусор — алерт выходит по чистому шаблону: сигнал важнее красоты.

Посмотреть вживую: канал [@rupolynews](https://t.me/rupolynews) публикует алерты 24/7.

Параллельно в том же процессе живёт бот [@polypulse_ru_bot](https://t.me/polypulse_ru_bot): можно подписаться на конкретный рынок и получать личные уведомления, когда вероятность сдвинется на ваш порог.

```
/watch <ссылка на рынок>   подписка на личные алерты
/list                      мои подписки (с кнопками отписки)
/threshold <slug> <п.п.>   свой порог сдвига
/status                    состояние сервиса
```

### Быстрый старт

```bash
git clone https://github.com/Sliptval/polypulse && cd polypulse
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env       # вписать токен бота, id канала и ключ DeepSeek
venv/bin/python main.py
```

Обязательные переменные: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `ADMIN_CHAT_ID`, `DEEPSEEK_API_KEY`. Для обкатки без публикаций — `DRY_RUN=true`, алерты пойдут только в лог.

Автозапуск через systemd:

```bash
sudo cp polypulse.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now polypulse
journalctl -u polypulse -f
```

### Настройка порогов

Все пороги лежат в `.env` и подхватываются после рестарта: окно и величина сдвига, множитель всплеска объёма, минимальная ликвидность, лимит алертов в час. Подобрать значения под свой вкус помогает прогон детектора по накопленной истории:

```bash
venv/bin/python scripts/backfill_check.py --since 24
```

Он покажет все сработки за сутки — сразу видно, слишком шумно или слишком тихо.

---

## 🇬🇧 English

**PolyPulse** watches [Polymarket](https://polymarket.com) prediction markets and posts to a Telegram channel the things you'd miss scrolling the site by hand: sharp probability moves, whale-sized volume inflows, brand-new markets with anomalous activity. Each alert comes with a short DeepSeek-written commentary — measured, no invented causes, no financial advice.

The whole service is a single Python process on a cheap VPS: no Docker, no Redis, no queues. SQLite instead of a database server, systemd instead of an orchestrator.

### Alert types

| | Type | Trigger |
|---|---|---|
| ⚡ | **Probability move** | market moved ≥ 7 pp within an hour |
| 🐋 | **Volume spike** | window volume 4×+ above this market's typical |
| 🆕 | **Big new market** | created < 48 h ago, already at $100k+ |
| ⏰ | **Closing soon** | resolves within 48 h with a contested outcome (35–65%) |

Anti-spam: a 6-hour cooldown per market+type pair and an hourly publish cap — when over budget, the highest-scored alerts (move size × volume) win.

### How it works

Every 10 minutes a scheduler pulls the top-100 Polymarket events from the public Gamma API (~1500 markets), snapshots each market into SQLite and compares against history. Detected anomalies are sent to DeepSeek for commentary and published to the channel. If the LLM is down or returns garbage, the alert goes out as a plain template — the signal matters more than the prose.

Live demo: the [@rupolynews](https://t.me/rupolynews) channel posts alerts 24/7. The same process also runs a bot, [@polypulse_ru_bot](https://t.me/polypulse_ru_bot): subscribe to any market with `/watch <link>` and get a DM when its probability moves past your threshold.

### Quick start

```bash
git clone https://github.com/Sliptval/polypulse && cd polypulse
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env       # bot token, channel id, DeepSeek key
venv/bin/python main.py
```

Set `DRY_RUN=true` to test without posting anywhere. All thresholds live in `.env`; `scripts/backfill_check.py --since 24` replays the detector over collected history to help tune them. For autostart, ship `polypulse.service` to systemd (see above).

Notes on the actual Gamma API field quirks are in [docs/api_notes.md](docs/api_notes.md).

---

<div align="center">

MIT © [Bulat](https://github.com/Sliptval)

</div>
