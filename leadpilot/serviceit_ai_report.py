"""Pure evidence report rendering. No credentials, transport or CRM side effects."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
import re


def plain(value, limit=5000):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or re.search(r"[<>@`\[\]{}#*]|[a-z][a-z0-9+.-]*://|www\.|mailto:|tel:|t\.me/|wa\.me/|\+\d|\d[\d ()-]{9,}\d", value, re.I)
            or re.search(r"[\x00-\x08\x0b-\x1f\x7f]|ignore|system:|assistant:|инструкц|игнорир", value, re.I)):
        raise ValueError("REPORT_UNSAFE_TEXT")
    return value.strip()


@dataclass(frozen=True)
class SignalReport:
    score: int
    score_version: str
    summary: str
    service_recommendation: str
    evidence_quote: str
    evidence_reference: str
    possible_need: str
    limitations: str
    verification_status: str
    score_explanation: str
    manager_checklist: tuple[str, ...]
    next_action: str
    full_report: str
    provider: str
    model: str
    timestamp: str

    def to_dict(self):
        return asdict(self)


def build_report(assessment, *, source_text, evidence_quote, reference, ai=None, timestamp=None):
    """Assessment is supplied by the deterministic engine, never by the model.

    Quote remains byte-for-byte unchanged; unsafe source is rejected, not rewritten.
    External free-form conclusions are validated but deliberately not promoted into
    factual prose. A bounded service hypothesis is derived from verified evidence.
    """
    plain(source_text)
    plain(evidence_quote)
    if evidence_quote not in source_text:
        raise ValueError("REPORT_QUOTE_NOT_VERBATIM")
    score = assessment["lead_score"]
    factors = assessment["score_factors"]
    # Engine explanations also carry non-point metadata. Never sum age/id/rules.
    if isinstance(factors, dict):
        factors = {k:v for k,v in factors.items() if k not in {"signal_id", "days_since_signal", "rules"}}
    if (type(score) is not int or not 0 <= score <= 100 or not isinstance(factors, dict)
            or not factors or any(type(v) is not int or v < 0 for v in factors.values())
            or min(100, sum(factors.values())) != score):
        raise ValueError("REPORT_SCORE_FACTORS_INVALID")
    version = plain(assessment["score_version"], 100)
    service = plain(assessment["suggested_service"], 200)
    reference = plain(reference, 200)
    status, provider, model = "NOT_RUN", "NOT_RUN", "NOT_RUN"
    need = "AI NOT_RUN: гипотеза модели отсутствует. Проверить применимость услуги по источнику."
    if ai is not None:
        from .ai import ALLOWED
        if not isinstance(ai, dict) or ai.get("provider") not in ("gigachat", "routerai"):
            raise ValueError("REPORT_APPROVED_PROVIDER_REQUIRED")
        confidence = ai.get("confidence")
        if (ai.get("origin") != "AI_hypothesis" or ai.get("quality_status") != "complete"
                or ai.get("service") not in ALLOWED or ai["service"] == "Требуется проверка"
                or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 < confidence <= 1
                or not isinstance(ai.get("evidence_quote"), str)
                or not ai["evidence_quote"].strip() or ai["evidence_quote"] not in source_text):
            raise ValueError("REPORT_AI_QUALITY_BLOCKED")
        plain(ai["possible_need"], 400)
        plain(ai["evidence_quote"])
        # Never repeat model-invented facts or demand claims in a customer card.
        provider, model = plain(ai["provider"], 80), plain(ai["model"], 80)
        status = "HYPOTHESIS_UNVERIFIED"
        need = f"HYPOTHESIS — проверить применимость услуги «{plain(ai['service'], 100)}» к приведённой цитате. Потребность не подтверждена."
    limitations = ("Источник подтверждает только приведённый текст. Бюджет, сроки, договор, решение о покупке и контактное согласие не установлены. "
                   "Вакансия сама по себе не является проектом или лидом. Проверки клиента, дублей во всей CRM, стоп-листа и правового основания требуют отдельного подтверждения.")
    checklist = ("Сверить цитату, дату и происхождение сигнала.", "Подтвердить компанию и проверить существующего клиента и дубли во всей CRM.",
                 "Проверить STOP_REASON, стоп-лист и законное основание контакта.", "Уточнить задачу, объём, сроки и бюджет только после разрешения контакта.",
                 "Зафиксировать решение менеджера; не создавать повторную задачу.")
    summary = f"Оценка сигнала {score}/100; услуга для проверки: {service}. Статус AI: {status}."
    factor_labels = {"region": "Регион", "segment": "Отраслевой сегмент", "signal": "Тип сигнала",
                     "freshness": "Свежесть сигнала", "service_fit": "Соответствие услуге", "contact": "Наличие контакта"}
    explanation = "; ".join(f"{factor_labels.get(k, plain(k, 100))}: {v} баллов" for k, v in factors.items())
    action = "Проверить источник и корпоративные допуски. До проверки не контактировать."
    stamp = timestamp or datetime.now(timezone.utc).isoformat()
    datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    sections = [("Сервис ИТ | AI-анализ", summary), ("Lead Score и версия", f"{score}/100; {version}. Confidence модели не является Lead Score."),
                ("Проверенная дословная цитата", evidence_quote), ("Основание / ссылка на доказательство", reference),
                ("Возможная потребность — НЕПРОВЕРЕННАЯ ГИПОТЕЗА", need), ("Ограничения бизнеса", limitations),
                ("Факторы детерминированной оценки", explanation), ("Чек-лист менеджера", "\n".join(f"{i+1}. {s}" for i,s in enumerate(checklist))),
                ("Следующее действие", action), ("Модель и время", f"{provider} / {model}; {stamp}; {status}")]
    full = "\n\n".join(f"{title}\n{body}" for title, body in sections)
    return SignalReport(score, version, summary, service, evidence_quote, reference, need, limitations, status, explanation, checklist, action, full, provider, model, stamp)
