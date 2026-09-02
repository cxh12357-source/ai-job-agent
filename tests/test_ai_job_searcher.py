from types import SimpleNamespace

from ai_job_agent.demo_data import demo_profile
from ai_job_agent.services.job_searcher import search_jobs, search_jobs_detailed
from job_assistant.models import Job


def test_demo_job_search_is_offline_and_returns_complete_jobs():
    jobs = search_jobs(demo_profile(), demo_mode=True)

    assert len(jobs) >= 3
    assert all(job.title and job.company and job.job_url for job in jobs)
    assert any(job.source == "demo" for job in jobs)


def test_demo_search_reports_that_only_local_samples_were_queried():
    result = search_jobs_detailed(demo_profile(), demo_mode=True)

    assert result.mode == "demo"
    assert result.boards_queried == ("local-demo",)
    assert result.total_jobs_seen == len(result.jobs) == 4


def test_broad_nationwide_search_disables_literal_title_and_city_gates(monkeypatch):
    captured = {}

    class FakeDiscovery:
        def discover(self, query, *, board_tokens):
            captured["query"] = query
            captured["tokens"] = board_tokens
            job = Job(
                id="1",
                title="Graduate Engineer",
                company="Official Company",
                location="Beijing, China",
                description="Python and data analysis",
                url="https://job-boards.greenhouse.io/rzr/jobs/1",
                source="Greenhouse:rzr",
            )
            return SimpleNamespace(
                jobs=(job,),
                boards_queried=("rzr",),
                boards_succeeded=("rzr",),
                failures=(),
                cache_hits=(),
                total_jobs_seen=1,
            )

    monkeypatch.setattr("job_assistant.discovery.GreenhouseDiscovery", FakeDiscovery)
    result = search_jobs_detailed(
        demo_profile(),
        demo_mode=False,
        greenhouse_tokens=("rzr",),
        broad_search=True,
        nationwide=True,
    )

    assert captured["query"].target_titles == ()
    assert captured["query"].locations == ()
    assert captured["tokens"] == ("rzr",)
    assert result.mode == "official"
    assert result.jobs[0].company == "Official Company"
