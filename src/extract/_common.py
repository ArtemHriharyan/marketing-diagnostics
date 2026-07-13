"""Общая обвязка для экстракторов: секреты, окно дат, ретраи, ошибки.

Здесь живёт всё, что одинаково у Метрики (Logs/Reports) и Директа:
    - чтение токенов из clients/<name>/.env (python-dotenv), БЕЗ логирования;
    - разрешение окна выгрузки из config/defaults и деление на месячные чанки;
    - HTTP с экспоненциальным бэкоффом (3 попытки) и уважением rate limits;
    - единая семантика ошибки авторизации -> «источник недоступен», а не крэш.

LLM здесь не вызывается (принцип 3): чистый детерминированный код.

ВАЖНО ПРО СЕКРЕТЫ (принцип 6): токен читается один раз и передаётся только в
заголовок Authorization. Ни токен, ни заголовки запроса нигде не логируются и
не попадают в тексты исключений.
"""

from __future__ import annotations

import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

try:  # requests — рантайм-зависимость; в части unit-тестов сессия мокается.
    import requests
except Exception:  # pragma: no cover - окружение без requests
    requests = None  # type: ignore


# ── Константы ретраев ──────────────────────────────────────────────────────
MAX_ATTEMPTS = 3               # всего попыток на один HTTP-вызов
BACKOFF_BASE_SEC = 1.0         # база экспоненты: base * 2**(attempt-1)
BACKOFF_CAP_SEC = 60.0         # потолок паузы между попытками
RETRY_STATUSES = (500, 502, 503, 504)  # временные серверные ошибки -> ретрай
AUTH_STATUSES = (401, 403)     # мёртвый/невалидный токен -> источник недоступен
RATE_LIMIT_STATUS = 429        # превышен лимит -> ждём Retry-After и повторяем

DEFAULT_WINDOW_MONTHS = 12     # дубль config/defaults.yaml на случай отсутствия

# Код возврата, который оркестратор трактует как «источник недоступен»
# (штатная деградация, принцип 4), а НЕ как крэш пайплайна.
EXIT_SOURCE_UNAVAILABLE = 3


# ── Исключения ─────────────────────────────────────────────────────────────
class SourceUnavailable(RuntimeError):
    """Источник недоступен: сеть легла, лимиты, битый ответ и т.п.

    Несёт ``exit_code`` (EXIT_SOURCE_UNAVAILABLE), чтобы оркестратор мог
    завершить этап управляемо, не роняя пайплайн.
    """

    def __init__(self, source: str, message: str,
                 exit_code: int = EXIT_SOURCE_UNAVAILABLE) -> None:
        super().__init__(message)
        self.source = source
        self.exit_code = exit_code


class AuthError(SourceUnavailable):
    """Мёртвый или отсутствующий токен. Частный случай недоступности источника."""


def auth_dead_message(source: str) -> str:
    """Понятное аналитику сообщение о протухшем токене."""
    return f"токен {source.upper()} мёртв, обнови в .env (clients/<name>/.env)"


# ── Секреты ────────────────────────────────────────────────────────────────
def load_env(env_file: Path) -> dict[str, str]:
    """Прочитать clients/<name>/.env в словарь через python-dotenv.

    Значения НЕ пишутся в os.environ (не засоряем процесс) и НЕ логируются.
    Отсутствие файла -> пустой словарь (доступность решит get_token).
    """
    from dotenv import dotenv_values  # локальный импорт: нужен только в рантайме

    path = Path(env_file)
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def get_token(env: dict[str, str], key: str, source: str) -> str:
    """Достать токен из env по ключу или упасть с внятным AuthError.

    Сам токен в сообщение об ошибке НЕ попадает.
    """
    token = (env or {}).get(key)
    if not token:
        raise AuthError(
            source,
            f"нет {key} в .env — {auth_dead_message(source)}",
        )
    return token


# ── Окно дат и месячные чанки ──────────────────────────────────────────────
def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _months_back(anchor: date, months: int) -> date:
    """Дата на ``months`` месяцев раньше ``anchor`` (день клампится к длине месяца)."""
    total = (anchor.year * 12 + (anchor.month - 1)) - months
    year, month = divmod(total, 12)
    month += 1
    # Последний день целевого месяца, чтобы не выйти за границу (напр. 31 -> 28).
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    last_day = (next_first.toordinal() - 1)
    day = min(anchor.day, date.fromordinal(last_day).day)
    return date(year, month, day)


def resolve_window(
    config: dict[str, Any],
    defaults: dict[str, Any] | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    """Полное окно выгрузки [date_from, date_to] из конфига (инкремента нет).

    Приоритет: явные ``data_window.date_from``/``date_to`` в конфиге клиента;
    иначе ``data_window.months`` (или ``defaults.data_window_months``) назад от
    ``today`` (по умолчанию — сегодня).
    """
    today = today or date.today()
    window = (config.get("data_window") or {}) if config else {}

    if window.get("date_from") and window.get("date_to"):
        return _parse_date(window["date_from"]), _parse_date(window["date_to"])

    months = window.get("months")
    if not months and defaults:
        months = defaults.get("data_window_months")
    months = int(months or DEFAULT_WINDOW_MONTHS)
    return _months_back(today, months), today


def month_chunks(date_from: date, date_to: date) -> list[tuple[date, date]]:
    """Разбить окно по календарным месяцам (Logs API не любит большие окна).

    Возвращает список [(chunk_from, chunk_to)], где границы чанков совпадают с
    границами месяцев, а концы окна не выходят за [date_from, date_to].
    """
    if date_from > date_to:
        return []

    chunks: list[tuple[date, date]] = []
    cur = date_from
    while cur <= date_to:
        if cur.month == 12:
            month_end = date(cur.year, 12, 31)
        else:
            month_end = date(cur.year, cur.month + 1, 1).fromordinal(
                date(cur.year, cur.month + 1, 1).toordinal() - 1
            )
        chunk_to = min(month_end, date_to)
        chunks.append((cur, chunk_to))
        cur = date.fromordinal(chunk_to.toordinal() + 1)
    return chunks


def fmt(d: date) -> str:
    """Дата в формате API Яндекса (YYYY-MM-DD)."""
    return d.strftime("%Y-%m-%d")


# ── Файловая раскладка raw ─────────────────────────────────────────────────
def source_dir(paths: Any, source: str) -> Path:
    """Каталог сырья одного источника: data/raw/<source>/."""
    return Path(paths.raw) / source


def reset_dir(path: Path) -> Path:
    """Очистить и пересоздать каталог (перезапись СВОЕГО слоя целиком, принцип 2)."""
    path = Path(path)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── HTTP с ретраями ────────────────────────────────────────────────────────
def _retry_after_seconds(response: Any, attempt: int) -> float:
    """Сколько ждать перед повтором: заголовок Retry-After или экспонента."""
    header = None
    try:
        header = response.headers.get("Retry-After")
    except Exception:
        header = None
    if header:
        try:
            return min(float(header), BACKOFF_CAP_SEC)
        except (TypeError, ValueError):
            pass
    return backoff_delay(attempt)


def backoff_delay(attempt: int) -> float:
    """Экспоненциальная задержка для попытки ``attempt`` (1-based), с потолком."""
    return min(BACKOFF_BASE_SEC * (2 ** (attempt - 1)), BACKOFF_CAP_SEC)


def http_request(
    session: Any,
    method: str,
    url: str,
    *,
    source: str,
    max_attempts: int = MAX_ATTEMPTS,
    retry_statuses: tuple[int, ...] = RETRY_STATUSES,
    sleeper: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> Any:
    """Один HTTP-вызов с экспоненциальным бэкоффом и уважением rate limits.

    Политика:
        - 401/403          -> сразу AuthError (токен мёртв, ретраить бесполезно);
        - 429              -> ждём Retry-After и повторяем (в рамках max_attempts);
        - 5xx / сетевой сбой -> экспоненциальный бэкофф и повтор;
        - иначе            -> возвращаем ответ (статус проверяет вызывающий код).

    Токен/заголовки НЕ логируются и НЕ попадают в исключения.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = session.request(method, url, **kwargs)
        except Exception as exc:  # сетевой сбой: ретраим, детали токена не светим
            last_exc = exc
            if attempt < max_attempts:
                sleeper(backoff_delay(attempt))
                continue
            raise SourceUnavailable(
                source, f"сеть недоступна после {max_attempts} попыток: {type(exc).__name__}"
            ) from exc

        status = getattr(response, "status_code", None)

        if status in AUTH_STATUSES:
            raise AuthError(source, auth_dead_message(source))

        if status == RATE_LIMIT_STATUS:
            if attempt < max_attempts:
                sleeper(_retry_after_seconds(response, attempt))
                continue
            raise SourceUnavailable(
                source, f"rate limit (429) не отпустил за {max_attempts} попыток"
            )

        if status in retry_statuses:
            last_exc = SourceUnavailable(source, f"HTTP {status}")
            if attempt < max_attempts:
                sleeper(backoff_delay(attempt))
                continue
            raise SourceUnavailable(
                source, f"HTTP {status} после {max_attempts} попыток"
            )

        return response

    # недостижимо, но для полноты
    raise SourceUnavailable(source, "исчерпаны попытки")  # pragma: no cover


def ensure_ok(response: Any, source: str, context: str = "") -> Any:
    """Проверить финальный статус ответа (после ретраев). 401/403 -> AuthError."""
    status = getattr(response, "status_code", None)
    if status in AUTH_STATUSES:
        raise AuthError(source, auth_dead_message(source))
    if status is None or status >= 400:
        suffix = f" ({context})" if context else ""
        raise SourceUnavailable(source, f"HTTP {status}{suffix}")
    return response


# ── Запись табличного сырья (parquet или csv) ──────────────────────────────
def resolve_raw_format(spec: dict[str, Any] | None, default: str = "csv") -> str:
    """Формат сырья источника: parquet / csv / auto.

    ``spec`` — блок источника из config.sources.<name>. Явные "parquet"/"csv"
    уважаются как есть; "auto" (или пусто) -> parquet, если доступен pyarrow,
    иначе ``default`` (csv). Так пайплайн не падает в окружении без pyarrow.
    """
    fmt = str((spec or {}).get("raw_format") or "auto").lower()
    if fmt in ("csv", "parquet"):
        return fmt
    try:  # pragma: no cover - зависит от окружения
        import pyarrow  # noqa: F401
        return "parquet"
    except Exception:
        return default


def write_table(
    path_no_ext: Path,
    records: Iterable[dict[str, Any]],
    fields: list[str],
    fmt: str = "csv",
) -> Path:
    """Записать список словарей как parquet или csv с фиксированным набором колонок.

    Возвращает фактический путь (с расширением). Порядок колонок = ``fields``;
    отсутствующие ключи заполняются пустым значением. Расширение выбирается по
    ``fmt`` — на него опирается transform.
    """
    import csv

    path_no_ext = Path(path_no_ext)
    rows = list(records)
    if fmt == "parquet":
        import pandas as pd  # локальный импорт: parquet нужен не всегда

        out = path_no_ext.with_suffix(".parquet")
        pd.DataFrame(rows, columns=fields).to_parquet(out, index=False)
        return out

    out = path_no_ext.with_suffix(".csv")
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
    return out


def count_data_rows(text: str, has_header: bool = True) -> int:
    """Число строк данных в TSV/CSV-тексте (без пустых и без строки заголовка)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0
    return len(lines) - 1 if has_header else len(lines)
