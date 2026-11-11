"""Offline no-network V43 one-field INN write guards."""
import hashlib
import secrets
from unittest.mock import MagicMock

import pytest

from ops.serviceit_vps_b2b_v43_inn_field import (
    SOURCE, INN_FIELD, company_class, passport, digest_of, signed_evidence,
)
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_b2b_v20_versioned import compute_digest


@pytest.mark.parametrize("card,inn", [(19,"2310031475"),
                                     (21,"7715995942"),
                                     (23,"0411075740")])
def test_exact_signed_id_inn_pairs(card,inn):
    assert SOURCE[card][2]==inn
    from leadpilot.bitrix_1062_mapping import _is_valid_inn
    assert _is_valid_inn(inn)


def test_company_name_is_not_silently_overwritten_by_inn_helper():
    assert company_class("ПАО «Не тот покупатель»",SOURCE[19][1])=="DIFFERENT_LEGAL_ENTITY"
    assert company_class(SOURCE[19][1],SOURCE[19][1])=="MATCH"
    assert company_class("B2B-Center",SOURCE[19][1])=="PLACEHOLDER"
    assert company_class("ООО УК «ПМХ»",SOURCE[23][1])=="PUBLIC_VERIFIED_ALIAS"
    assert company_class("ООО «УК ПМХ»",SOURCE[23][1])=="PUBLIC_VERIFIED_ALIAS"
    assert company_class("ООО УК «ПМХ»",SOURCE[19][1])=="DIFFERENT_LEGAL_ENTITY"
    assert company_class("заказчик не установлен",SOURCE[19][1])=="PLACEHOLDER"
    assert company_class("",SOURCE[19][1])=="EMPTY"


def test_passport_excludes_unrelated_addenda_and_sidebars():
    content=(
        "Досье\n01  ПАСПОРТ ЗАКУПКИ\n"
        "Заказчик: АО «Тандер»\nИНН: 2310031475\n"
        "02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ\n"
        "Ссылка на третью компанию, ИНН: 7700000000")
    assert "2310031475" in passport(content)
    assert "7700000000" not in passport(content)


def test_proof_rejects_unverified_or_tampered_v20_receipt():
    card,source=19,SOURCE[19]
    tid,buyer,inn,url,legal_url=source
    digest=hashlib.sha256(("B2B-CENTER-"+tid).encode()).hexdigest()
    fields={k:"Ещё не подтверждено" for k in AI_FIELDS}
    fields[PREFIX+"AI_FULL_REPORT"]=(
        "01  ПАСПОРТ ЗАКУПКИ\nЗаказчик: "+buyer+
        "\nИНН: "+inn+"\n02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ\n"
        "Источник: "+url+"\nЮридическое лицо: "+legal_url)
    salt=secrets.token_bytes(16)
    signature=compute_digest(fields,salt)
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value=(
        {"source":"B2B-Center","tender_id":tid},)
    conn.execute.return_value.fetchall.return_value=[(fields,salt,signature)]
    notice,src,h=signed_evidence(conn,card,tid,buyer,inn,url,legal_url)
    assert src=="B2B-CENTER-"+tid and h==digest
    conn.execute.return_value.fetchall.return_value=[(fields,salt,b"x"*32)]
    with pytest.raises(ValueError,match="NO_ATTESTED_V20_BUYER_INN"):
        signed_evidence(conn,card,tid,buyer,inn,url,legal_url)


def test_durable_intent_digest_is_exact_one_inn_field():
    salt=b"z"*16
    patch={INN_FIELD:"2310031475"}
    value=digest_of(patch,salt)
    assert len(value)==32
    assert value != digest_of({INN_FIELD:"7715995942"},salt)
    assert value != digest_of({PREFIX+"AI_STATUS":"SOMETHING"},salt)


def test_unrelated_company_inn_source_cannot_pass_validated_receipt():
    card,source=19,SOURCE[19]
    tid,buyer,inn,url,legal_url=source
    fields={k:"получено" for k in AI_FIELDS}
    fields[PREFIX+"AI_FULL_REPORT"]=(
        "01  ПАСПОРТ ЗАКУПКИ\nЗаказчик: Другая компания\n"
        "ИНН: "+inn+"\n02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ\n"
        +url+"\n"+legal_url)
    salt=b"u"*16
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value=({"source":"B2B-Center"},)
    conn.execute.return_value.fetchall.return_value=[
        (fields,salt,compute_digest(fields,salt))]
    with pytest.raises(ValueError,match="NO_ATTESTED_V20_BUYER_INN"):
        signed_evidence(
            conn,card,tid,buyer,inn,url,legal_url)
