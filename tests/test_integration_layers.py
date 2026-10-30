"""Deterministic tests, no outbound network and no live CRM/AI writes."""
import httpx
import json
import pytest

from leadpilot.core import initialize, get_leads, qualify
from leadpilot.sources import scan, import_scan, staged_candidates, parse_vacancy
from leadpilot.ai import AIProvider, AIConfig
from leadpilot.bitrix_readonly import readonly_preflight


def vacancy(inn='7701234567', title='Программист 1С', url='https://trudvsem.ru/vacancy/example'):
    return {"vacancy": {"job-name": title, "creation-date": "2026-10-30",
            "vac_url": url, "company": {"name": "Учебная организация", "inn": inn}}}


def mock_client(routes):
    def handler(req):
        return routes(req)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_trudvsem_known_inn_and_real_source_url_preserved(tmp_path):
    def fake(req):
        assert req.url.host == 'opendata.trudvsem.ru'
        assert req.url.params['text'] == '1С'
        return httpx.Response(200, json={"status": "200", "results": {"vacancies": [vacancy()]}})
    with mock_client(fake) as client:
        res = scan(client=client)
    assert res['valid_signals'] == 1
    assert res['signals'][0]['inn'] == '7701234567'
    assert res['signals'][0]['source_url'] == 'https://trudvsem.ru/vacancy/example'
    assert res['signals'][0]['phone'] == '' and res['signals'][0]['email'] == ''
    db=tmp_path/'sample.sqlite3'
    assert import_scan(db, res)['accepted'] == 1
    assert import_scan(db, res)['accepted'] == 1
    assert len(get_leads(db)) == 1
    assert len(get_leads(db)[0]['signals']) == 1


def test_trudvsem_without_inn_staged_not_fabricated(tmp_path):
    def fake(req):
        return httpx.Response(200, json={"status": "200", "results": {"vacancies": [vacancy(inn='')]}})
    with mock_client(fake) as client:
        res = scan(client=client)
    db=tmp_path/'sample.sqlite3'
    counts=import_scan(db,res)
    assert counts['accepted'] == 0 and counts['staged_needs_inn'] == 1
    assert len(staged_candidates(db)) == 1
    assert not get_leads(db)


def test_trudvsem_rejects_bad_source_url():
    assert parse_vacancy(vacancy(url='https://evil.example/vacancy'), '77') is None
    assert parse_vacancy(vacancy(url='http://trudvsem.ru/vacancy'), '77')['source_url'] == 'http://trudvsem.ru/vacancy' 


def test_trudvsem_rejects_outside_titles_and_incorrect_response():
    def fake(req):
        return httpx.Response(200, json={"status":"200", "results":{"vacancies":[vacancy(title='Курьер')]}})
    with mock_client(fake) as client:
        assert scan(client=client)['valid_signals'] == 0
    with mock_client(lambda _: httpx.Response(403)) as client:
        with pytest.raises(RuntimeError, match='unavailable'):
            scan(client=client)


def test_source_allowlist():
    with pytest.raises(ValueError):scan(region='999')
    with pytest.raises(ValueError):scan(term='тест; drop')
    with pytest.raises(ValueError):scan(limit=200)


def test_bitrix_preflight_read_only():
    methods = []
    def fake(req):
        methods.append(req.url.path.split('/')[-1])
        assert req.method == 'POST'
        return httpx.Response(200,json={"result": {}})
    with mock_client(fake) as client:
        res=readonly_preflight('https://demo.bitrix24.ru/rest/123/FAKESECRET',client)
    assert res['crm_writes'] is False
    assert methods == ['crm.settings.mode.get', 'crm.item.fields']
    assert all(x=='READ_OK' for x in res['methods'].values())


def test_bitrix_host_validation():
    with pytest.raises(ValueError):readonly_preflight('https://evil.example/rest/4/abc')
    with pytest.raises(ValueError):readonly_preflight('http://demo.bitrix24.ru/rest/4/abc')


def test_gigachat_adapter_oauth_and_response():
    called = []
    def fake(req):
        called.append(req.url.path)
        if req.url.path.endswith('/oauth'):
            assert req.headers['Authorization'].startswith('Basic ')
            return httpx.Response(200,json={'access_token':'FAKE_TOKEN'})
        assert req.headers['Authorization'] == 'Bearer FAKE_TOKEN'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({
            'service':'1С','possible_need':'Возможно нужна поддержка 1С',
            'confidence':0.75,'evidence_quote':'вакансия 1С'},ensure_ascii=False)}}]})
    with mock_client(fake) as client:
        provider=AIProvider(AIConfig(provider='gigachat',model='GigaChat-2',credentials='FAKE_KEY'), client)
        res=provider.classify('публичная вакансия 1С')
    assert called == ['/api/v2/oauth', '/v1/chat/completions']
    assert res['origin']=='AI_hypothesis'
    assert res['confidence']==0.75
    assert res['evidence_quote']=='вакансия 1С'


def test_gigachat_does_not_invent_quotes():
    answer={'service':'1С','possible_need':'вероятная потребность','confidence':.5,'evidence_quote':'ВЫДУМАНО'}
    with mock_client(lambda _:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer,ensure_ascii=False)}}]})) as client:
        p=AIProvider(AIConfig(provider='openai_compatible',model='test',url='https://demo.example/v1/chat/completions',key='test'),client)
        res=p.classify('Только проверяемый сигнал')
    assert res['evidence_quote']==''
