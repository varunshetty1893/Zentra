from app.utils.time import utcnow

from app import db


class Application(db.Model):
    """A candidate's application to a job, with a computed match score."""

    __tablename__ = "applications"

    STATUS_APPLIED = "applied"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_SHORTLISTED = "shortlisted"
    STATUS_REJECTED = "rejected"
    STATUS_HIRED = "hired"
    STATUS_INTERVIEW = "interview"
    STATUSES = (
        STATUS_APPLIED, STATUS_UNDER_REVIEW, STATUS_SHORTLISTED,
        STATUS_REJECTED, STATUS_HIRED, STATUS_INTERVIEW,
    )

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    candidate_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resume_id = db.Column(db.Integer, db.ForeignKey("resumes.id"), nullable=False)

    match_score = db.Column(db.Float, nullable=True)
    # When match_score was last computed. Set at application-create time and
    # bumped whenever a targeted re-score runs (job scoring-input edit,
    # resume content edit). NULL only for rows that predate this column,
    # which the admin backfill utility (see recruiter.refresh_match_scores)
    # is meant to fill in.
    scored_at = db.Column(db.DateTime, nullable=True)
    cover_note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_APPLIED)
    applied_at = db.Column(db.DateTime, default=utcnow)
    # Set when a recruiter schedules an interview (status -> interview).
    # Surfaced to the candidate via the status-change notification/event note.
    interview_date = db.Column(db.DateTime, nullable=True)
    interview_type = db.Column(db.String(50), nullable=True)
    interviewer_name = db.Column(db.String(120), nullable=True)
    meeting_link = db.Column(db.String(255), nullable=True)
    # Recruiter explicitly marks an interview done — separate from "is the
    # scheduled date in the past" because an interview can slip and still
    # needs an explicit close-out action.
    interview_completed = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    # Optional free-text reason captured on the Reject action, shown back on
    # the Rejected pipeline card as an audit trail of why a candidate was
    # passed on.
    rejection_reason = db.Column(db.Text, nullable=True)

    job = db.relationship("Job", back_populates="applications")
    candidate = db.relationship("User", back_populates="applications", foreign_keys=[candidate_id])
    resume = db.relationship("Resume", back_populates="applications")
    events = db.relationship("ApplicationEvent", back_populates="application", cascade="all, delete-orphan", order_by="ApplicationEvent.created_at.asc()")

    __table_args__ = (
        db.UniqueConstraint("job_id", "candidate_id", name="uq_one_application_per_job"),
    )

    def __repr__(self):
        return f"<Application job={self.job_id} candidate={self.candidate_id}>"
