from job_assistant.salary_parser import parse_salary


def test_parses_cny_monthly_k_range():
    parsed = parse_salary("薪资范围：20k-30k / 月，另有年终奖")
    assert parsed is not None
    assert (parsed.minimum, parsed.maximum) == (20_000, 30_000)
    assert parsed.currency == "CNY"
    assert parsed.period == "month"


def test_parses_usd_annual_range():
    parsed = parse_salary("The annual salary range is USD 120,000 - 160,000 per year.")
    assert parsed is not None
    assert (parsed.minimum, parsed.maximum) == (120_000, 160_000)
    assert parsed.currency == "USD"
    assert parsed.period == "year"


def test_does_not_treat_experience_or_single_budget_as_salary():
    assert parse_salary("Requires 3-5 years experience. Learning budget USD 2,000 per year.") is None


def test_does_not_treat_internship_days_or_months_as_salary_when_pay_appears_later():
    text = (
        "工作时间每周可出勤 4~5 天，能稳定实习 3~6 个月，沟通能力良好。"
        "工作地点北京，薪资待遇：时薪 27.70 CNY。"
    )

    assert parse_salary(text) is None
