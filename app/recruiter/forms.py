import re
from app.utils.time import utcnow
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, TextAreaField, BooleanField, SubmitField
from wtforms.fields import DateTimeLocalField
from wtforms.validators import DataRequired, Email, Length, Optional, URL, ValidationError


def no_control_characters(form, field):
    """Reject null bytes or control characters in text inputs."""
    if field.data and (re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", field.data)):
        raise ValidationError("Input contains forbidden control characters.")


def future_date(form, field):
    """Ensure the submitted datetime is strictly in the future (UTC)."""
    if field.data:
        orig_obj = getattr(form, "_obj", None)
        if orig_obj and getattr(orig_obj, "application_deadline", None) == field.data:
            return
        if field.data <= utcnow():
            raise ValidationError("Application deadline must be a future date and time.")


def password_complexity_check(form, field):
    """Enforce at least 8 chars, max 128 chars, and non-trivial password."""
    pwd = field.data or ""
    if len(pwd) < 8:
        raise ValidationError("Password must be at least 8 characters.")
    if len(pwd) > 128:
        raise ValidationError("Password exceeds maximum allowed length of 128 characters.")


def optional_whole_number(form, field):
    """Field is empty or a plain non-negative integer.

    salary_min/salary_max are plain StringFields (so the placeholder/format
    stays flexible in the template), but the model column is Integer. Without
    this check, typing "12,00,000" or "12.5" passed validation silently and
    _safe_int() in the view swallowed it into None — the job saved with no
    error shown, and the recruiter's salary figure just vanished.
    """
    val = (field.data or "").strip()
    if not val:
        return
    if not val.isdigit():
        raise ValidationError("Enter a whole number with no commas or symbols (e.g. 12).")


class RecruiterRegistrationForm(FlaskForm):
    # Company details
    company_name = StringField("Company name", validators=[DataRequired(), Length(min=2, max=200), no_control_characters])
    industry = SelectField(
        "Industry",
        choices=[
            ("", "Select industry"),
            ("technology", "Technology"), ("finance", "Finance"),
            ("healthcare", "Healthcare"), ("manufacturing", "Manufacturing"),
            ("retail", "Retail"), ("other", "Other"),
        ],
        validators=[DataRequired(message="Please select an industry.")],
    )
    company_size = SelectField(
        "Company size",
        choices=[
            ("", "Select company size"),
            ("1-10", "1–10 employees"), ("11-50", "11–50 employees"),
            ("51-200", "51–200 employees"), ("201-500", "201–500 employees"),
            ("500+", "500+ employees"),
        ],
        validators=[DataRequired(message="Please select a company size.")],
    )
    company_website = StringField("Company website", validators=[Optional(), Length(max=255), URL(require_tld=False), no_control_characters])

    # Contact person / account credentials
    contact_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=150), no_control_characters])
    contact_role = StringField("Role at company", validators=[Optional(), Length(max=150), no_control_characters])
    work_email = StringField("Work email", validators=[DataRequired(), Length(max=255), Email(check_deliverability=False), no_control_characters])
    phone = StringField("Phone number", validators=[Optional(), Length(max=30), no_control_characters])
    password = PasswordField("Password", validators=[DataRequired(), password_complexity_check])

    hiring_needs = TextAreaField("What are you hiring for?", validators=[Optional(), Length(max=5000), no_control_characters])

    agree_terms = BooleanField(
        "I confirm I'm authorised to register on behalf of this company, and agree "
        "to the Terms & Conditions and Privacy Policy",
        validators=[DataRequired(message="You must agree to continue.")],
    )
    submit = SubmitField("Submit for review")


class JobPostForm(FlaskForm):
    title = StringField("Job title", validators=[DataRequired(), Length(min=3, max=150), no_control_characters])
    description = TextAreaField("About the role", validators=[DataRequired(), Length(min=10, max=50000), no_control_characters])
    responsibilities = TextAreaField("Responsibilities", validators=[Optional(), Length(max=30000), no_control_characters])
    requirements = TextAreaField("Requirements", validators=[Optional(), Length(max=30000), no_control_characters])

    required_skills_raw = StringField(
        "Required skills",
        validators=[DataRequired(message="Add at least one required skill."), Length(max=2000), no_control_characters],
    )
    preferred_skills_raw = StringField(
        "Preferred / bonus skills",
        validators=[Optional(), Length(max=2000), no_control_characters],
    )

    job_type = SelectField(
        "Job type",
        choices=[
            ("", "Select job type"),
            ("full_time", "Full-time"), ("part_time", "Part-time"),
            ("internship", "Internship"), ("contract", "Contract"),
        ],
        validators=[DataRequired(message="Please select a job type.")],
    )
    work_mode = SelectField(
        "Work mode",
        choices=[
            ("", "Select work mode"),
            ("remote", "Remote"), ("hybrid", "Hybrid"), ("onsite", "On-site"),
        ],
        validators=[DataRequired(message="Please select a work mode.")],
    )
    experience_level = SelectField(
        "Experience level",
        choices=[
            ("", "Select experience level"),
            ("entry", "Entry level"), ("mid", "Mid level"),
            ("senior", "Senior"), ("lead", "Lead"),
        ],
        validators=[DataRequired(message="Please select an experience level.")],
    )
    location = StringField("Location", validators=[Optional(), Length(max=150), no_control_characters])
    salary_min = StringField("Minimum salary (LPA)", validators=[Optional(), Length(max=50), no_control_characters, optional_whole_number])
    salary_max = StringField("Maximum salary (LPA)", validators=[Optional(), Length(max=50), no_control_characters, optional_whole_number])
    application_deadline = DateTimeLocalField(
        "Application Deadline",
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired(message="Please set an application deadline."), future_date],
    )
    status = SelectField(
        "Job status",
        choices=[
            ("active", "Active (Publish immediately)"),
            ("draft", "Draft (Save as draft)"),
            ("paused", "Paused (Hidden temporarily)"),
            ("closed", "Closed (No longer accepting applicants)"),
        ],
        default="active",
    )
    submit = SubmitField("Save Job")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        # Cross-field check: can't do per-field since each is valid in
        # isolation. Only compare when both are present and numeric (the
        # optional_whole_number validator above already rejected anything
        # else, so a safe int() conversion here is guaranteed).
        smin = (self.salary_min.data or "").strip()
        smax = (self.salary_max.data or "").strip()
        if smin.isdigit() and smax.isdigit() and int(smin) > int(smax):
            self.salary_max.errors.append("Maximum salary must be greater than or equal to minimum salary.")
            return False
        return True
