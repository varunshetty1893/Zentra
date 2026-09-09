from app.utils.time import utcnow

from app import db


class Job(db.Model):
    """A job posting created by an approved recruiter."""

    __tablename__ = "jobs"

    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_CLOSED = "closed"
    STATUSES = (STATUS_DRAFT, STATUS_ACTIVE, STATUS_PAUSED, STATUS_CLOSED)

    id = db.Column(db.Integer, primary_key=True)
    recruiter_profile_id = db.Column(
        db.Integer, db.ForeignKey("recruiter_profiles.id"), nullable=False
    )

    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)
    responsibilities = db.Column(db.Text, nullable=True)
    requirements = db.Column(db.Text, nullable=True)

    # Structured, recruiter-authored skill tags used by the ATS scoring engine.
    # Comma-separated canonical skill names (e.g. "AWS, Terraform, Kubernetes").
    # required_skills_raw is required at job-creation time — relying solely on
    # NLP keyword extraction from free-text description/requirements proved
    # unreliable and produced inconsistent Skills Match scores.
    required_skills_raw = db.Column(db.Text, nullable=False, default="")
    preferred_skills_raw = db.Column(db.Text, nullable=True)

    job_type = db.Column(db.String(30), nullable=False, default="full_time")
    work_mode = db.Column(db.String(20), nullable=False, default="remote")  # remote/hybrid/onsite
    experience_level = db.Column(db.String(20), nullable=False, default="mid")
    location = db.Column(db.String(150), nullable=True)

    salary_min = db.Column(db.Integer, nullable=True)
    salary_max = db.Column(db.Integer, nullable=True)

    # Per-job AI shortlist threshold (0-100). Recruiters can adjust this from
    # the Shortlisted workspace page. Persisted so the setting survives page
    # reloads. Safe default matches the platform-wide SHORTLIST_SCORE_THRESHOLD.
    shortlist_threshold = db.Column(db.Integer, nullable=False, default=40, server_default="40")

    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE)
    created_at = db.Column(db.DateTime, default=utcnow)
    application_deadline = db.Column(db.DateTime, nullable=True)  # None = no deadline

    recruiter_profile = db.relationship("RecruiterProfile", back_populates="jobs")
    applications = db.relationship("Application", back_populates="job", cascade="all, delete-orphan")

    @property
    def is_deadline_passed(self):
        if not self.application_deadline:
            return False
        return utcnow() > self.application_deadline

    @property
    def required_skills_list(self):
        return [s.strip() for s in (self.required_skills_raw or "").split(",") if s.strip()]

    @property
    def preferred_skills_list(self):
        return [s.strip() for s in (self.preferred_skills_raw or "").split(",") if s.strip()]

    @property
    def is_accepting_applications(self):
        return self.status == self.STATUS_ACTIVE and not self.is_deadline_passed

    def __repr__(self):
        return f"<Job {self.title}>"
