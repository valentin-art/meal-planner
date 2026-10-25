"""Проверка стенда tools/ingress-test: приложение работает под префиксом.

URL резолвятся так же, как в браузере, — от адреса страницы (urljoin), поэтому
проверка ловит абсолютные пути (/assets, /api), которые за HA Ingress ушли бы
мимо приложения.

Запуск: python3 tools/ingress-test/check.py [http://localhost:8080/ingress-test/]
Только stdlib. Выход ≠ 0, если хоть одна проверка не прошла.
"""

import json
import sys
import urllib.error
import urllib.request
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

PAGE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/ingress-test/"
TIMEOUT = 10

failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(f"{'OK  ' if ok else 'FAIL'} {what}")
    failures += not ok


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url: str, data: dict | None = None, follow: bool = True):
    """(status, headers, body) — без исключений на 4xx/5xx; 0, если стенд не отвечает."""
    req = urllib.request.Request(url)
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(*([] if follow else [NoRedirect()]))
    try:
        with opener.open(req, timeout=TIMEOUT) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"     {url}: {e}")
        return 0, Message(), b""


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("src", "href") and value:
                self.urls.append(value)


def main() -> int:
    prefix = urlsplit(PAGE).path  # /ingress-test/
    origin = urljoin(PAGE, "/")

    # 1. Без завершающего слеша — редирект на форму со слешем.
    status, headers, _ = request(PAGE.rstrip("/"), follow=False)
    location = urljoin(PAGE, headers.get("Location", ""))
    check(
        status == 301 and location == PAGE,
        f"{prefix.rstrip('/')} → 301 на {prefix} ({status} {location})",
    )

    # 2. Страница и всё, на что она ссылается, — под префиксом.
    status, headers, body = request(PAGE)
    check(
        status == 200 and "text/html" in headers.get("Content-Type", ""),
        f"{prefix} → HTML ({status})",
    )
    links = Links()
    links.feed(body.decode())
    check(bool(links.urls), f"в HTML есть ссылки на ассеты: {links.urls}")
    for link in links.urls:
        url = urljoin(PAGE, link)
        status, headers, _ = request(url)
        check(
            url.startswith(PAGE) and status == 200,
            f"{link} → {urlsplit(url).path} ({status} {headers.get('Content-Type')})",
        )

    # 3. API от адреса страницы, как fetch('api/state').
    status, _, body = request(urljoin(PAGE, "api/state"))
    state = json.loads(body) if status == 200 else {}
    check(
        status == 200 and "today" in state and len(state.get("schedule", [])) == 14,
        f"api/state → {prefix}api/state ({status}, today={state.get('today')})",
    )

    # 4. Действие проходит через прокси и сохраняется; потом вернуть как было.
    if state:
        timing = state["settings"]["shopping_timing"]
        other = "before" if timing == "same" else "same"
        url = urljoin(PAGE, "api/actions/set-shopping-timing")
        status, _, body = request(url, {"shopping_timing": other})
        changed = json.loads(body) if status == 200 else {}
        check(
            status == 200
            and changed["settings"]["shopping_timing"] == other
            and changed["version"] == state["version"] + 1,
            f"api/actions/set-shopping-timing → {status}, version {state['version']} → {changed.get('version')}",
        )
        status, _, _ = request(url, {"shopping_timing": timing})
        check(status == 200, f"настройка возвращена ({timing})")

    # 5. Контроль: абсолютный путь (фронтенд с ведущим '/') мимо префикса не работает.
    status, _, _ = request(urljoin(origin, "/api/state"))
    check(status == 404, f"абсолютный /api/state вне префикса → 404 ({status})")

    print("Всё OK" if not failures else f"Не прошло проверок: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
