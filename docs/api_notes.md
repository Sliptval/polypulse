# Gamma API — фактический маппинг полей (проверено 2026-07-12)

Базовый URL: `https://gamma-api.polymarket.com`, авторизация не нужна.

Основной вызов: `GET /events?closed=false&order=volume24hr&ascending=false&limit=100` —
возвращает JSON-массив событий, у каждого события вложенный массив `markets`.

## Неочевидное поведение полей

| Поле рынка | Интуитивно ждёшь | Фактически |
|---|---|---|
| `outcomes` | массив | **JSON-строка**: `'["Yes", "No"]'` — нужен `json.loads` |
| `outcomePrices` | массив | **JSON-строка**: `'["0.2025", "0.7975"]'` — `json.loads`, элементы — строки |
| `volume` | строка-число | строка-число, но есть числовой дубль **`volumeNum`** (float) |
| `liquidity` | строка-число | строка-число, есть числовой дубль **`liquidityNum`** (float) |
| `volume24hr` | строка-число | **уже float** (число, не строка) |

У некоторых рынков `outcomes`/`outcomePrices` могут отсутствовать или быть пустыми —
парсер возвращает `None` для `yes_price` в таких случаях.

## Используемые поля рынка

- `question`, `slug`, `conditionId` — строки, как в спеке.
- `outcomes` + `outcomePrices` — параллельные (после `json.loads`); рынок считается
  бинарным, если `outcomes == ["Yes", "No"]` (регистронезависимо); `yes_price` = float
  первого элемента `outcomePrices`.
- `volumeNum` (fallback: `volume` через float), `volume24hr`, `liquidityNum`
  (fallback: `liquidity`).
- `endDate`, `createdAt` — ISO-8601 с `Z` или смещением; бывают микросекунды
  (`2025-07-02T16:54:40.860413Z`).
- `closed`, `active` — bool.

## Поля события (используются частично)

`volume24hr`, `volume`, `liquidity` у события — **уже числа** (float), в отличие от
рынка. Событие несёт `markets`; negRisk-события (например «World Cup Winner»)
содержат десятки бинарных под-рынков — каждый обрабатывается как отдельный рынок.

## Прочее

- Один запрос с `limit=100` покрывает топ-100 событий по суточному объёму — этого
  достаточно для v1, пагинация не используется.
- `endDateIso`/`startDateIso` существуют, но берём канонические `endDate`/`createdAt`.
