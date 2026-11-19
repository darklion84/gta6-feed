"""Тонкая обёртка над requests.

Главное правило: в текст ошибок никогда не попадает URL запроса, потому что в
нём лежат ключ YouTube и токен бота, а логи GitHub Actions в публичном
репозитории видны всем.
"""
import requests

TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (compatible; clips-feed/1.0)"}


class ApiError(Exception):
    def __init__(self, service, status, body=""):
        super().__init__(f"{service}: HTTP {status} {str(body)[:200]}")
        self.service = service
        self.status = status


def _check(service, r):
    if r.status_code >= 400:
        raise ApiError(service, r.status_code, r.text)
    return r


def get_json(service, url, params=None):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT, headers=UA)
    except requests.RequestException as e:
        raise ApiError(service, 0, type(e).__name__) from None
    return _check(service, r).json()


def post_json(service, url, payload):
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT, headers=UA)
    except requests.RequestException as e:
        raise ApiError(service, 0, type(e).__name__) from None
    return _check(service, r).json()


def get_bytes(service, url):
    """Содержимое по ссылке или None, если не получилось."""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        if r.status_code == 200:
            return r.content
    except requests.RequestException:
        pass
    return None


def resolve(url):
    """Конечный адрес после редиректов (для коротких ссылок) или None."""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA, allow_redirects=True, stream=True)
        final = r.url
        r.close()
        return final
    except requests.RequestException:
        return None
