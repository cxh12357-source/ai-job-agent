from __future__ import annotations

from .models import CandidateProfile, Education, Experience, JobPosting, Project


def demo_profile() -> CandidateProfile:
    return CandidateProfile(
        name="Demo Candidate",
        phone="13800000000",
        email="demo.candidate@example.com",
        location="Shanghai, China",
        education=[
            Education(
                school="Example University",
                degree="Bachelor",
                major="Mechanical Engineering",
                graduation_date="2027-06",
            )
        ],
        skills=[
            "Python",
            "Excel",
            "Data Analysis",
            "LLM",
            "AI Agent",
            "SolidWorks",
            "AutoCAD",
            "Thermal Management",
        ],
        languages=[],
        internships=[
            Experience(
                company="Example Manufacturing",
                title="Data & Operations Intern",
                description="Cleaned operational data and automated weekly reporting.",
                achievements=["Improved reporting efficiency with Python and Excel."],
                skills=["Python", "Excel", "Data Analysis"],
            )
        ],
        projects=[
            Project(
                name="Engineering AI Assistant",
                description="Built a source-grounded workflow for engineering documents.",
                technologies=["Python", "LLM", "AI Agent"],
            )
        ],
        target_roles=[
            "AI Application Intern",
            "Data Analyst Intern",
            "Manufacturing Engineer Intern",
        ],
        target_locations=["Shanghai", "Suzhou", "Shenzhen"],
        career_stage="fresh_graduate",
        parse_method="demo",
    )


def demo_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            job_id="demo-ai-001",
            title="Industrial AI Application Intern",
            company="Demo Smart Industry",
            location="Shanghai",
            job_type="Internship",
            department="Digital Industries",
            description=(
                "Build LLM workflows and AI agents for manufacturing scenarios. "
                "Use Python to clean data, evaluate outputs and automate reporting."
            ),
            requirements="Current engineering student; Python, data analysis and communication.",
            required_skills=["Python", "Data Analysis", "LLM", "AI Agent"],
            preferred_skills=["Automation", "Excel"],
            job_url="http://127.0.0.1:8766/jobs/demo-ai-001",
            source="demo",
            publish_date="2026-09-01",
        ),
        JobPosting(
            job_id="demo-thermal-002",
            title="Thermal Management Engineering Intern",
            company="Demo Mobility",
            location="Suzhou",
            job_type="Internship",
            department="Engineering",
            description=(
                "Support heat-transfer analysis, mechanical design, prototype testing "
                "and manufacturing documentation for electric mobility products."
            ),
            requirements="Mechanical engineering student; CAD and thermal fundamentals.",
            required_skills=["Thermal Management", "SolidWorks", "AutoCAD"],
            preferred_skills=["Data Analysis", "Python"],
            job_url="http://127.0.0.1:8766/jobs/demo-thermal-002",
            source="demo",
            publish_date="2026-09-01",
        ),
        JobPosting(
            job_id="demo-ops-003",
            title="Sales Operations Data Intern",
            company="Demo Infrastructure",
            location="Shanghai",
            job_type="Internship",
            department="Sales Operations",
            description=(
                "Maintain CRM data quality, analyze funnel data, prepare dashboards "
                "and support customer research."
            ),
            requirements="Excel, data analysis, clear communication and attention to detail.",
            required_skills=["Excel", "Data Analysis"],
            preferred_skills=["Python", "Customer Communication"],
            job_url="http://127.0.0.1:8766/jobs/demo-ops-003",
            source="demo",
            publish_date="2026-09-01",
        ),
        JobPosting(
            job_id="demo-senior-004",
            title="Senior Embedded Systems Architect",
            company="Demo Automation",
            location="Beijing",
            job_type="Full-time",
            department="R&D",
            description="Lead embedded C++ architecture and a large engineering team.",
            requirements="10+ years of embedded systems leadership and C++ architecture.",
            required_skills=["C++", "Embedded Systems", "Leadership"],
            job_url="http://127.0.0.1:8766/jobs/demo-senior-004",
            source="demo",
            publish_date="2026-09-01",
        ),
    ]


__all__ = ["demo_jobs", "demo_profile"]
