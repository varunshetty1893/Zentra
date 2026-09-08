import pytest
from app import create_app, db
from app.models.user import User
from app.models.job import Job
from app.models.resume import Resume
from app.models.application import Application

# This is an integration check against real seeded data (from
# seed_microsoft.py), not a unit test — it needs create_app()'s
# *real* DATABASE_URL (dev/staging Postgres), not the isolated
# in-memory SQLite the rest of the suite uses. Excluded from the
# default `pytest` run (see pyproject.toml addopts) so CI/local runs
# never hang waiting on a database that may not be reachable. Run it
# explicitly after seeding with: pytest -m requires_seed_data
@pytest.mark.requires_seed_data
def test_microsoft_seed():
    app = create_app()
    with app.app_context():
        # Check Recruiter
        recruiter = User.query.filter_by(email="recruiter@microsoft.com").first()
        assert recruiter is not None
        assert recruiter.role == User.ROLE_RECRUITER
        assert recruiter.check_password("Microsoft@2026")
        assert recruiter.recruiter_profile.company_name == "Microsoft"
        assert recruiter.recruiter_profile.approval_status == "approved"

        # Check Jobs count (at least 10, seeded 12)
        jobs = Job.query.filter_by(recruiter_profile_id=recruiter.recruiter_profile.id).all()
        assert len(jobs) >= 10
        for j in jobs:
            assert j.status == Job.STATUS_ACTIVE
            assert len(j.description) > 50
            assert len(j.requirements) > 30
            assert len(j.required_skills_list) > 0

        # Check Candidate
        cand = User.query.filter_by(email="alex.chen@example.com").first()
        assert cand is not None
        assert cand.role == User.ROLE_CANDIDATE
        assert cand.check_password("Candidate@123")
        assert cand.headline is not None
        assert len(cand.career_entries) >= 4

        # Check Candidate Resume
        primary_resume = Resume.get_primary(cand.id)
        assert primary_resume is not None
        assert primary_resume.is_primary is True
        assert primary_resume.last_ats_score > 50.0

        # Check Applications to Microsoft
        apps = Application.query.filter_by(candidate_id=cand.id).all()
        assert len(apps) >= 2
        for a in apps:
            assert a.job.recruiter_profile.company_name == "Microsoft"
            assert a.status in ("applied", "interview", "shortlisted", "hired", "rejected")
            assert a.match_score is not None and a.match_score > 50.0
