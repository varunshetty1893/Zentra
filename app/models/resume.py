from app.utils.time import utcnow

from app import db


class Resume(db.Model):
    """A candidate's resume — either an uploaded file or pasted text."""

    __tablename__ = "resumes"

    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    source = db.Column(db.String(10), nullable=False, default="paste")  # 'upload' | 'paste' | 'builder'
    original_filename = db.Column(db.String(255), nullable=True)
    stored_filename = db.Column(db.String(255), nullable=True)  # path on disk, if uploaded
    raw_text = db.Column(db.Text, nullable=False)  # extracted/pasted plain text
    name = db.Column(db.String(150), nullable=True)
    target_role = db.Column(db.String(150), nullable=True)

    # Latest ATS check result against a JD, if any
    last_ats_score = db.Column(db.Float, nullable=True)
    last_matched_keywords = db.Column(db.Text, nullable=True)  # comma-separated
    last_missing_keywords = db.Column(db.Text, nullable=True)  # comma-separated

    is_primary = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, server_default="false")
    created_at = db.Column(db.DateTime, default=utcnow)
    # Bumped whenever resume content/name/target_role is edited in place
    # (Resume Builder's save-existing path). created_at is never touched
    # after the row is first created. Falls back to created_at for rows
    # that predate this column (see sync_schema.py).
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    candidate = db.relationship("User", back_populates="resumes", foreign_keys=[candidate_id])
    applications = db.relationship("Application", back_populates="resume")

    @classmethod
    def get_primary(cls, candidate_id):
        """Fetch the designated primary resume for a candidate, falling back to the most recent non-deleted."""
        if not candidate_id:
            return None
        primary = cls.query.filter_by(candidate_id=candidate_id, is_primary=True, is_deleted=False).first()
        if primary:
            return primary
        return cls.query.filter_by(candidate_id=candidate_id, is_deleted=False).order_by(cls.created_at.desc()).first()

    @classmethod
    def get_active_resumes(cls, candidate_id):
        """Fetch all active (non-deleted) resumes for a candidate, ordered newest first."""
        if not candidate_id:
            return []
        return cls.query.filter_by(candidate_id=candidate_id, is_deleted=False).order_by(cls.created_at.desc()).all()

    def __repr__(self):
        return f"<Resume {self.id} candidate={self.candidate_id}>"
