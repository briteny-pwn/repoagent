import json
from app.agent import parse_result
from app.main import public_task


def test_truncated_draft_preserves_complete_markdown_report_without_drafts():
    content='{"report":"## Result\\n\\n- A","drafts":[{"kind":"issue","body":"unfinished'
    report,drafts=parse_result(content,set(),{'search_issues'})
    assert report['report']=='## Result\n\n- A'
    assert drafts==[]
    assert any('不完整' in value for value in report['limitations'])


def test_truncated_report_is_not_rendered_as_raw_json():
    report,drafts=parse_result('{"report":"unfinished',set(),set())
    assert not report['report'].startswith('{')
    assert drafts==[]


def test_legacy_fallback_report_normalized_without_mutating_storage():
    raw='{"report":"## Result\\n- A","drafts":['
    task={'id':'one','result':{'report':raw,'evidence':[],'limitations':['模型未返回结构化结果；未生成任何可发布草稿。']}}
    output=public_task(task)
    assert output['result']['report']=='## Result\n- A'
    assert task['result']['report']==raw


def test_plain_markdown_and_json_examples_preserved():
    text='## Example\n\n```json\n{"report":"example"}\n```'
    assert parse_result(text,set(),set())[0]['report']==text
    valid=json.dumps({'report':text,'drafts':[]})
    assert parse_result(valid,set(),set())[0]['report']==text
