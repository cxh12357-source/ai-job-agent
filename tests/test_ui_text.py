from ai_job_agent.ui_text import markdown_literal


def test_remote_markdown_resources_and_html_are_literal_text():
    text = markdown_literal('![tracker](https://example.com/image) <img src="x">')
    assert text.startswith(r'\!\[tracker\]\(https://example\.com/image\)')
    assert r'\<img src="x"\>' in text


def test_job_names_keep_plain_language_and_cannot_inject_headings():
    assert markdown_literal("工程师\n# 标题") == "工程师 \\# 标题"
