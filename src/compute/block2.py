"""Блок 2 — трафик, каналы и атрибуция (каталог v2 §7, T01–T10).

Проверки (config/methodology.yaml, catalog-proveryaemyh-marketingovyh-ugroz-v2.md §7):
    T01  внешние ссылки не размечены UTM                       [visits]
    T02  наивная модель против corrected lastsign               [visits]
    T03  self-referral через смену домена/платёжки/виджета      [visits, referer_domains]
    T04  каналы сравниваются по разным моделям атрибуции        [visits, costs,
                                                                  goal_attribution_models]
    T05  брендовый и небрендовый спрос смешаны                  [seo_queries] (+direct_queries)
    T06  звонки/карты/мессенджеры/офлайн-обращения невидимы     [client_answers] (+visits)
    T07  cookie-визит трактуется как клиент                     [visits]
    T08  зависимость от одного канала/кампании                  [visits, costs,
                                                                  visit_campaign_map]
    T09  аномалия канала — поломка измерений                    [visits] (+costs, client_answers)
    T10  реферальный спам/боты/технические домены                [visits, referer_domains,
                                                                  referral_geo]

Контракт:
    Читает   — data/canonical/{visits,costs,direct_campaigns,direct_queries,
               seo_queries}.parquet, inputs/client_answers.yaml (T06/T09),
               config.yaml клиента (brand_terms — T05; sources.direct.macro_goals —
               T04), data/metrics/degradation_report.json (confidence_cap).
    Пишет    — data/metrics/{t01..t10}.csv/.json. БЕЗ LLM.

Уровень уверенности: HIGH зарезервирован за прямыми визит-уровневыми долями
при выборке >= min_sample_visits (T01 utm_tagging_summary, T02 summary, T03
session_break_summary, T07, T08 — по каждому сегменту своя выборка); все
проверки, построенные на эвристических порогах-коэффициентах без формального
теста значимости (T04 reconciliation, T05 brand mix, T06 coverage, T09
аномалии, T10 spam-эвристика, разрезы T01/T02/T08 по сегментам) — MED по
определению, тот же принцип, что у A02–A11 в block1.py. Пороги-коэффициенты
ниже — эвристики (каталог не даёт точных чисел), тот же подход, что и в
block1.py: обоснование — в комментарии у каждой константы.

── T02 — «наивная» vs «corrected lastsign» (methodology v2 §5, обязателен) ──
Наивная модель — ``last_traffic_source_naive`` (сырое значение
``ym:s:lastTrafficSource``), пропущенное через ТУ ЖЕ функцию
``classify_traffic_source``, что и transform (импортирована отсюда, не
переопределена — одна таблица маппинга на весь пайплайн, не две). Corrected —
``source_group_resolved``: результат обязательного carry-forward шага
(``resolve_traffic_source``, transform/build_canonical.py), который уже
восстановил internal/undefined-визиты по цепочке clientID ДО того, как
canonical-слой попал в compute. compute здесь ничего не восстанавливает
повторно и не переопределяет source_final/source_group_resolved — только
СРАВНИВАЕТ уже готовые колонки и публикует расхождение (confusion-матрица +
per-channel наивный/corrected дельта для ad/organic). Прямой трафик не
становится «рекламой» никаким условием внутри этого модуля — единственное
место, где сырой lastsign-источник фактически переклассифицируется по
правилам атрибуции, это carry-forward в transform (см. CLAUDE.md принцип 2:
слои неизменяемы, compute не переписывает чужой слой). Если canonical-слой
собран до появления carry-forward (колонки source_group_resolved нет) — T02
пишет unavailable, а не считает по суррогату (не подменять отсутствующие
данные).

── T03 — self-referral: реестровая недоступность без домена ─────────────────
Каталог требует «цепочки доменов, рефереры и разрывы сессий». Сырой referer/
домен перехода (``ym:s:referer``) НЕ входит в SCHEMAS["visits"]
(build_canonical.py) — экстрактор запрашивает поле у Logs API (см.
VISIT_FIELDS_BASE, src/extract/metrika_logs.py), но transform не переносит
его в canonical. Поэтому T03 требует реестровый вход ``referer_domains``
и без него не запускается; частота internal/undefined не является
заменой проверки self-referral и не публикуется как её verdict.

── T10 — реестровая недоступность без referer и гео ─────────────────────────
Каталог требует одновременно реферер, поведение, географию и повторяемость.
Без ``referer_domains`` и ``referral_geo`` T10 не запускается реестром:
повторяемость cookie с нулевой вовлечённостью не подменяет этот контракт.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from . import common
from ..pipeline import degradation as degradation_mod
from ..pipeline import orchestrator as orchestrator_mod
from ..transform.build_canonical import classify_traffic_source, is_brand_query

# ── Пороги-эвристики (каталог не даёт точных чисел; см. докстринг модуля и
# block1.py — тот же принцип: обоснование у каждой константы) ───────────────

# T01: минимум визитов в НЕ-рекламной группе источника, чтобы вообще судить о
# доле неразмеченных ссылок (иначе шум на единичных визитах); доля визитов
# без utm_source в такой группе, начиная с которой это "вероятно неразмеченный
# внешний источник", а не единичный случай.
_T01_MIN_VISITS_FOR_GROUP_CHECK = 30
_T01_HIGH_UNTAGGED_SHARE = 0.5
_T01_EXTERNAL_GROUPS = ("referral", "social", "messenger")

# T04: во сколько раз должны разойтись "чистые конверсии" по данным Метрики
# (визит-уровень, lastsign) и по данным Директа (сервер, собственная
# атрибуция), чтобы считать модели атрибуции несовместимыми для прямого
# сравнения; минимум конверсий с каждой стороны, чтобы сравнение было
# осмысленным (тот же порог материальности, что A05/A11 в block1.py).
_T04_ATTRIBUTION_MISMATCH_RATIO_LOW = 0.5
_T04_ATTRIBUTION_MISMATCH_RATIO_HIGH = 2.0
_T04_MIN_CONVERSIONS_FOR_COMPARISON = 5

# T05: минимум показов/кликов, чтобы вообще оценивать бренд/небренд-микс
# (иначе шум); доля бренд-спроса, начиная с которой микс считается
# "брендово-тяжёлым" (риск выдать известность компании за результат
# SEO/рекламы по холодному спросу — формулировка угрозы из каталога).
_T05_MIN_VOLUME_FOR_CHECK = 50
_T05_HIGH_BRAND_SHARE = 0.5

# T08: доля визитов/расхода одного канала или одной кампании, начиная с
# которой зависимость считается рискованной (небольшое изменение аукциона/
# алгоритма резко ухудшает общий результат — формулировка угрозы).
_T08_MIN_VISITS_FOR_CHECK = 30
_T08_CHANNEL_CONCENTRATION_SHARE = 0.6
_T08_CAMPAIGN_CONCENTRATION_SHARE = 0.5

# T09: минимум дней истории канала, чтобы считать медиану бейзлайном (не
# судить о "норме" по 2-3 точкам); минимум медианных визитов/день, чтобы не
# ловить шум на почти-нулевых каналах (канал с медианой 1 визит/день даёт
# тривиальные "аномалии" x3/x0.3 без содержательного смысла); во сколько раз
# день должен отклониться от медианы, чтобы считаться всплеском/провалом;
# окно (дней) для сопоставления даты аномалии с client_answers.changes_log.
_T09_MIN_DAYS_FOR_BASELINE = 7
_T09_MIN_BASELINE_VISITS = 5
_T09_SPIKE_RATIO = 3.0
_T09_DROP_RATIO = 1.0 / 3.0
_T09_CHANGE_LOG_CORRELATION_DAYS = 3

# T10: минимум повторных визитов одного client_id в группе referral с нулевой
# вовлечённостью, чтобы считать client_id кандидатом в реферальный спам/бот
# (единичный "тихий" визит — нормальное поведение, не спам).
_T10_MIN_VISITS_FOR_SPAM_CANDIDATE = 5

_T_CANDIDATE_FLAGS: tuple[str, ...] = (
    "likely_untagged_external_traffic",
    "demand_mix_brand_heavy",
    "coverage_gap",
)
_T_CANDIDATE_FINDINGS: frozenset[str] = frozenset({"non_standardized_utm_source"})
_T_LIMITATION_FINDINGS: frozenset[str] = frozenset({"cookie_visitor_segments"})
_T_SUMMARY_FINDINGS: frozenset[str] = frozenset({
    "utm_tagging_summary", "summary", "seo_brand_mix", "paid_brand_mix",
    "offline_channel_coverage",
})


def _analysis_signal(row: dict[str, Any]) -> str | None:
    """Вернуть стабильный код уже рассчитанного проблемного сигнала строки."""
    for field in _T_CANDIDATE_FLAGS:
        if row.get(field) is True:
            return field
    finding = row.get("finding")
    if finding in _T_CANDIDATE_FINDINGS:
        return str(finding)
    if row.get("check_id") == "T02":
        if finding == "summary" and int(row.get("mismatch_count") or 0) > 0:
            return "attribution_mismatch"
        if finding == "confusion_matrix":
            return "attribution_confusion"
        if finding == "channel_naive_vs_corrected" and int(row.get("delta") or 0) != 0:
            return "channel_attribution_delta"
    return None


def _analysis_role(row: dict[str, Any], signal: str | None) -> str:
    if signal is not None:
        return "candidate"
    finding = row.get("finding")
    status = row.get("status")
    if (
        status in {"unavailable", "unavailable_for_cause"}
        or finding in _T_LIMITATION_FINDINGS
        or (row.get("check_id") == "T06" and bool(row.get("directories_not_answered")))
    ):
        return "limitation"
    if finding in _T_SUMMARY_FINDINGS:
        return "summary"
    if finding == "channel_anomaly_context":
        return "context"
    return "baseline"


def _annotate_analysis_rows(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Добавить единый аналитический контракт, не меняя значения метрик."""
    annotated: list[dict[str, Any]] = []
    evidence_ids = common.assign_evidence_ids(name, [dict(row) for row in rows])
    for source_row, evidence_id in zip(rows, evidence_ids):
        row = dict(source_row)
        check_id = str(row.get("check_id") or name).lower()
        signal = _analysis_signal(row)
        role = _analysis_role(row, signal)
        reason_token = signal or row.get("finding") or row.get("status") or role
        row.update({
            "evidence_id": evidence_id,
            "evidence_label": common.evidence_label(row),
            "row_ref": evidence_id,
            "candidate": signal is not None,
            "row_role": role,
            "candidate_reason": f"{check_id}_{reason_token}",
            "context_refs": [],
        })
        annotated.append(row)

    context_row = next(
        (row for role in ("summary", "limitation", "baseline", "context")
         for row in annotated if row["row_role"] == role),
        None,
    )
    if context_row is not None:
        for row in annotated:
            if row["candidate"] and row["evidence_id"] != context_row["evidence_id"]:
                row["context_refs"] = [context_row["evidence_id"]]
    return annotated


def _write_metric_artifact(
    metrics_dir: Path,
    name: str,
    rows: list[dict[str, Any]],
    *,
    confidence_cap: str | None = None,
) -> tuple[Path, Path]:
    return common.write_metric_artifact(
        metrics_dir,
        name,
        _annotate_analysis_rows(name, rows),
        confidence_cap=confidence_cap,
    )


# ── Общие хелперы (дублируют паттерн block0.py/block1.py — блоки compute не
# делят приватные хелперы через common.py, см. CLAUDE.md принцип 2) ─────────
def _table_nonempty(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return pq.ParquetFile(path).metadata.num_rows > 0
    except OSError:
        return False


def _table_columns(con: Any, table: str) -> set[str]:
    cur = con.execute(f'SELECT * FROM "{table}" LIMIT 0')
    return {d[0] for d in cur.description}


def _confidence_caps(paths: Any) -> dict[str, str]:
    """{check_id: confidence_cap} из уже записанного degradation_report.json."""
    report = common.load_degradation(paths)
    return {
        c.get("check_id"): c.get("confidence_cap", "HIGH")
        for c in (report.get("checks") or [])
        if c.get("check_id")
    }


def _registry_unavailable_reasons(paths: Any) -> dict[str, str]:
    """Причины невыполнимости из реестра текущего compute-прогона."""
    report = common.load_degradation(paths)
    return {
        check.get("check_id"): check.get("reason_if_not_runnable")
        for check in (report.get("checks") or [])
        if check.get("check_id") and check.get("runnable") is False
        and check.get("reason_if_not_runnable")
    }


def _cap(confidence: str, confidence_cap: str) -> str:
    """Прижать confidence к потолку проверки (compute капает вниз, не поднимает)."""
    return degradation_mod.min_confidence(confidence, confidence_cap)


def _sample_confidence(sample_size: int, min_sample_visits: int) -> str:
    """HIGH при визит-уровневой выборке >= порога, иначе MED."""
    return "HIGH" if sample_size >= min_sample_visits else "MED"


def _write_unavailable(metrics_dir: Path, check_id: str, reason: str) -> None:
    """Явная запись «проверка недоступна» вместо молчаливого пропуска."""
    _write_metric_artifact(
        metrics_dir,
        check_id.lower(),
        [{"check_id": check_id, "status": "unavailable", "reason": reason}],
    )


def _write_contract_unavailable(metrics_dir: Path, check_id: str, requirement: str) -> None:
    """Не выдавать verdict при обходе реестра без обязательного контракта."""
    _write_unavailable(
        metrics_dir,
        check_id,
        f"обязательный контракт {requirement} отсутствует; проверка должна быть "
        "остановлена реестром деградации",
    )


def _money(cost_sum: float | None, null_rows: int) -> float | None:
    """SUM(cost_normalized) валиден, только если ни одна строка группы не null."""
    if cost_sum is None or null_rows > 0:
        return None
    return round(float(cost_sum), 2)


def _macro_goal_ids(config: dict[str, Any]) -> list[str]:
    """config.sources.direct.macro_goals -> [id, ...] (строками, как в goal_conv_<id>)."""
    direct_cfg = ((config.get("sources") or {}).get("direct") or {})
    return [
        str(g["id"]) for g in (direct_cfg.get("macro_goals") or [])
        if g.get("id") is not None
    ]


def _net_conversions_expr(con: Any, table: str, goal_ids: list[str]) -> str | None:
    """SQL-выражение "сумма goal_conv_<id> по всем macro_goals" или None,

    если macro_goals не настроены или колонки goal_conv_<id> физически нет в
    таблице (тот же хелпер, что block1.py — см. CLAUDE.md принцип 2).
    """
    if not goal_ids:
        return None
    columns = _table_columns(con, table)
    parts: list[str] = []
    for gid in goal_ids:
        col = f"goal_conv_{gid}"
        if col not in columns:
            return None
        parts.append(f'COALESCE("{col}", 0)')
    return "(" + " + ".join(parts) + ")"


def _median(values: list[float]) -> float | None:
    """Медиана отсортированного списка (не изменяет исходный список)."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


# ── T01 — внешние ссылки не размечены UTM ────────────────────────────────────
def _run_t01(
    paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path,
) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        total_visits, tagged_visits = con.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE utm_source_raw IS NOT NULL AND utm_source_raw != '') "
            "FROM visits"
        ).fetchone()
        by_group = con.execute(
            "SELECT source_group, COUNT(*), "
            "COUNT(*) FILTER (WHERE utm_source_raw IS NOT NULL AND utm_source_raw != '') "
            "FROM visits GROUP BY source_group ORDER BY source_group"
        ).fetchall()
        variants = con.execute(
            "SELECT lower(trim(utm_source_raw)) AS norm, utm_source_raw, COUNT(*) "
            "FROM visits WHERE utm_source_raw IS NOT NULL AND utm_source_raw != '' "
            "GROUP BY norm, utm_source_raw ORDER BY norm, utm_source_raw"
        ).fetchall()
    finally:
        con.close()

    total_visits = int(total_visits or 0)
    tagged_visits = int(tagged_visits or 0)
    tagged_share = (tagged_visits / total_visits) if total_visits else None

    rows: list[dict[str, Any]] = [{
        "check_id": "T01",
        "finding": "utm_tagging_summary",
        "total_visits": total_visits,
        "tagged_visits": tagged_visits,
        "untagged_visits": total_visits - tagged_visits,
        "tagged_share": round(tagged_share, 4) if tagged_share is not None else None,
        "confidence": _cap(
            _sample_confidence(total_visits, min_sample) if total_visits > 0 else "LOW",
            confidence_cap,
        ),
    }]

    for source_group, cnt, tagged in by_group:
        cnt = int(cnt or 0)
        tagged = int(tagged or 0)
        untagged = cnt - tagged
        untagged_share = (untagged / cnt) if cnt else 0.0
        likely_untagged_external = bool(
            source_group in _T01_EXTERNAL_GROUPS
            and cnt >= _T01_MIN_VISITS_FOR_GROUP_CHECK
            and untagged_share >= _T01_HIGH_UNTAGGED_SHARE
        )
        rows.append({
            "check_id": "T01",
            "finding": "by_source_group",
            "source_group": source_group,
            "visit_count": cnt,
            "tagged_count": tagged,
            "untagged_count": untagged,
            "untagged_share": round(untagged_share, 4),
            "min_visits_threshold": _T01_MIN_VISITS_FOR_GROUP_CHECK,
            "untagged_share_threshold": _T01_HIGH_UNTAGGED_SHARE,
            "likely_untagged_external_traffic": likely_untagged_external,
            "confidence": _cap("MED", confidence_cap),
        })

    by_norm: dict[str, list[tuple[str, int]]] = {}
    for norm, raw, cnt in variants:
        by_norm.setdefault(norm, []).append((raw, int(cnt or 0)))

    for norm, variant_list in sorted(by_norm.items()):
        if len(variant_list) <= 1:
            continue
        variant_list = sorted(variant_list, key=lambda v: -v[1])
        rows.append({
            "check_id": "T01",
            "finding": "non_standardized_utm_source",
            "normalized_value": norm,
            "variant_count": len(variant_list),
            "variants": [{"raw": raw, "visit_count": cnt} for raw, cnt in variant_list],
            "total_visits": sum(cnt for _, cnt in variant_list),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "t01", rows, confidence_cap=confidence_cap)


# ── T02 — наивная модель vs corrected lastsign ───────────────────────────────
def _run_t02(
    paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path,
) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        columns = _table_columns(con, "visits")
        if "source_group_resolved" not in columns:
            con.close()
            _write_unavailable(
                metrics_dir, "T02",
                "колонка source_group_resolved отсутствует в visits.parquet — "
                "обязательный carry-forward шаг (methodology v2 §5) не выполнен "
                "на этапе transform для этой выгрузки",
            )
            return
        raw_rows = con.execute(
            "SELECT last_traffic_source_naive, source_group_resolved FROM visits"
        ).fetchall()
    finally:
        con.close()

    naive_available = 0
    mismatch = 0
    confusion: dict[tuple[str, str], int] = {}
    naive_channel_counts: dict[str, int] = {}
    corrected_channel_counts: dict[str, int] = {}

    for naive_raw, corrected_group in raw_rows:
        if naive_raw is None:
            continue
        naive_available += 1
        corrected_group = corrected_group or "other"
        naive_group = classify_traffic_source(naive_raw)
        naive_channel_counts[naive_group] = naive_channel_counts.get(naive_group, 0) + 1
        corrected_channel_counts[corrected_group] = (
            corrected_channel_counts.get(corrected_group, 0) + 1
        )
        key = (naive_group, corrected_group)
        confusion[key] = confusion.get(key, 0) + 1
        if naive_group != corrected_group:
            mismatch += 1

    mismatch_share = (mismatch / naive_available) if naive_available else None

    rows: list[dict[str, Any]] = [{
        "check_id": "T02",
        "finding": "summary",
        "total_visits": len(raw_rows),
        "naive_available_visits": naive_available,
        "mismatch_count": mismatch,
        "mismatch_share": round(mismatch_share, 4) if mismatch_share is not None else None,
        "confidence": _cap(
            _sample_confidence(naive_available, min_sample) if naive_available > 0 else "LOW",
            confidence_cap,
        ),
    }]

    for (naive_group, corrected_group), cnt in sorted(confusion.items(), key=lambda kv: -kv[1]):
        if naive_group == corrected_group:
            continue
        rows.append({
            "check_id": "T02",
            "finding": "confusion_matrix",
            "naive_group": naive_group,
            "corrected_group": corrected_group,
            "visit_count": cnt,
            "confidence": _cap("MED", confidence_cap),
        })

    for channel in ("ad", "organic"):
        naive_cnt = naive_channel_counts.get(channel, 0)
        corrected_cnt = corrected_channel_counts.get(channel, 0)
        delta = corrected_cnt - naive_cnt
        direction = "understated" if delta > 0 else ("overstated" if delta < 0 else "unchanged")
        rows.append({
            "check_id": "T02",
            "finding": "channel_naive_vs_corrected",
            "channel": channel,
            "naive_count": naive_cnt,
            "corrected_count": corrected_cnt,
            "delta": delta,
            "direction": direction,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "t02", rows, confidence_cap=confidence_cap)


# ── T03 — self-referral / разрыв сессии (без домена, см. докстринг) ─────────
def _run_t03(
    paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path,
) -> None:
    _write_contract_unavailable(metrics_dir, "T03", "referer_domains")


# ── T04 — каналы сравниваются по разным моделям атрибуции ──────────────────
def _run_t04(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    _write_contract_unavailable(metrics_dir, "T04", "goal_attribution_models")


# ── T05 — брендовый и небрендовый спрос смешаны ─────────────────────────────
def _run_t05(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    brand_terms = config.get("brand_terms") or []

    con = common.open_duckdb(paths)
    try:
        seo_rows = con.execute(
            "SELECT is_brand, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "GROUP BY is_brand ORDER BY is_brand"
        ).fetchall()

        direct_rows: list[tuple[Any, ...]] = []
        if "direct_queries" in canonical and _table_nonempty(canonical["direct_queries"]):
            direct_rows = con.execute(
                "SELECT query, "
                "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
                "COUNT(*) FILTER (WHERE cost_normalized IS NULL), SUM(clicks) "
                "FROM direct_queries GROUP BY query ORDER BY query"
            ).fetchall()
    finally:
        con.close()

    seo_brand_shows = seo_other_shows = seo_brand_clicks = seo_other_clicks = 0
    for is_brand, shows, clicks in seo_rows:
        shows = int(shows or 0)
        clicks = int(clicks or 0)
        if is_brand:
            seo_brand_shows += shows
            seo_brand_clicks += clicks
        else:
            seo_other_shows += shows
            seo_other_clicks += clicks
    seo_total_shows = seo_brand_shows + seo_other_shows
    seo_brand_share = (seo_brand_shows / seo_total_shows) if seo_total_shows else None

    rows: list[dict[str, Any]] = [{
        "check_id": "T05",
        "finding": "seo_brand_mix",
        "brand_shows": seo_brand_shows,
        "non_brand_shows": seo_other_shows,
        "total_shows": seo_total_shows,
        "brand_share": round(seo_brand_share, 4) if seo_brand_share is not None else None,
        "brand_clicks": seo_brand_clicks,
        "non_brand_clicks": seo_other_clicks,
        "min_volume_threshold": _T05_MIN_VOLUME_FOR_CHECK,
        "high_brand_share_threshold": _T05_HIGH_BRAND_SHARE,
        "demand_mix_brand_heavy": bool(
            seo_total_shows >= _T05_MIN_VOLUME_FOR_CHECK
            and seo_brand_share is not None
            and seo_brand_share >= _T05_HIGH_BRAND_SHARE
        ),
        "confidence": _cap("MED", confidence_cap),
    }]

    if direct_rows:
        brand_cost = other_cost = 0.0
        brand_cost_null = other_cost_null = 0
        brand_clicks = other_clicks = 0
        for query, cost_sum, null_rows, clicks in direct_rows:
            brand = is_brand_query(query, brand_terms)
            cost = _money(cost_sum, int(null_rows or 0))
            clicks = int(clicks or 0)
            if brand:
                brand_clicks += clicks
                if cost is None:
                    brand_cost_null += 1
                else:
                    brand_cost += cost
            else:
                other_clicks += clicks
                if cost is None:
                    other_cost_null += 1
                else:
                    other_cost += cost

        total_paid_clicks = brand_clicks + other_clicks
        brand_click_share = (brand_clicks / total_paid_clicks) if total_paid_clicks else None
        rows.append({
            "check_id": "T05",
            "finding": "paid_brand_mix",
            "brand_clicks": brand_clicks,
            "non_brand_clicks": other_clicks,
            "total_clicks": total_paid_clicks,
            "brand_click_share": (
                round(brand_click_share, 4) if brand_click_share is not None else None
            ),
            "brand_cost_normalized_rub": None if brand_cost_null > 0 else round(brand_cost, 2),
            "non_brand_cost_normalized_rub": (
                None if other_cost_null > 0 else round(other_cost, 2)
            ),
            "min_volume_threshold": _T05_MIN_VOLUME_FOR_CHECK,
            "high_brand_share_threshold": _T05_HIGH_BRAND_SHARE,
            "demand_mix_brand_heavy": bool(
                total_paid_clicks >= _T05_MIN_VOLUME_FOR_CHECK
                and brand_click_share is not None
                and brand_click_share >= _T05_HIGH_BRAND_SHARE
            ),
            "confidence": _cap("MED", confidence_cap),
        })
    else:
        rows.append({
            "check_id": "T05",
            "finding": "paid_brand_mix",
            "status": "unavailable",
            "reason": "direct_queries недоступна",
            "confidence": _cap("LOW", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "t05", rows, confidence_cap=confidence_cap)


# ── T06 — звонки, карты, мессенджеры и офлайн-обращения невидимы ───────────
def _run_t06(
    paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path,
) -> None:
    inputs = common.load_inputs(paths)
    client_answers = inputs.get("client_answers") or {}
    business = client_answers.get("business") or {}
    offline_channels = business.get("offline_lead_channels") or []
    directories = client_answers.get("directories") or {}

    invisible_directories: list[str] = []
    not_answered_directories: list[str] = []
    for name in ("yandex_maps", "gis2", "calltracking"):
        entry = directories.get(name) or {}
        exists = entry.get("exists")
        stats_available = entry.get("stats_available")
        if exists is None:
            not_answered_directories.append(name)
        elif exists and stats_available is not True:
            invisible_directories.append(name)

    call_click_visits = None
    messenger_click_visits = None
    if "visits" in canonical and _table_nonempty(canonical["visits"]):
        con = common.open_duckdb(paths)
        try:
            r = con.execute(
                "SELECT SUM(call_click_count), SUM(messenger_click_count) FROM visits"
            ).fetchone()
        finally:
            con.close()
        call_click_visits = int(r[0] or 0)
        messenger_click_visits = int(r[1] or 0)

    rows: list[dict[str, Any]] = [{
        "check_id": "T06",
        "finding": "offline_channel_coverage",
        "offline_lead_channels_declared": list(offline_channels),
        "invisible_directories": invisible_directories,
        "directories_not_answered": not_answered_directories,
        "website_call_click_visits": call_click_visits,
        "website_messenger_click_visits": messenger_click_visits,
        "coverage_gap": bool(offline_channels or invisible_directories),
        "confidence": _cap("MED", confidence_cap),
    }]

    _write_metric_artifact(metrics_dir, "t06", rows, confidence_cap=confidence_cap)


# ── T07 — cookie-визит трактуется как клиент ────────────────────────────────
def _run_t07(
    paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path,
) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        total_visits, new_visits = con.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE is_new_user) FROM visits"
        ).fetchone()
        distinct_clients, single_visit_clients = con.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE visit_count = 1) FROM ("
            "  SELECT client_id, COUNT(*) AS visit_count FROM visits GROUP BY client_id"
            ") AS per_client"
        ).fetchone()
    finally:
        con.close()

    total_visits = int(total_visits or 0)
    new_visits = int(new_visits or 0)
    returning_visits = total_visits - new_visits
    distinct_clients = int(distinct_clients or 0)
    single_visit_clients = int(single_visit_clients or 0)
    repeat_visit_clients = distinct_clients - single_visit_clients

    new_visit_share = (new_visits / total_visits) if total_visits else None
    repeat_client_share = (
        (repeat_visit_clients / distinct_clients) if distinct_clients else None
    )

    rows: list[dict[str, Any]] = [{
        "check_id": "T07",
        "finding": "cookie_visitor_segments",
        "total_visits": total_visits,
        "distinct_client_ids": distinct_clients,
        "new_visits": new_visits,
        "returning_visits": returning_visits,
        "new_visit_share": round(new_visit_share, 4) if new_visit_share is not None else None,
        "single_visit_client_ids": single_visit_clients,
        "repeat_visit_client_ids": repeat_visit_clients,
        "repeat_visit_client_id_share": (
            round(repeat_client_share, 4) if repeat_client_share is not None else None
        ),
        "cookie_is_not_customer_proxy": True,
        "caveat": (
            "client_id — идентификатор браузерной cookie Метрики, не "
            "подтверждённый идентификатор реального клиента; несколько разных "
            "client_id могут принадлежать одному человеку (смена устройства/"
            "браузера, очистка cookie), и наоборот. Использовать только как "
            "поведенческий сегмент визитов, не как счётчик уникальных клиентов."
        ),
        "confidence": _cap(
            _sample_confidence(total_visits, min_sample) if total_visits > 0 else "LOW",
            confidence_cap,
        ),
    }]

    _write_metric_artifact(metrics_dir, "t07", rows, confidence_cap=confidence_cap)


# ── T08 — зависимость от одного канала или одной кампании ──────────────────
def _run_t08(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    _write_contract_unavailable(metrics_dir, "T08", "visit_campaign_map")


# ── T09 — резкий рост/падение канала вызван поломкой измерений ─────────────
def _run_t09(
    paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path,
) -> None:
    con = common.open_duckdb(paths)
    try:
        daily = con.execute(
            "SELECT source_final, date, COUNT(*) FROM visits GROUP BY source_final, date ORDER BY source_final, date"
        ).fetchall()
    finally:
        con.close()

    by_channel: dict[str, list[tuple[date, int]]] = {}
    for channel, visit_date, cnt in daily:
        by_channel.setdefault(channel, []).append((visit_date, int(cnt or 0)))

    inputs = common.load_inputs(paths)
    client_answers = inputs.get("client_answers") or {}
    changes_log = client_answers.get("changes_log") or []
    change_dates: list[date] = []
    for entry in changes_log:
        raw_date = (entry or {}).get("date")
        if not raw_date:
            continue
        try:
            change_dates.append(datetime.strptime(str(raw_date), "%Y-%m-%d").date())
        except ValueError:
            continue

    change_log_available = bool(change_dates)
    independent_series_available = _table_nonempty(canonical.get("channel_daily_reference"))
    cause_missing: list[str] = []
    if not change_log_available:
        cause_missing.append("журнал изменений")
    if not independent_series_available:
        cause_missing.append("независимый ряд канала")

    anomaly_rows: list[dict[str, Any]] = []
    channels_evaluated = 0
    for channel, series in sorted(by_channel.items()):
        if len(series) < _T09_MIN_DAYS_FOR_BASELINE:
            continue
        med = _median([cnt for _, cnt in series])
        if med is None or med < _T09_MIN_BASELINE_VISITS:
            continue
        channels_evaluated += 1
        for visit_date, cnt in sorted(series):
            ratio = (cnt / med) if med else None
            is_spike = ratio is not None and ratio >= _T09_SPIKE_RATIO
            is_drop = ratio is not None and ratio <= _T09_DROP_RATIO
            if not (is_spike or is_drop):
                continue
            anomaly_rows.append({
                "check_id": "T09",
                "finding": "channel_anomaly_context",
                "channel": channel,
                "date": visit_date.isoformat(),
                "visit_count": cnt,
                "baseline_median": round(med, 2),
                "ratio_to_baseline": round(ratio, 3) if ratio is not None else None,
                "anomaly_type": "spike" if is_spike else "drop",
                "spike_ratio_threshold": _T09_SPIKE_RATIO,
                "drop_ratio_threshold": _T09_DROP_RATIO,
                "causal_claim": False,
                "confidence": _cap("MED", confidence_cap),
            })

    rows: list[dict[str, Any]] = [{
        "check_id": "T09",
        "finding": "summary",
        "status": "unavailable_for_cause" if not (
            change_log_available and independent_series_available
        ) else "context_only",
        "reason": (
            "нет источника для вывода о причине: " + "; ".join(cause_missing)
            if cause_missing else None
        ),
        "channels_evaluated": channels_evaluated,
        "anomalies_detected": len(anomaly_rows),
        "min_days_for_baseline": _T09_MIN_DAYS_FOR_BASELINE,
        "min_baseline_visits": _T09_MIN_BASELINE_VISITS,
        "change_log_available": change_log_available,
        "independent_series_available": independent_series_available,
        "causal_claim": False,
        "confidence": _cap("MED", confidence_cap),
    }]
    rows.extend(anomaly_rows)

    _write_metric_artifact(metrics_dir, "t09", rows, confidence_cap=confidence_cap)


# ── T10 — реферальный спам, боты или технические домены ────────────────────
def _run_t10(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    _write_contract_unavailable(metrics_dir, "T10", "referer_domains и referral_geo")


# ── Диспетчер блока ──────────────────────────────────────────────────────────
def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Выполнить T01–T10 из числа доступных; вернуть имена записанных артефактов."""
    canonical = common.load_canonical(paths)
    config = orchestrator_mod.load_client_config(paths)
    caps = _confidence_caps(paths)
    registry_unavailable = _registry_unavailable_reasons(paths)
    metrics_dir = Path(paths.metrics)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[str] = []

    for check_id in ("T03", "T04", "T08", "T10"):
        reason = registry_unavailable.get(check_id)
        if reason is not None:
            _write_unavailable(metrics_dir, check_id, reason)
            artifacts.append(check_id.lower())

    if "T01" in runnable_ids and "visits" in canonical:
        _run_t01(paths, defaults, caps.get("T01", "HIGH"), metrics_dir)
        artifacts.append("t01")

    if "T02" in runnable_ids and "visits" in canonical:
        _run_t02(paths, defaults, caps.get("T02", "HIGH"), metrics_dir)
        artifacts.append("t02")

    if "T03" in runnable_ids and "T03" not in registry_unavailable and "visits" in canonical:
        _run_t03(paths, defaults, caps.get("T03", "HIGH"), metrics_dir)
        artifacts.append("t03")

    if (
        "T04" in runnable_ids and "T04" not in registry_unavailable
        and "visits" in canonical and "costs" in canonical
    ):
        _run_t04(paths, config, canonical, caps.get("T04", "HIGH"), metrics_dir)
        artifacts.append("t04")

    if "T05" in runnable_ids and "seo_queries" in canonical:
        _run_t05(paths, config, canonical, caps.get("T05", "HIGH"), metrics_dir)
        artifacts.append("t05")

    if "T06" in runnable_ids:
        _run_t06(paths, canonical, caps.get("T06", "HIGH"), metrics_dir)
        artifacts.append("t06")

    if "T07" in runnable_ids and "visits" in canonical:
        _run_t07(paths, defaults, caps.get("T07", "HIGH"), metrics_dir)
        artifacts.append("t07")

    if "T08" in runnable_ids and "T08" not in registry_unavailable and "visits" in canonical:
        _run_t08(paths, defaults, canonical, caps.get("T08", "HIGH"), metrics_dir)
        artifacts.append("t08")

    if "T09" in runnable_ids and "visits" in canonical:
        _run_t09(paths, canonical, caps.get("T09", "HIGH"), metrics_dir)
        artifacts.append("t09")

    if "T10" in runnable_ids and "T10" not in registry_unavailable and "visits" in canonical:
        _run_t10(paths, caps.get("T10", "HIGH"), metrics_dir)
        artifacts.append("t10")

    return artifacts
