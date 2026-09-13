import io
import unittest
from app import create_app, db
from app.models.user import User
from app.models.job import Job
from app.models.resume import Resume
from app.models.saved_job import SavedJob
from app.models.recruiter_profile import RecruiterProfile
from app.models.support_ticket import SupportTicket
from app.models.application import Application


class TestCandidateResumesAndPrivacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("testing")
        cls.client = cls.app.test_client()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Create candidate
        self.candidate = User(
            email="cand@test.com",
            full_name="Candidate Tester",
            role=User.ROLE_CANDIDATE,
            public_slug="cand-tester",
            public_profile_enabled=True,
            recruiter_discoverable=True,
            public_resume_enabled=True,
        )
        self.candidate.set_password("Secret123!")
        db.session.add(self.candidate)

        # Create primary resume
        self.primary_resume = Resume(
            candidate_id=1,  # will assign after flush
            name="My Master Resume",
            target_role="Full Stack Engineer",
            raw_text="Candidate Tester\nFull Stack Engineer\nEmail: cand@test.com\nSkills: Python, Flask, JavaScript, React, Docker, SQL.\nExperience: Software Engineer at Acme Corp (2022 - 2026). Built APIs.",
            is_primary=True,
            last_ats_score=85.0,
        )
        db.session.flush()
        self.primary_resume.candidate_id = self.candidate.id
        db.session.add(self.primary_resume)

        # Create recruiter and job
        self.recruiter_user = User(
            email="rec@test.com",
            full_name="Recruiter Tester",
            role=User.ROLE_RECRUITER,
        )
        self.recruiter_user.set_password("Secret123!")
        db.session.add(self.recruiter_user)
        db.session.flush()

        self.profile = RecruiterProfile(
            user_id=self.recruiter_user.id,
            company_name="Acme Corp",
            approval_status=RecruiterProfile.STATUS_APPROVED,
        )
        db.session.add(self.profile)
        db.session.flush()

        self.job = Job(
            recruiter_profile_id=self.profile.id,
            title="Senior Python Engineer",
            description="Looking for Python, Flask, Docker, and SQL experience.",
            required_skills_raw="Python, Flask, Docker, SQL",
            status=Job.STATUS_ACTIVE,
        )
        db.session.add(self.job)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_candidate(self):
        client = self.app.test_client()
        client.post("/auth/login", data={
            "login-email": "cand@test.com",
            "login-password": "Secret123!",
            "login-submit": "Log in",
        }, follow_redirects=True)
        return client

    def login_recruiter(self):
        client = self.app.test_client()
        client.post("/auth/login", data={
            "login-email": "rec@test.com",
            "login-password": "Secret123!",
            "login-submit": "Log in",
        }, follow_redirects=True)
        return client

    def test_01_ats_check_does_not_create_duplicate_untitled_resume(self):
        """Testing a resume against a job should update existing resume, NOT spawn duplicate Untitled Resume."""
        client = self.login_candidate()
        initial_count = Resume.query.filter_by(candidate_id=self.candidate.id).count()
        self.assertEqual(initial_count, 1)

        # Post analysis using candidate's resume text
        res = client.post("/candidate/resume-ai", data={
            "resume_text": self.primary_resume.raw_text,
            "analysis_mode": "specific_job",
            "job_selection_type": "select",
            "selected_job_id": str(self.job.id),
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Count should still be 1 — no duplicate Untitled Resume spawned!
        after_count = Resume.query.filter_by(candidate_id=self.candidate.id).count()
        self.assertEqual(after_count, 1)

        # Primary resume's ATS score was refreshed
        refreshed = db.session.get(Resume, self.primary_resume.id)
        self.assertIsNotNone(refreshed.last_ats_score)

    def test_02_resume_preview_api_returns_structured_data(self):
        """The /candidate/api/resumes/<id>/preview endpoint returns structured JSON for the modal view."""
        client = self.login_candidate()
        res = client.get(f"/candidate/api/resumes/{self.primary_resume.id}/preview")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["resume"]["name"], "My Master Resume")
        self.assertIn("skills", data["resume"]["structured"])
        self.assertIn("Python", data["resume"]["raw_text"])

    def test_03_privacy_visibility_public_profile_toggle(self):
        """Disabling public profile returns 404, enabling returns 200."""
        # 1. Enabled
        res = self.client.get(f"/profile/{self.candidate.public_slug}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Candidate Tester", res.data)

        # 2. Disable public profile
        client = self.login_candidate()
        client.post("/candidate/privacy", data={
            "public_slug": "cand-tester",
            # public_profile_enabled not sent -> False
        }, follow_redirects=True)

        refreshed = db.session.get(User, self.candidate.id)
        self.assertFalse(refreshed.public_profile_enabled)

        # Now public profile returns 404
        res_404 = self.client.get(f"/profile/{self.candidate.public_slug}")
        self.assertEqual(res_404.status_code, 404)

    def test_04_privacy_visibility_hide_resume(self):
        """When public_resume_enabled is False, public profile hides resume content."""
        client = self.login_candidate()
        client.post("/candidate/privacy", data={
            "public_slug": "cand-tester",
            "public_profile_enabled": "y",
            # public_resume_enabled not sent -> False
        }, follow_redirects=True)

        refreshed = db.session.get(User, self.candidate.id)
        self.assertTrue(refreshed.public_profile_enabled)
        self.assertFalse(refreshed.public_resume_enabled)

        res = self.client.get(f"/profile/{self.candidate.public_slug}")
        self.assertEqual(res.status_code, 200)
        # Profile shows name but hides resume
        self.assertIn(b"Candidate Tester", res.data)
        self.assertNotIn(b"Software Engineer at Acme Corp", res.data)

    def test_05_saved_jobs_lifecycle(self):
        """Candidate can save, view, and unsave jobs."""
        client = self.login_candidate()

        # Save job
        save_res = client.post(f"/candidate/jobs/{self.job.id}/save", follow_redirects=True)
        self.assertEqual(save_res.status_code, 200)
        self.assertTrue(SavedJob.query.filter_by(candidate_id=self.candidate.id, job_id=self.job.id).first() is not None)

        # View saved jobs
        view_res = client.get("/candidate/saved-jobs")
        self.assertEqual(view_res.status_code, 200)
        self.assertIn(b"Senior Python Engineer", view_res.data)

        # Unsave job
        unsave_res = client.post(f"/candidate/jobs/{self.job.id}/unsave", follow_redirects=True)
        self.assertEqual(unsave_res.status_code, 200)
        self.assertTrue(SavedJob.query.filter_by(candidate_id=self.candidate.id, job_id=self.job.id).first() is None)

    def test_06_support_ticket_creation_and_view(self):
        """Candidate can submit a support ticket and view it in support center."""
        client = self.login_candidate()

        # Submit ticket
        res = client.post("/support/new", data={
            "issue_type": "technical",
            "subject": "Question about ATS parsing",
            "description": "How does the ATS calculate keyword density?",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        ticket = SupportTicket.query.filter_by(user_id=self.candidate.id).first()
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.subject, "Question about ATS parsing")

        # View support index
        index_res = client.get("/support")
        self.assertEqual(index_res.status_code, 200)
        self.assertIn(b"Question about ATS parsing", index_res.data)

    def test_07_upload_resume_file(self):
        """Candidate can directly upload a PDF resume from My Resumes page."""
        from app.utils.resume_pdf import build_structured_resume_pdf, parse_resume_to_structured_dict
        client = self.login_candidate()

        sample_text = "Jane Doe\nDevOps Engineer\nEmail: jane@example.com\nSkills: Kubernetes, Terraform, AWS, Python, CI/CD\nExperience: Senior DevOps at Cloud Corp (2021-2026)."
        structured = parse_resume_to_structured_dict(sample_text)
        pdf_bytes = build_structured_resume_pdf(structured).getvalue()

        res = client.post("/candidate/resumes/upload", data={
            "resume_file": (io.BytesIO(pdf_bytes), "Jane_DevOps_Resume.pdf"),
            "resume_name": "Jane Cloud Resume",
            "target_role": "DevOps Architect",
            "is_primary": "1",
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        uploaded = Resume.query.filter_by(candidate_id=self.candidate.id, name="Jane Cloud Resume").first()
        self.assertIsNotNone(uploaded)
        self.assertEqual(uploaded.target_role, "DevOps Architect")
        self.assertTrue(uploaded.is_primary)
        self.assertIn("Kubernetes", uploaded.raw_text)

    def test_08_delete_resume_with_application_soft_deletes_and_promotes_primary(self):
        """Deleting a resume associated with an application soft-deletes it without FK error and promotes another resume."""
        client = self.login_candidate()

        # Create a second resume for candidate
        second_resume = Resume(
            candidate_id=self.candidate.id,
            name="Tailored AI Resume",
            target_role="AI Engineer",
            raw_text="Candidate Tester\nAI Engineer\nSkills: PyTorch, NLP, LLMs",
            is_primary=False,
        )
        db.session.add(second_resume)
        db.session.commit()

        # Link primary resume to an application
        app_record = Application(
            job_id=self.job.id,
            candidate_id=self.candidate.id,
            resume_id=self.primary_resume.id,
            status=Application.STATUS_APPLIED,
        )
        db.session.add(app_record)
        db.session.commit()

        # Delete the primary resume
        del_res = client.post(f"/candidate/resumes/{self.primary_resume.id}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)

        # Verify primary resume is soft-deleted, not hard-deleted (preserving foreign key integrity for recruiter)
        primary_refreshed = db.session.get(Resume, self.primary_resume.id)
        self.assertIsNotNone(primary_refreshed)
        self.assertTrue(primary_refreshed.is_deleted)
        self.assertFalse(primary_refreshed.is_primary)

        # Verify second resume is now primary
        second_refreshed = db.session.get(Resume, second_resume.id)
        self.assertTrue(second_refreshed.is_primary)
        self.assertFalse(second_refreshed.is_deleted)

        # Verify active resumes list does not include the deleted resume
        active_resumes = Resume.get_active_resumes(self.candidate.id)
        self.assertEqual(len(active_resumes), 1)
        self.assertEqual(active_resumes[0].id, second_resume.id)

        # Ensure my-resumes page renders without the soft-deleted resume
        my_res = client.get("/candidate/my-resumes")
        self.assertEqual(my_res.status_code, 200)
        self.assertNotIn(b"My Master Resume", my_res.data)
        self.assertIn(b"Tailored AI Resume", my_res.data)

    def test_09_apply_with_specific_resume_id(self):
        """Candidate can select a specific active resume when applying to a job."""
        client = self.login_candidate()

        # Create tailored resume
        tailored_resume = Resume(
            candidate_id=self.candidate.id,
            name="Tailored Python Resume",
            target_role="Senior Python Engineer",
            raw_text="Candidate Tester\nSenior Python Engineer\nPython, Flask, Docker, SQL expert.",
            is_primary=False,
        )
        db.session.add(tailored_resume)
        db.session.commit()

        # Apply using the tailored resume (not the primary one)
        apply_res = client.post(f"/candidate/jobs/{self.job.id}/apply", data={
            "resume_id": str(tailored_resume.id),
            "cover_note": "Tailored application note",
        }, follow_redirects=True)
        self.assertEqual(apply_res.status_code, 200)

        # Verify application record uses the tailored resume
        application = Application.query.filter_by(job_id=self.job.id, candidate_id=self.candidate.id).first()
        self.assertIsNotNone(application)
        self.assertEqual(application.resume_id, tailored_resume.id)

    def test_10_resume_builder_select_and_save_resume(self):
        """Resume builder provides selection of resumes and allows saving new or existing resumes."""
        client = self.login_candidate()

        # 1. Access builder with existing resume_id
        res = client.get(f"/candidate/resume-builder?resume_id={self.primary_resume.id}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"My Master Resume", res.data)
        self.assertIn(b"builder-resume-select", res.data)

        # 2. Access builder in new mode
        res_new = client.get("/candidate/resume-builder?new=1")
        self.assertEqual(res_new.status_code, 200)
        self.assertIn(b"builder-resume-select", res_new.data)

        # 3. Save new resume via builder API
        save_res = client.post("/candidate/api/resume-builder/save", json={
            "name": "Brand New Built Resume",
            "target_role": "Backend Lead",
            "is_primary": True,
            "resume_data": {
                "personal": {"name": "Candidate Tester", "email": "cand@test.com"},
                "summary": "Experienced backend lead architect.",
                "skills": ["Python", "Flask", "PostgreSQL"],
                "experience": [],
                "education": [],
            }
        })
        self.assertEqual(save_res.status_code, 200)
        data = save_res.get_json()
        self.assertEqual(data["status"], "success")
        new_resume_id = data["resume_id"]

        new_resume = db.session.get(Resume, new_resume_id)
        self.assertIsNotNone(new_resume)
        self.assertTrue(new_resume.is_primary)

        # Check that previous primary resume is no longer primary
        primary_refreshed = db.session.get(Resume, self.primary_resume.id)
        self.assertFalse(primary_refreshed.is_primary)


if __name__ == "__main__":
    unittest.main()

