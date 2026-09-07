from app.utils.time import utcnow

from app import db


class RecruiterProfile(db.Model):
    """Company + approval info for a recruiter account.

    Every recruiter starts in 'pending' status after registering. An admin
    must move them to 'approved' before they can post jobs.
    """

    __tablename__ = "recruiter_profiles"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    company_name = db.Column(db.String(200), nullable=False)
    logo_filename = db.Column(db.String(255), nullable=True)
    industry = db.Column(db.String(100), nullable=True)
    company_size = db.Column(db.String(50), nullable=True)
    company_website = db.Column(db.String(255), nullable=True)

    contact_role = db.Column(db.String(150), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    hiring_needs = db.Column(db.Text, nullable=True)

    approval_status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    submitted_at = db.Column(db.DateTime, default=utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    rejection_reason = db.Column(db.String(255), nullable=True)

    user = db.relationship("User", back_populates="recruiter_profile", foreign_keys=[user_id])
    jobs = db.relationship("Job", back_populates="recruiter_profile", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<RecruiterProfile {self.company_name} ({self.approval_status})>"
