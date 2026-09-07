"""Strict Candidate / User-Side Security and Business Logic Test Suite for Zentra.

Tests all 20 direct tampering and security scenarios:
1. Candidate changes another user's resume ID
2. Candidate changes another user's application ID
3. Candidate accesses another user's notification
4. Candidate applies to a closed job
5. Candidate applies to a paused job
6. Candidate applies to a draft job
7. Candidate submits inactive target_job_id to ATS analysis
8. Candidate submits malformed target_job_id
9. Candidate accesses disabled public profile
10. Candidate accesses private resume
11. Candidate manipulates public resume access
12. Candidate privacy filters enforce recruiter_discoverable=False
13. Candidate submits excessively large ATS input (>50,000 chars)
14. Candidate AI rate limiting & validation
15. Disabled candidate uses an existing authenticated session
16. Candidate attempts unauthorized direct URLs (/admin/, /recruiter/)
17. Candidate attempts unauthorized POST requests to admin routes
18. Candidate attempts duplicate application
19. Candidate manipulates saved-job IDs
20. Notification isolation per candidate
"""

import unittest
from app import create_app, db
from app.models.user import User
from app.models.job import Job
from app.models.resume import Resume
from app.models.application import Application
from app.models.saved_job import SavedJob
from app.models.notification import Notification
from app.models.recruiter_profile import RecruiterProfile


class TestCandidateSecurity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app("testing")
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()

        # 1. Create Users
        cls.alice = User(
            full_name="Alice Candidate",
            email="alice@example.com",
            role=User.ROLE_CANDIDATE,
            public_slug="alice-slug",
            public_profile_enabled=True,
            public_resume_enabled=True,
            recruiter_discoverable=True,
            is_active_account=True,
            # avatar_filename must be set so the settings route's "photo required"
            # gate doesn't block test_22 before it reaches field-validation logic.
            avatar_filename="test_avatar_alice.jpg",
        )
        cls.alice.set_password("Alice@1234")
        db.session.add(cls.alice)

        cls.bob = User(
            full_name="Bob Candidate",
            email="bob@example.com",
            role=User.ROLE_CANDIDATE,
            public_slug="bob-slug",
            public_profile_enabled=False,
            public_resume_enabled=False,
            recruiter_discoverable=False,
            is_active_account=True,
        )
        cls.bob.set_password("Bob@1234")
        db.session.add(cls.bob)

        cls.carol = User(
            full_name="Carol Recruiter",
            email="carol@example.com",
            role=User.ROLE_RECRUITER,
            is_active_account=True,
        )
        cls.carol.set_password("Carol@1234")
        db.session.add(cls.carol)

        cls.admin = User(
            full_name="System Admin",
            email="admin@example.com",
            role=User.ROLE_ADMIN,
            is_active_account=True,
        )
        cls.admin.set_password("Admin@1234")
        db.session.add(cls.admin)
        db.session.flush()

        # 2. Create Recruiter Profile
        cls.recruiter_profile = RecruiterProfile(
            user_id=cls.carol.id,
            company_name="InnovateCorp",
            approval_status="approved",
        )
        db.session.add(cls.recruiter_profile)
        db.session.flush()

        # 3. Create Jobs
        cls.active_job = Job(
            recruiter_profile_id=cls.recruiter_profile.id,
            title="Senior Backend Engineer",
            description="Python FastAPI and PostgreSQL development",
            requirements="Python, SQL, Docker",
            status=Job.STATUS_ACTIVE,
        )
        cls.paused_job = Job(
            recruiter_profile_id=cls.recruiter_profile.id,
            title="Paused Role",
            description="Temporarily paused hiring",
            status=Job.STATUS_PAUSED,
        )
        cls.closed_job = Job(
            recruiter_profile_id=cls.recruiter_profile.id,
            title="Closed Role",
            description="No longer hiring",
            status=Job.STATUS_CLOSED,
        )
        cls.draft_job = Job(
            recruiter_profile_id=cls.recruiter_profile.id,
            title="Draft Role",
            description="Unpublished draft role",
            status=Job.STATUS_DRAFT,
        )
        db.session.add_all([cls.active_job, cls.paused_job, cls.closed_job, cls.draft_job])
        db.session.flush()

        # 4. Create Resumes
        cls.alice_resume = Resume(
            candidate_id=cls.alice.id,
            name="Alice Core Resume",
            raw_text="Alice Candidate. Python Backend Developer with 5 years experience.",
            last_ats_score=85,
        )
        cls.bob_resume = Resume(
            candidate_id=cls.bob.id,
            name="Bob Secret Resume",
            raw_text="Bob Candidate. Confidential Resume.",
            last_ats_score=90,
        )
        db.session.add_all([cls.alice_resume, cls.bob_resume])
        db.session.flush()

        # 5. Create Application for Alice
        cls.alice_app = Application(
            job_id=cls.active_job.id,
            candidate_id=cls.alice.id,
            resume_id=cls.alice_resume.id,
            match_score=85.0,
            status=Application.STATUS_APPLIED,
        )
        db.session.add(cls.alice_app)
        db.session.flush()

        # 6. Create Notifications
        cls.alice_notif = Notification(
            candidate_id=cls.alice.id,
            title="Alice Notification",
            message="Your application was submitted.",
        )
        cls.bob_notif = Notification(
            candidate_id=cls.bob.id,
            title="Bob Private Notification",
            message="Private note for Bob.",
        )
        db.session.add_all([cls.alice_notif, cls.bob_notif])
        db.session.commit()

        # Plain integer IDs
        cls.alice_id = cls.alice.id
        cls.bob_id = cls.bob.id
        cls.carol_id = cls.carol.id
        cls.admin_id = cls.admin.id
        cls.alice_resume_id = cls.alice_resume.id
        cls.bob_resume_id = cls.bob_resume.id
        cls.alice_app_id = cls.alice_app.id
        cls.active_job_id = cls.active_job.id
        cls.paused_job_id = cls.paused_job.id
        cls.closed_job_id = cls.closed_job.id
        cls.draft_job_id = cls.draft_job.id
        cls.alice_notif_id = cls.alice_notif.id
        cls.bob_notif_id = cls.bob_notif.id
        cls.alice_slug = "alice-slug"
        cls.bob_slug = "bob-slug"

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        db.session.rollback()
        with self.app.test_request_context():
            from flask_login import logout_user
            logout_user()

    def tearDown(self):
        db.session.rollback()
        with self.app.test_request_context():
            from flask_login import logout_user
            logout_user()

    def _login_as(self, email: str, password: str):
        """Create a fresh test client authenticated via the official auth login endpoint."""
        with self.app.test_request_context():
            from flask_login import logout_user
            logout_user()
        client = self.app.test_client()
        client.post(
            "/auth/login",
            data={"login-email": email, "login-password": password, "login-submit": "Log in"},
        )
        return client

    # 1. Candidate changes another user's resume ID
    def test_01_idor_resume_access(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        # Alice tries to duplicate Bob's resume
        resp = client.post(f"/candidate/resumes/{self.bob_resume_id}/duplicate")
        self.assertEqual(resp.status_code, 404)

        # Alice tries to delete Bob's resume
        resp = client.post(f"/candidate/resumes/{self.bob_resume_id}/delete")
        self.assertEqual(resp.status_code, 404)

    # 2. Candidate changes another user's application ID
    def test_02_idor_application_access(self):
        client = self._login_as("bob@example.com", "Bob@1234")
        # Bob tries to view Alice's application
        resp = client.get(f"/candidate/applications/{self.alice_app_id}")
        self.assertEqual(resp.status_code, 404)

    # 3. Candidate accesses notifications (strictly candidate-owned)
    def test_03_notification_isolation(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        resp = client.get("/candidate/notifications")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Alice Notification", resp.data)
        self.assertNotIn(b"Bob Private Notification", resp.data)

    # 4. Candidate applies to a closed job -> Rejected
    def test_04_apply_closed_job(self):
        client = self._login_as("bob@example.com", "Bob@1234")
        resp = client.post(f"/candidate/jobs/{self.closed_job_id}/apply")
        self.assertEqual(resp.status_code, 302)  # Redirect with error flash
        # Ensure no application row was created
        app_row = Application.query.filter_by(
            job_id=self.closed_job_id, candidate_id=self.bob_id
        ).first()
        self.assertIsNone(app_row)

    # 5. Candidate applies to a paused job -> Rejected
    def test_05_apply_paused_job(self):
        client = self._login_as("bob@example.com", "Bob@1234")
        resp = client.post(f"/candidate/jobs/{self.paused_job_id}/apply")
        self.assertEqual(resp.status_code, 302)
        app_row = Application.query.filter_by(
            job_id=self.paused_job_id, candidate_id=self.bob_id
        ).first()
        self.assertIsNone(app_row)

    # 6. Candidate applies to a draft job -> Rejected
    def test_06_apply_draft_job(self):
        client = self._login_as("bob@example.com", "Bob@1234")
        resp = client.post(f"/candidate/jobs/{self.draft_job_id}/apply")
        self.assertEqual(resp.status_code, 302)
        app_row = Application.query.filter_by(
            job_id=self.draft_job_id, candidate_id=self.bob_id
        ).first()
        self.assertIsNone(app_row)

    # 7. Candidate submits inactive target_job_id to ATS live analysis -> Falls back gracefully
    def test_07_ats_inactive_target_job(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        resp = client.post("/candidate/api/analyze-live", json={
            "resume_data": {"personal": {"full_name": "Alice"}},
            "target_job_id": self.closed_job_id,  # Inactive job
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("ats_result", data)

    # 8. Candidate submits malformed target_job_id
    def test_08_ats_malformed_target_job(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        resp = client.post("/candidate/api/analyze-live", json={
            "resume_data": {"personal": {"full_name": "Alice"}},
            "target_job_id": "invalid-not-an-int-999999",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")

    # 9. Candidate accesses disabled public profile -> 404
    def test_09_disabled_public_profile(self):
        client = self.app.test_client()
        # Bob has public_profile_enabled = False
        resp = client.get(f"/profile/{self.bob_slug}")
        self.assertEqual(resp.status_code, 404)

        # Alice has public_profile_enabled = True
        resp = client.get(f"/profile/{self.alice_slug}")
        self.assertEqual(resp.status_code, 200)

    # 10. Candidate accesses private resume via public profile -> Only if enabled
    def test_10_public_resume_privacy(self):
        client = self.app.test_client()
        # Alice has public_resume_enabled = True
        resp = client.get(f"/profile/{self.alice_slug}")
        self.assertIn(b"Resume", resp.data)

        # Disable Alice's public resume directly via DB
        User.query.filter_by(id=self.alice_id).update({"public_resume_enabled": False})
        db.session.commit()
        resp = client.get(f"/profile/{self.alice_slug}")
        self.assertNotIn(b"Alice Core Resume", resp.data)

        # Restore
        User.query.filter_by(id=self.alice_id).update({"public_resume_enabled": True})
        db.session.commit()

    # 11. Candidate recruiter_discoverable privacy enforced in recruiter search
    def test_11_candidate_discovery_privacy(self):
        client = self._login_as("carol@example.com", "Carol@1234")  # Recruiter
        resp = client.get("/recruiter/candidates")
        self.assertEqual(resp.status_code, 200)
        # Alice is discoverable, Bob is NOT discoverable and has no apps to Carol's jobs
        self.assertIn(b"Alice Candidate", resp.data)
        self.assertNotIn(b"Bob Candidate", resp.data)

    # 12. Direct URL access to private candidate dossier -> 404
    def test_12_private_candidate_dossier_access(self):
        client = self._login_as("carol@example.com", "Carol@1234")
        # Carol tries to view Bob's dossier (Bob is recruiter_discoverable=False with 0 apps)
        resp = client.get(f"/recruiter/candidates/{self.bob_id}/intelligence")
        self.assertEqual(resp.status_code, 404)

    # 13. Excessively large ATS input rejected with HTTP 400
    def test_13_ats_input_length_limit(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        huge_jd = "Python " * 15000  # > 50,000 chars
        resp = client.post("/candidate/api/analyze-live", json={
            "resume_data": {},
            "jd_text": huge_jd,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"too long", resp.data)

    # 14. AI endpoints validate required parameters
    def test_14_ai_endpoints_validation(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        # Empty bullet
        resp = client.post("/candidate/api/improve-bullet", json={"bullet": ""})
        self.assertEqual(resp.status_code, 400)

        # Valid bullet
        resp = client.post("/candidate/api/improve-bullet",
                           json={"bullet": "worked on postgres database optimization"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")

    # 15. Disabled candidate account is blocked from active session
    def test_15_disabled_candidate_session_revocation(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        User.query.filter_by(id=self.alice_id).update({"is_active_account": False})
        db.session.commit()

        resp = client.get("/candidate/dashboard")
        self.assertEqual(resp.status_code, 302)  # Redirects to login

        # Restore
        User.query.filter_by(id=self.alice_id).update({"is_active_account": True})
        db.session.commit()

    # 16. Candidate attempts unauthorized direct URLs (/admin/, /recruiter/)
    def test_16_role_based_access_control(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        resp = client.get("/admin/")
        self.assertEqual(resp.status_code, 403)

        resp = client.get("/recruiter/dashboard")
        self.assertEqual(resp.status_code, 403)

    # 17. Candidate attempts unauthorized POST to admin action
    def test_17_candidate_post_to_admin_route(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        resp = client.post(f"/admin/users/{self.bob_id}/toggle")
        self.assertEqual(resp.status_code, 403)

    # 18. Candidate attempts duplicate application to same job
    def test_18_duplicate_application_prevention(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        # Alice already applied to active_job in setUpClass
        resp = client.post(f"/candidate/jobs/{self.active_job_id}/apply")
        self.assertEqual(resp.status_code, 302)  # Redirects with 'already applied' flash
        count = Application.query.filter_by(
            job_id=self.active_job_id, candidate_id=self.alice_id
        ).count()
        self.assertEqual(count, 1)

    # 19. Candidate manipulates saved-job IDs
    def test_19_saved_job_tampering(self):
        client_alice = self._login_as("alice@example.com", "Alice@1234")
        # Try saving closed job -> 404
        resp = client_alice.post(f"/candidate/jobs/{self.closed_job_id}/save")
        self.assertEqual(resp.status_code, 404)

        # Save active job
        resp = client_alice.post(f"/candidate/jobs/{self.active_job_id}/save")
        self.assertEqual(resp.status_code, 302)

        # Bob tries to unsave Alice's saved job -> 404 (Bob doesn't own it)
        client_bob = self._login_as("bob@example.com", "Bob@1234")
        resp = client_bob.post(f"/candidate/jobs/{self.active_job_id}/unsave")
        self.assertEqual(resp.status_code, 404)

        # Cleanup: Alice unsaves her own job
        client_alice.post(f"/candidate/jobs/{self.active_job_id}/unsave")

    # 20. Notification isolation per candidate
    def test_20_notification_tampering(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        # Alice visits notifications -> marks only her own notifications as read
        resp = client.get("/candidate/notifications")
        self.assertEqual(resp.status_code, 200)

        # Reload fresh from DB
        alice_notif = Notification.query.get(self.alice_notif_id)
        bob_notif = Notification.query.get(self.bob_notif_id)
        self.assertTrue(alice_notif.is_read)
        self.assertFalse(bob_notif.is_read)  # Bob's notification remains unread

    # 21. Candidate AI Bio Retouch API
    def test_21_api_retouch_bio(self):
        # Anonymous -> redirected / unauthorized
        anon = self.app.test_client()
        resp = anon.post("/candidate/api/retouch-bio", json={"raw_bio": "developer with flask"})
        self.assertEqual(resp.status_code, 302)

        # Authenticated candidate -> success with enhanced bio
        client = self._login_as("alice@example.com", "Alice@1234")
        resp = client.post(
            "/candidate/api/retouch-bio",
            json={
                "raw_bio": "i build python web apps with flask and postgresql",
                "headline": "Python Developer",
                "skills": "Python, Flask, PostgreSQL",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(len(data["retouched_bio"]) > 20)
        self.assertIn("provider", data)

    # 22. Candidate Profile Settings Validation
    def test_22_candidate_settings_validation(self):
        client = self._login_as("alice@example.com", "Alice@1234")
        # Missing required bio & skills
        resp = client.post(
            "/candidate/settings",
            data={
                "full_name": "Alice Wonderland",
                "phone": "+91 99999 88888",
                "location": "Bengaluru, India",
                "work_preference": "remote",
                "experience_level": "mid",
                # missing skills and bio
            },
        )
        self.assertEqual(resp.status_code, 200)

        # Valid post with required bio, skills, work_preference, experience_level
        resp = client.post(
            "/candidate/settings",
            data={
                "full_name": "Alice Wonderland",
                "phone": "+91 99999 88888",
                "location": "Bengaluru, India",
                "skills": "Python, Flask, Docker",
                "bio": "Experienced developer dedicated to building high-performance web applications.",
                "work_preference": "remote",
                "experience_level": "mid",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Profile updated.", resp.data)

    # 23. Career Entry Validation & Duplicate Prevention
    def test_23_career_entry_validation_and_deduplication(self):
        client = self._login_as("alice@example.com", "Alice@1234")

        # 1. Invalid date order (end date earlier than start date)
        resp = client.post(
            "/candidate/profile/career-entry",
            data={
                "entry_type": "education",
                "title": "BSc Computer Science",
                "organization": "Kuvempu University",
                "start_date": "2026",
                "end_date": "2024",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"cannot be earlier than start date", resp.data)

        # 2. Add valid career entry
        resp = client.post(
            "/candidate/profile/career-entry",
            data={
                "entry_type": "education",
                "title": "BSc Computer Science",
                "organization": "Kuvempu University",
                "start_date": "2020",
                "end_date": "2024",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Education added to your profile.", resp.data)

        # 3. Duplicate rejection
        resp = client.post(
            "/candidate/profile/career-entry",
            data={
                "entry_type": "education",
                "title": "BSc Computer Science",
                "organization": "Kuvempu University",
                "start_date": "2020",
                "end_date": "2024",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"already added to your profile", resp.data)


if __name__ == "__main__":
    unittest.main()
