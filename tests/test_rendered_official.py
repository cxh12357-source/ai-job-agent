from __future__ import annotations

from ai_job_agent.sources.rendered_official import jobs_from_rendered_links


def test_rendered_links_keep_only_concrete_same_origin_jobs() -> None:
    jobs = jobs_from_rendered_links(
        "https://careers.example.com/jobs",
        [
            {
                "href": "https://careers.example.com/position/123",
                "text": "AI Agent Engineer",
                "nearby_text": "AI Agent Engineer Shanghai Python",
                "visible": True,
            },
            {
                "href": "https://careers.example.com/jobs",
                "text": "全部职位",
                "visible": True,
            },
            {
                "href": "https://other.example.org/position/999",
                "text": "External Engineer",
                "visible": True,
            },
            {
                "href": "https://careers.example.com/position/hidden",
                "text": "Hidden Engineer",
                "visible": False,
            },
        ],
        company="Example",
    )

    assert len(jobs) == 1
    assert jobs[0].title == "AI Agent Engineer"
    assert jobs[0].company == "Example"
    assert jobs[0].description == "AI Agent Engineer Shanghai Python"
    assert jobs[0].source == "Official website:careers.example.com"


def test_rendered_links_are_bounded_and_ids_are_stable() -> None:
    links = [
        {
            "href": f"https://careers.example.com/jobs/{index}",
            "text": f"Software Engineer {index}",
            "visible": True,
        }
        for index in range(10)
    ]
    first = jobs_from_rendered_links(
        "https://careers.example.com/jobs", links, company="Example", max_jobs=3
    )
    second = jobs_from_rendered_links(
        "https://careers.example.com/jobs", links, company="Example", max_jobs=3
    )

    assert len(first) == 3
    assert [job.job_id for job in first] == [job.job_id for job in second]


def test_rendered_links_recognise_opaque_early_career_routes() -> None:
    jobs = jobs_from_rendered_links(
        "https://campus.example.com/campus",
        [
            {
                "href": "https://campus.example.com/detail/opaque-123",
                "text": "2027届应届毕业生算法工程师",
                "nearby_text": "校园招聘 北京",
                "visible": True,
            },
            {
                "href": "https://campus.example.com/campus/home",
                "text": "校园招聘",
                "visible": True,
            },
        ],
        company="Example",
    )

    assert [item.title for item in jobs] == ["2027届应届毕业生算法工程师"]
