"""Fail-closed preview of a task linked to the ONE accepted synthetic CRM item.

No live REST, no task creation, no employee guesses, no customer contact.
Input metadata must originate from separately authorized read-only portal checks.
"""
from __future__ import annotations

import re
from hashlib import sha256
from urllib.parse import urlsplit

from .bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION

ENTITY = 1062
CATEGORY = 21
ITEM_ID = 3
ITEM_TITLE = "DEMO ServiceIT AI — DEMO-ITREQ-001"
STAGE = "DT1062_21:CLIENT"
KEY_CODE = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"
UF = {
    "inn": "UF_CRM_13_SVCIT_COMPANY_INN",
    "name": "UF_CRM_13_SVCIT_COMPANY_NAME",
    "source": "UF_CRM_13_SVCIT_SIGNAL_SOURCE",
    "url": "UF_CRM_13_SVCIT_SIGNAL_URL",
    "signal": "UF_CRM_13_SVCIT_SIGNAL_ID",
    "kind": "UF_CRM_13_SVCIT_SIGNAL_KIND",
}
SAFE_KEY = re.compile(r"DEMO-IDEMP-[0-9a-f]{32}\Z")
SAFE_SYMBOL = re.compile(r"T[0-9a-f]{1,8}\Z")


class DemoTaskGateBlocked(ValueError):
    """A synthetic task must not be sent until all gates pass."""


def _deny(code: str) -> None:
    raise DemoTaskGateBlocked(code)


def _positive(value: object) -> int:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and re.fullmatch(r"[1-9][0-9]{0,9}", value):
        return int(value)
    _deny("PORTAL_ID_INVALID")


def _yes(value: object) -> bool:
    return value is True or value in ("Y", "true", "1")


def _off(value: object) -> bool:
    return value is False or value in ("N", "false", "0")


def _present(item: dict, name: str) -> object:
    if name in item:
        return item[name]
    # Do not guess the full UF naming transformation here.
    _deny("ORIGINAL_UF_NAMES_REQUIRED")


def task_link_preflight(
    *, item: dict, process_type: dict, owner_types: list[dict],
    responsible_user: dict, task_fields: dict,
) -> dict:
    """Construct tasks.task.add candidate, never execute.

    Per official Bitrix24 docs: owner type short symbol comes from
    crm.enum.ownertype; smart process must be enabled for UF_CRM_TASK.
    Validated responsible ID comes from an authorized user lookup, not
    a guessed employee ID. Output is preview-only, including CRM binding.
    """
    if not all(isinstance(x, dict) for x in (item, process_type, responsible_user, task_fields)):
        _deny("METADATA_SHAPE_INVALID")
    if not isinstance(owner_types, list):
        _deny("OWNER_TYPE_LIST_MISSING")
    if (_positive(item.get("id")) != ITEM_ID
            or _positive(item.get("entityTypeId")) != ENTITY
            or _positive(item.get("categoryId")) != CATEGORY
            or item.get("title") != ITEM_TITLE
            or item.get("stageId") != STAGE):
        _deny("SYNTHETIC_ITEM_IDENTITY_MISMATCH")
    if (item.get("contactIds") not in (None, [])
            or item.get("companyId") not in (None, 0, "0")
            or item.get("parentId2") not in (None, 0, "0")):
        _deny("REAL_CUSTOMER_LINK_FORBIDDEN")
    if (_present(item, UF["inn"]) != "7700000009"
            or _present(item, UF["name"]) != "DEMO COMPANY-001"
            or _present(item, UF["source"]) != "synthetic-fixture"
            or _present(item, UF["signal"]) != "DEMO-ITREQ-001"
            or _present(item, UF["kind"]) != "demo-synthetic"):
        _deny("SYNTHETIC_IMMUTABLE_FIELDS_MISMATCH")
    if not ALL_FIELD_CODES.issubset(item.keys()):
        _deny("REQUIRED_SIXTEEN_UF_FIELDS_MISSING")
    key = _present(item, KEY_CODE)
    expected_key = "DEMO-IDEMP-" + sha256(
        f"{CONTRACT_VERSION}\x007700000009\x00DEMO-ITREQ-001".encode("utf-8")
    ).hexdigest()[:32]
    if not isinstance(key, str) or not SAFE_KEY.fullmatch(key) or key != expected_key:
        _deny("SYNTHETIC_KEY_MISMATCH")
    url = _present(item, UF["url"])
    if not isinstance(url, str):
        _deny("SYNTHETIC_SOURCE_URL_MISMATCH")
    u = urlsplit(url)
    if (u.scheme != "https" or u.hostname != "example.invalid"
            or u.path != "/serviceit-demo/request/001" or u.query or u.fragment):
        _deny("SYNTHETIC_SOURCE_URL_MISMATCH")

    if (_positive(process_type.get("entityTypeId")) != ENTITY
            or not _yes(process_type.get("isUseInUserfieldEnabled"))
            or not isinstance(process_type.get("linkedUserFields"), dict)
            or not _yes(process_type["linkedUserFields"].get("TASKS_TASK|UF_CRM_TASK"))):
        _deny("SMART_PROCESS_TASK_BINDING_NOT_ENABLED")
    if (not _off(process_type.get("isAutomationEnabled"))
            or not _off(process_type.get("isBizProcEnabled"))):
        _deny("SMART_PROCESS_AUTOMATION_MUST_BE_CONFIRMED_OFF")

    matches = [r for r in owner_types if isinstance(r, dict)
               and str(r.get("ID")) == str(ENTITY)]
    if len(matches) != 1:
        _deny("CRM_OWNER_TYPE_NOT_UNIQUE")
    symbol = matches[0].get("SYMBOL_CODE_SHORT")
    if (not isinstance(symbol, str) or not SAFE_SYMBOL.fullmatch(symbol)
            or symbol != f"T{ENTITY:x}"):
        _deny("CRM_OWNER_SYMBOL_NOT_VERIFIED")

    uid = _positive(responsible_user.get("ID"))
    if (responsible_user.get("ACTIVE") not in (True, "Y", "true")):
        _deny("RESPONSIBLE_NOT_ACTIVE")
    # User must still confirm this ID belongs to Mikhail. It is NOT an
    # authenticated assertion merely because user.get found an active account.
    if responsible_user.get("EXTERNAL_AUTH_ID") not in (None, ""):
        _deny("EXTERNAL_USER_NOT_ALLOWED")
    required = ("TITLE", "RESPONSIBLE_ID", "UF_CRM_TASK")
    for key_name in required:
        if key_name not in task_fields:
            _deny("TASK_FIELD_SCHEMA_NOT_CONFIRMED")
    if task_fields["UF_CRM_TASK"].get("type") != "crm":
        _deny("TASK_CRM_BINDING_FIELD_NOT_CRM_TYPE")
    title = "[DEMO / НЕ ЗВОНИТЬ] Проверить сигнал 1С по карточке ServiceIT №3"
    description = (
        "УЧЕБНАЯ ЗАДАЧА. НЕ ЗВОНИТЬ, НЕ ПИСАТЬ, НЕ СОЗДАВАТЬ КП КЛИЕНТУ.\n"
        "Проверить учебную карточку DEMO-ITREQ-001; оценка 86 является учебной, "
        "реальной потребности и ЛПР нет.\n"
        "Пример для менеджера: уточнить при настоящем запросе количество точек, "
        "состав 1С/ККТ, сроки и бюджет. Фиксировать решения только по согласованной процедуре.\n"
        "Объект CRM: https://serviseit.bitrix24.ru/crm/type/1062/details/3/"
    )
    return {
        "status": "OFFLINE_LINKED_MANAGER_TASK_PREVIEW",
        "portal": "serviseit.bitrix24.ru",
        "method_if_separately_authorized": "tasks.task.add",
        "params_not_sent": {"fields": {
            "TITLE": title, "RESPONSIBLE_ID": uid,
            "DESCRIPTION": description, "UF_CRM_TASK": [f"{symbol}_{ITEM_ID}"],
        }},
        "manager_identity_independently_confirmed": False,
        "crm_item_id": ITEM_ID, "synthetic": True,
        "task_created": False, "crm_writes": "NO", "outbound_communications": "NO",
        "requires_explicit_manager_identity_and_write_approval": True,
    }
