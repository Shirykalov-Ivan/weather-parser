#!/usr/bin/env python3
"""
Задача: вывод погоды по списку городов.

1. Загружает список городов по публичной ссылке (облако Mail.ru, при недоступности —
   резервный источник gistpad.com) в память.
2. Для каждого уникального города получает данные через API wttr.in (формат JSON).
3. Строит модель WeatherData (Город, температура в °C, Страна).
4. Выводит погоду по каждому городу: "Tokyo, Japan +18 °C".
5. Группирует города по странам: количество городов, средняя / минимальная /
   максимальная текущая температура.

Используются только стандартные модули Python (json, urllib, dataclasses),
никакие внешние пакеты не требуются.
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass

# Публичная ссылка на папку с файлом в облаке Mail.ru (из условия задания).
CLOUD_PUBLIC_URL = "https://cloud.mail.ru/public/LWcw/kpMqFgztw"

# Резервный источник с тем же списком городов (используется, если облако
# недоступно или изменило формат отдачи).
GISTPAD_URL = "https://gistpad.com/raw/vk-task-14"


# Базовый адрес из условия задания. Если HTTPS-доступ к wttr.in заблокирован,
# запрос автоматически повторяется по HTTP
API_URL = "https://wttr.in/{city}?format=j1"
API_URL_FALLBACK = "http://wttr.in/{city}?format=j1"

# Таймаут одного запроса в секундах.
REQUEST_TIMEOUT = 10.0

# Регулярное выражение для адреса публичной ссылки вида:
# https://cloud.mail.ru/public/XXXX/YYYY  (два сегмента в пути).
WEBLINK_RE = re.compile(r"/public/([A-Za-z0-9_-]+/[A-Za-z0-9_-]+)/?$")


@dataclass
class WeatherData:
    """Модель данных о текущей погоде в городе"""

    city: str
    temperature_c: float
    country: str


def http_get(url: str, timeout: float = REQUEST_TIMEOUT) -> bytes:
    """Выполняет GET-запрос и возвращает тело ответа."""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (CityWeather test task)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def get_cloud_weblink_dispatch_url() -> str:
    """Получает служебный адрес раздачи публичных файлов облака."""

    body = http_get("https://cloud.mail.ru/api/v2/dispatcher")
    data = json.loads(body.decode("utf-8"))
    prefix = data["body"]["weblink_get"][0]["url"]
    if not prefix:
        raise RuntimeError("Облако не вернуло адрес раздачи (weblink_get пуст)")
    return prefix


def load_cities_from_cloud(public_url: str) -> list[str]:
    """Скачивает список городов из публичной папки облака Mail.ru."""

    prefix = get_cloud_weblink_dispatch_url()
    match = WEBLINK_RE.search(public_url)
    if not match:
        raise RuntimeError(f"Не удалось распознать weblink в ссылке: {public_url}")
    weblink = match.group(1)

    body = http_get(f"{prefix.rstrip('/')}/{weblink}")
    return split_cities(body.decode("utf-8"))


def load_cities_from_url(url: str) -> list[str]:
    """Скачивает список городов по произвольной ссылке (обычный текст)."""

    body = http_get(url)
    return split_cities(body.decode("utf-8"))


def split_cities(text: str) -> list[str]:
    """Превращает текст в список уникальных городов, сохраняя порядок.

    Города могут разделяться переносами строк, запятыми или пробелами;
    пустые строки и комментарии (начинающиеся с '#') игнорируются.
    """
    raw_parts = re.split(r"[\n,;]+", text)
    cities = []
    seen = set()
    for part in raw_parts:
        name = part.strip()
        if not name or name.startswith("#"):
            continue
        if name not in seen:
            seen.add(name)
            cities.append(name)
    return cities


def load_cities() -> list[str]:
    """Загружает города: сначала облако, при неудаче — gistpad.

    Возвращает список уникальных городов. Если не получилось ни одного
    источника, выбрасывает RuntimeError.
    """
    errors = []
    sources = [
        ("облако Mail.ru", lambda: load_cities_from_cloud(CLOUD_PUBLIC_URL)),
        ("gistpad.com", lambda: load_cities_from_url(GISTPAD_URL)),
    ]
    for source_name, loader in sources:
        try:
            cities = loader()
            if cities:
                print(f"Города загружены из источника: {source_name}")
                return cities
            errors.append(f"{source_name}: список пуст")
        except Exception as exc:  # noqa: BLE001 — тут ловим всё, чтобы попробовать резерв
            errors.append(f"{source_name}: {exc.__class__.__name__}: {exc}")
    raise RuntimeError(
        "Не удалось получить список городов ни из одного источника.\n"
        + "\n".join(f"  - {e}" for e in errors)
    )


def fetch_weather_json(city: str) -> dict:
    """Запрашивает JSON с погодой у API wttr.in для заданного города.
    Сначала пробуется HTTPS-адрес из условия, при сетевой ошибке или ошибке
    сервера выполняется повторный запрос по HTTP (fallback)"""

    last_error = None
    for url_template in (API_URL, API_URL_FALLBACK):
        url = url_template.format(city=city)
        try:
            body = http_get(url)
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 404 — город не найден в базе; пробовать другой протокол бессмысленно.
            if exc.code == 404:
                raise
            last_error = exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
    raise RuntimeError(
        f"Не удалось получить данные от wttr.in для города '{city}': {last_error}"
    )


def parse_weather(city: str, raw: dict) -> WeatherData:
    """Превращает JSON-ответ wttr.in в модель WeatherData.
    * temperature_c — current_condition[0].temp_C;
    * country       — nearest_area[0].country[0].value (страну определяет сервис)"""

    current = raw["current_condition"][0]
    nearest = raw["nearest_area"][0]
    temperature_c = float(current["temp_C"])
    country = nearest["country"][0]["value"].strip()
    return WeatherData(city=city, temperature_c=temperature_c, country=country)


def format_temperature(value: float) -> str:
    """Форматирует температуру в стиле задания: '+18 °C' или '-5 °C'"""
    rounded = round(value)
    sign = "+" if rounded >= 0 else ""
    return f"{sign}{rounded} °C"


def print_city_weather(items: list[WeatherData]) -> None:
    """Выводит информацию по каждому городу: 'City, Country +18 °C'."""
    print("\nПогода по городам:")
    print("-" * 40)
    for item in items:
        print(f"{item.city}, {item.country} {format_temperature(item.temperature_c)}")


def summarize_by_country(items: list[WeatherData]) -> None:
    """Группирует города по странам и выводит сводную статистику.
    Для каждой страны: количество городов, средняя, минимальная и
    максимальная текущая температура"""

    groups = defaultdict(list)
    for item in items:
        groups[item.country].append(item.temperature_c)

    print("\nСводка по странам:")
    print("-" * 60)
    for country in sorted(groups):
        temps = groups[country]
        count = len(temps)
        avg = sum(temps) / count
        low = min(temps)
        high = max(temps)
        word = "city" if count == 1 else "cities"
        print(
            f"{country} — {count} {word}, "
            f"avg: {format_temperature(avg)}, "
            f"min: {format_temperature(low)}, "
            f"max: {format_temperature(high)}"
        )


def main() -> int:
    try:
        cities = load_cities()
    except RuntimeError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(f"Загружено уникальных городов: {len(cities)}")

    weather_items: list[WeatherData] = []
    skipped: list[tuple[str, str]] = []

    for city in cities:
        print(f"Запрашиваю погоду для {city} ...")
        try:
            raw = fetch_weather_json(city)
            weather_items.append(parse_weather(city, raw))
        except RuntimeError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            skipped.append((city, str(exc)))
        except (KeyError, IndexError, ValueError) as exc:
            print(f"  ! Неожиданный формат ответа для {city}: {exc}", file=sys.stderr)
            skipped.append((city, str(exc)))

    if not weather_items:
        print("Не удалось получить погоду ни для одного города.", file=sys.stderr)
        return 1

    print_city_weather(weather_items)
    summarize_by_country(weather_items)

    if skipped:
        print(f"\nНе обработано городов: {len(skipped)} ({[c for c, _ in skipped]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
