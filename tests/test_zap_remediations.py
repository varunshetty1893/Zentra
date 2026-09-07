import pytest

from app import create_app, db


@pytest.fixture
def client():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        with app.test_client() as test_client:
            yield test_client
        db.session.remove()
        db.drop_all()


def test_security_headers_are_applied(client):
    response = client.get("/")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_jobs_does_not_reflect_invalid_filter_values(client):
    response = client.get(
        "/jobs?salary_min=ZAP&salary_max=jobs&experience=%3Csvg%20onload%3Dalert(1)%3E"
    )

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'value="ZAP"' not in html
    assert "onload=alert(1)" not in html
