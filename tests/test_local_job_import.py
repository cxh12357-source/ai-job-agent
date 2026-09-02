import json

import pytest

from ai_job_agent.services.local_job_import import (
    MAX_EXPORT_BYTES,
    LocalJobImportError,
    local_job_export_template,
    parse_local_job_export,
)


def export(*jobs, **overrides):
    body = {
        "schema_version": 1,
        "kind": "charles-job-export",
        "source": "local-playwright",
        "exported_at": "2026-09-03T10:20:00+08:00",
        "jobs": list(jobs),
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def job(**changes):
    record = {
        "title": "Python 实习生",
        "company": "Example company",
        "location": None,
        "description": "<p>使用 Python 开发工具。</p><script>alert('x')</script><p>欢迎应届生。</p>",
        "job_url": "https://careers.example.com/jobs/123?jobId=123#apply",
        "requirements": ["<b>Python</b>", "团队协作"],
    }
    record.update(changes)
    return record


def test_valid_export_html_null_location_and_stable_identifier():
    result = parse_local_job_export("jobs.JSON", export(job()))
    parsed = result.jobs[0]
    assert parsed.description == "使用 Python 开发工具。\n欢迎应届生。"
    assert parsed.requirements == ["Python", "团队协作"]
    assert parsed.location is None
    assert parsed.source == "local-playwright"
    assert parsed.job_url == "https://careers.example.com/jobs/123?jobId=123"
    assert parsed.id == parsed.job_id
    assert parsed.job_id.startswith("local:")
    assert result.exported_at == "2026-09-03T10:20:00+08:00"
    assert not result.warnings
    assert parse_local_job_export("j.json", export(job())).jobs[0].job_id == parsed.job_id


def test_no_network_is_used(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("must not resolve DNS"))
    assert len(parse_local_job_export("jobs.json", export(job())).jobs) == 1


@pytest.mark.parametrize("filename,content,match", [
    ("jobs.py", b"{}", "只支持"),
    ("jobs.json", b"", "为空"),
    ("jobs.json", b" " * (MAX_EXPORT_BYTES + 1), "5 MB"),
    ("jobs.json", b"not json", "UTF-8 JSON"),
    ("jobs.json", b"\xff", "UTF-8 JSON"),
    ("jobs.json", b"[]", "版本信息"),
    ("jobs.json", b'{"x":NaN}', "NaN"),
    ("jobs.json", b'{"x":1,"x":2}', "重复字段"),
], ids=["extension", "empty", "size-limit", "syntax", "encoding", "array", "nan", "duplicate-keys"])
def test_rejects_bad_file(filename, content, match):
    with pytest.raises(LocalJobImportError, match=match):
        parse_local_job_export(filename, content)


@pytest.mark.parametrize("overrides", [
    {"schema_version": 2}, {"schema_version": True},
    {"kind": "candidate-profile"}, {"source": "unknown"},
    {"exported_at": "yesterday"}, {"exported_at": 123},
    {"jobs": []}, {"jobs": {}}, {"jobs": [job()] * 101},
])
def test_rejects_bad_envelope(overrides):
    with pytest.raises(LocalJobImportError):
        parse_local_job_export("jobs.json", export(job(), **overrides))


@pytest.mark.parametrize("field", ["cookies", "storage_state", "candidate_profile", "OPENAI_API_KEY"])
def test_rejects_accidental_private_profile_exports_without_echoing_data(field):
    with pytest.raises(LocalJobImportError) as error:
        parse_local_job_export("jobs.json", export(job(), **{field: "private-test-value"}))
    assert "private-test-value" not in str(error.value)


@pytest.mark.parametrize("url", [
    "", "file:///C:/private/resume.pdf", "javascript:alert(1)", "data:text/html,abc",
    "https://name:private-password@example.com/jobs/1", "http://localhost/jobs/1",
    "http://localhost./", "http://test.localhost/jobs", "http://corp.internal/",
    "http://intranet/", "http://192.168.1.1/", "http://10.1.1.1/", "http://127.0.0.1/",
    "http://169.254.169.254/", "http://[::1]/", "http://[fd00::1]/", "http://[fe80::1]/",
    "http://[::ffff:127.0.0.1]/", "http://[fe80::1%25eth0]/", "http://2130706433/",
    "http://[ff0e::1]/", "http://224.0.0.1/",
    "http://127.1/", "http://0x7f000001/", "http://0x7f.0.0.1/", "http://0177.0.0.1/",
    "http://127.0.0.1.nip.io@127.0.0.1/", "https://example.com:bad/jobs",
    "https://example.com\\@localhost/", "https://exam\nple.com/jobs",
    "https://%6cocalhost/jobs", "https://jobs.example.com/?access_token=private-test-value",
    "https://jobs.example.com/?apiKey=private-test-value", "https://jobs.example.com/?session_id=private-test-value",
    "https://jobs.example.com/?co%64e=private-test-value", "https://jobs.example.com/?X-Amz-Signature=private-test-value",
])
def test_rejects_unsafe_or_credential_urls_without_echoing_them(url):
    with pytest.raises(LocalJobImportError) as error:
        parse_local_job_export("jobs.json", export(job(job_url=url)))
    assert "private-test-value" not in str(error.value)
    assert "private-password" not in str(error.value)


def test_mixed_records_skipped_and_duplicates_canonicalized():
    result = parse_local_job_export("jobs.json", export(
        job(job_url="HTTPS://CAREERS.EXAMPLE.COM:443/jobs/123?jobId=123#first"),
        job(job_url="https://careers.example.com/jobs/123?jobId=123#second"),
        job(job_url="http://localhost/secret"),
        job(job_url="https://careers.example.com/jobs/123?jobId=124", location="北京"),
    ))
    assert len(result.jobs) == 2
    assert len(result.warnings) == 2
    assert any("重复" in warning for warning in result.warnings)
    assert result.jobs[1].location == "北京"
    assert result.jobs[0].job_id != result.jobs[1].job_id


@pytest.mark.parametrize("field,value", [
    ("title", ""), ("title", "a" * 301), ("company", ""), ("company", 12),
    ("description", ""), ("description", "<script>only script</script>"),
    ("description", "a" * 60_001), ("location", []), ("requirements", [123]),
    ("requirements", ["a"] * 101), ("requirements", ["a" * 20_001]),
], ids=["empty-title", "long-title", "empty-company", "numeric-company", "empty-jd", "script-jd", "long-jd", "bad-location", "numeric-requirement", "many-requirements", "long-requirement"])
def test_rejects_invalid_or_unbounded_fields(field, value):
    with pytest.raises(LocalJobImportError):
        parse_local_job_export("jobs.json", export(job(**{field: value})))


def test_unknown_sensitive_extras_and_supplied_ids_are_discarded():
    result = parse_local_job_export("jobs.json", export(job(
        id="malicious-other-source", job_id="different-job", source="greenhouse",
        cookies=[{"private": "private-test-value"}], profile={"email": "private@example.com"},
    ), arbitrary_metadata={"private": "private-test-value"}))
    assert result.jobs[0].job_id.startswith("local:")
    assert result.jobs[0].source == "local-playwright"
    assert "private" not in result.jobs[0].model_dump_json()
    assert len(result.warnings) == 2
    assert all("private-test-value" not in message for message in result.warnings)


def test_invalid_record_type_skips_transparently():
    result = parse_local_job_export("jobs.json", export(None, job()))
    assert len(result.jobs) == 1
    assert "第 1 条" in result.warnings[0]


def test_template_round_trip_utf8_bom_optional_date_and_nonpersonal_data():
    content = local_job_export_template().encode("utf-8-sig")
    result = parse_local_job_export("template.json", content)
    assert len(result.jobs) == 1
    assert result.exported_at is None
    assert result.jobs[0].location is None
    assert "示例" in result.jobs[0].title
    assert result.jobs[0].job_url.startswith("https://example.com/")


def test_blank_unknown_location_stays_unknown_and_public_ipv6_is_allowed():
    result = parse_local_job_export("jobs.json", export(job(location="  ", job_url="https://[2606:4700:4700::1111]:443/jobs/1")))
    assert result.jobs[0].location is None
    assert result.jobs[0].job_url == "https://[2606:4700:4700::1111]/jobs/1"


def test_exporter_warnings_are_counted_but_never_displayed_or_retained():
    result = parse_local_job_export("jobs.json", export(job(), warnings=[
        "https://example.com/?token=private-test-value",
        "A selector was not found.",
    ]))
    assert len(result.warnings) == 1
    assert result.warnings[0] == "本机抓取器报告 2 条提示，请在本机查看原因。"
    assert "private-test-value" not in repr(result)


@pytest.mark.parametrize("warnings", [None, "log", {}, [1], ["log"] * 31, ["a" * 401]],
                         ids=["null", "string", "object", "number-item", "too-many", "too-long"])
def test_exporter_warnings_must_be_bounded_strings(warnings):
    with pytest.raises(LocalJobImportError, match="warnings 必须"):
        parse_local_job_export("jobs.json", export(job(), warnings=warnings))


def test_standalone_exporter_payload_imports_without_metadata_warning():
    from local_scraper import export_payload

    payload = export_payload([job()], [])
    result = parse_local_job_export("jobs.json", json.dumps(payload).encode("utf-8"))
    assert len(result.jobs) == 1
    assert not result.warnings
