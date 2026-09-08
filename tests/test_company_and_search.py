import pytest
from app import create_app, db
from app.models.job import Job
from app.models.recruiter_profile import RecruiterProfile
from app.models.user import User

# Same situation as test_seed_microsoft.py: create_app() here loads the
# real DATABASE_URL and depends on seed_microsoft.py having already run
# against it (12 real "Microsoft" jobs). Marked requires_seed_data and
# excluded from the default run for the same reason — see that file's
# comment and pyproject.toml addopts.
pytestmark = pytest.mark.requires_seed_data


@pytest.fixture
def test_client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with app.app_context():
            yield client


def test_job_search_includes_company_and_returns_all_12_jobs(test_client):
    """Verifies that searching for 'microsoft' finds all 12 jobs posted by Microsoft."""
    response = test_client.get("/jobs?q=microsoft")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Should report 12 jobs found
    assert "12</b> jobs found" in html or "12</b> job found" in html

    # Verify company name link is present in cards
    assert "/companies/" in html
    assert "Microsoft" in html


def test_company_detail_page(test_client):
    """Verifies that /companies/<id> renders company information and lists all its jobs."""
    with test_client.application.app_context():
        ms_profile = RecruiterProfile.query.filter_by(company_name="Microsoft", approval_status="approved").first()
        assert ms_profile is not None
        ms_id = ms_profile.id

    response = test_client.get(f"/companies/{ms_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Header and stats
    assert "Microsoft" in html
    assert "Verified Employer" in html
    assert "Active Openings" in html
    assert "12 Roles Available" in html

    # Some job titles
    assert "Principal Cloud Solution Architect" in html
    assert "Site Reliability Engineering Lead" in html
    assert "Senior Applied AI Scientist" in html


def test_company_detail_404_for_invalid_id(test_client):
    """Verifies that a non-existent company ID returns 404."""
    response = test_client.get("/companies/999999")
    assert response.status_code == 404
