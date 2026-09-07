import hashlib
from app.utils.time import utcnow

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


class User(UserMixin, db.Model):
    """A single account table for candidates, recruiters, and admins.

    The `role` column decides which profile/behaviour applies. Recruiters
    additionally have a RecruiterProfile row that tracks admin approval.
    """

    __tablename__ = "users"

    ROLE_CANDIDATE = "candidate"
    ROLE_RECRUITER = "recruiter"
    ROLE_ADMIN = "admin"
    ROLES = (ROLE_CANDIDATE, ROLE_RECRUITER, ROLE_ADMIN)

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)  # NULL for Google-only accounts
    google_id = db.Column(db.String(128), unique=True, nullable=True, index=True)
    role = db.Column(db.String(20), nullable=False, default=ROLE_CANDIDATE)
    is_active_account = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    # Candidate-only, optional fields
    headline = db.Column(db.String(150), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    location = db.Column(db.String(150), nullable=True)
    skills = db.Column(db.String(500), nullable=True)  # comma-separated, LinkedIn-style skill chips
    bio = db.Column(db.Text, nullable=True)
    avatar_filename = db.Column(db.String(255), nullable=True)
    experience = db.Column(db.Text, nullable=True)
    education = db.Column(db.Text, nullable=True)
    certifications = db.Column(db.Text, nullable=True)
    projects = db.Column(db.Text, nullable=True)
    github_url = db.Column(db.String(255), nullable=True)
    linkedin_url = db.Column(db.String(255), nullable=True)
    portfolio_url = db.Column(db.String(255), nullable=True)
    preferred_job_role = db.Column(db.String(150), nullable=True)
    preferred_location = db.Column(db.String(150), nullable=True)
    work_preference = db.Column(db.String(30), nullable=True)
    expected_salary = db.Column(db.String(100), nullable=True)
    experience_level = db.Column(db.String(50), nullable=True)
    public_slug = db.Column(db.String(80), unique=True, nullable=True, index=True)
    # Nullable keeps additive schema updates safe for existing accounts;
    # Python-side defaults apply to every newly created account.
    public_profile_enabled = db.Column(db.Boolean, default=False, nullable=True)
    recruiter_discoverable = db.Column(db.Boolean, default=True, nullable=True)
    public_resume_enabled = db.Column(db.Boolean, default=False, nullable=True)

    recruiter_profile = db.relationship(
        "RecruiterProfile", back_populates="user", uselist=False,
        cascade="all, delete-orphan", foreign_keys="RecruiterProfile.user_id",
    )
    resumes = db.relationship(
        "Resume", back_populates="candidate", cascade="all, delete-orphan",
        foreign_keys="Resume.candidate_id",
    )
    applications = db.relationship(
        "Application", back_populates="candidate", cascade="all, delete-orphan",
        foreign_keys="Application.candidate_id",
    )
    career_entries = db.relationship(
        "CareerEntry", back_populates="candidate", cascade="all, delete-orphan",
        order_by="CareerEntry.created_at.desc()",
    )
    saved_jobs = db.relationship("SavedJob", back_populates="candidate", cascade="all, delete-orphan")
    notifications = db.relationship("Notification", back_populates="candidate", cascade="all, delete-orphan", order_by="Notification.created_at.desc()")
    support_tickets = db.relationship("SupportTicket", back_populates="user", cascade="all, delete-orphan", order_by="SupportTicket.created_at.desc()")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        if not self.password_hash:
            # Google-only account — password login not allowed
            return False
        return check_password_hash(self.password_hash, raw_password)

    @property
    def password_stamp(self):
        """Short fingerprint of the current password_hash.

        Embedded in the Flask-Login session/remember-cookie identifier (see
        get_id()) so that changing a password invalidates every session and
        "remember me" cookie issued before the change, not just future
        logins with the old password. Derived from password_hash instead of
        a separate counter/column so this needs no schema change and can
        never drift out of sync with the actual password.

        For Google-only accounts (password_hash is None) we use the fixed
        stamp "oauth" — their sessions are invalidated when the admin
        disables their account (load_user checks is_active_account).
        """
        if not self.password_hash:
            return "oauth"
        return hashlib.sha256(self.password_hash.encode()).hexdigest()[:16]

    def get_id(self):
        """Flask-Login identifier: "<user id>|<password stamp>".

        app.load_user() splits this back apart and rejects the session if
        the stamp doesn't match the user's *current* password_hash — see
        password_stamp above.
        """
        return f"{self.id}|{self.password_stamp}"

    @property
    def is_active(self):
        return bool(self.is_active_account)

    @property
    def is_candidate(self):
        return self.role == self.ROLE_CANDIDATE

    @property
    def is_recruiter(self):
        return self.role == self.ROLE_RECRUITER

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_approved_recruiter(self):
        return (
            self.is_recruiter
            and self.recruiter_profile is not None
            and self.recruiter_profile.approval_status == "approved"
        )

    @property
    def profile_completeness(self):
        """Rough 0-100 score for the 'complete your profile' nudge —
        counts filled optional fields plus whether a resume exists.
        """
        if not self.is_candidate:
            return 100

        # Each optional profile element is worth five points.  This keeps the
        # suggestions tangible (for example, adding GitHub really is +5%).
        entry_types = {entry.entry_type for entry in self.career_entries}
        fields = [
            self.avatar_filename, self.headline, self.phone, self.location,
            self.skills, self.bio,
            self.experience or (self.career_entries if "experience" in entry_types else None),
            self.education or (self.career_entries if "education" in entry_types else None),
            self.certifications or (self.career_entries if "certification" in entry_types else None),
            self.projects or (self.career_entries if "project" in entry_types else None),
            self.github_url,
            self.linkedin_url, self.portfolio_url, self.preferred_job_role,
            self.preferred_location, self.work_preference,
            self.expected_salary, self.experience_level,
        ]
        filled = sum(1 for value in fields if bool(value))
        has_resume = len(self.resumes) > 0

        done_checks = filled + (1 if has_resume else 0)
        # Name and email are supplied at registration (the initial five
        # points); the remaining nineteen checks are five points each.
        return min(100, 5 + done_checks * 5)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"
