from app.utils.time import utcnow

from flask import Blueprint, render_template, request, current_app, abort, flash, redirect, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import db

from app.models.job import Job
from app.models.resume import Resume
from app.models.saved_job import SavedJob
from app.models.user import User
from app.models.career_entry import CareerEntry
from app.models.recruiter_profile import RecruiterProfile
from app.models.support_ticket import SupportTicket, SupportTicketMessage
from app.ml.job_matcher import rank_jobs_for_resume, match_breakdown, rank_and_explain_jobs
from app.utils.security import limiter

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_PUBLIC", "30 per minute"))
def landing():
    return render_template("landing.html")


@main_bp.route("/about")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_PUBLIC", "30 per minute"))
def about():
    return render_template("about.html")


@main_bp.route("/terms")
def terms():
    return render_template("terms.html", now=utcnow())


@main_bp.route("/privacy")
def privacy():
    return render_template("privacy.html", now=utcnow())


@main_bp.route("/profile/<slug>")
def public_profile(slug):
    candidate = User.query.filter_by(public_slug=slug, public_profile_enabled=True, role=User.ROLE_CANDIDATE, is_active_account=True).first_or_404()
    entries = {
        entry_type: [entry for entry in candidate.career_entries if entry.entry_type == entry_type]
        for entry_type in CareerEntry.TYPES
    }
    public_resume = None
    if candidate.public_resume_enabled:
        public_resume = Resume.get_primary(candidate.id)
    return render_template("public_profile.html", candidate=candidate, entries=entries, public_resume=public_resume)


@main_bp.route("/jobs")
def jobs():
    import re
    from flask import redirect, url_for, flash
    from app.models.admin_setting import AdminSetting

    # Enforce public job listing admin setting
    if AdminSetting.get("public_job_listing", "true").lower() == "false" and not current_user.is_authenticated:
        flash("Please log in to browse platform job listings.", "info")
        return redirect(url_for("auth.login"))

    # ── Safe page number ──────────────────────────────────────────────────────
    # type=int returns None for non-integer values (e.g. "abc", "-1.5")
    page = request.args.get("page", 1, type=int)
    if not page or page < 1:
        page = 1

    PER_PAGE = 12

    # ── Build filter query (active and unexpired) ─────────────────────────────
    query = Job.query.options(joinedload(Job.recruiter_profile)).join(Job.recruiter_profile).filter(
        Job.status == Job.STATUS_ACTIVE,
        or_(Job.application_deadline.is_(None), Job.application_deadline >= utcnow())
    )

    keyword = (request.args.get("q") or "").strip()
    if len(keyword) > 100:
        keyword = keyword[:100].strip()
    if re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", keyword):
        keyword = ""
    if keyword:
        tokens = [t for t in keyword.split() if t]
        for token in tokens:
            t_pat = f"%{token}%"
            query = query.filter(or_(
                RecruiterProfile.company_name.ilike(t_pat),
                Job.title.ilike(t_pat),
                Job.required_skills_raw.ilike(t_pat),
                Job.preferred_skills_raw.ilike(t_pat),
                Job.description.ilike(t_pat),
                Job.requirements.ilike(t_pat),
                Job.responsibilities.ilike(t_pat),
                Job.location.ilike(t_pat),
            ))

    allowed_experience = {"entry", "mid", "senior"}
    allowed_work_modes = {"remote", "hybrid", "onsite"}
    allowed_job_types = {"full_time", "part_time", "internship"}

    levels = [value for value in request.args.getlist("experience") if value in allowed_experience]
    if levels:
        query = query.filter(Job.experience_level.in_(levels))
    modes = [value for value in request.args.getlist("work_mode") if value in allowed_work_modes]
    if modes:
        query = query.filter(Job.work_mode.in_(modes))
    job_types = [value for value in request.args.getlist("job_type") if value in allowed_job_types]
    if job_types:
        query = query.filter(Job.job_type.in_(job_types))

    salary_min_raw = (request.args.get("salary_min") or "").strip()
    salary_max_raw = (request.args.get("salary_max") or "").strip()
    salary_min = int(salary_min_raw) if salary_min_raw.isdigit() and int(salary_min_raw) <= 1000 else None
    salary_max = int(salary_max_raw) if salary_max_raw.isdigit() and int(salary_max_raw) <= 1000 else None
    if salary_min is not None and salary_max is not None and salary_min > salary_max:
        salary_min, salary_max = salary_max, salary_min
    if salary_min is not None:
        query = query.filter(Job.salary_max >= salary_min)
    if salary_max is not None:
        query = query.filter(Job.salary_min <= salary_max)

    # ── DB-level pagination: only PER_PAGE rows fetched ───────────────────────
    pagination = query.order_by(Job.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )
    page_jobs = pagination.items   # exactly ≤10 Job objects

    # Clamp page to last valid page when requested page exceeds total
    if page > pagination.pages and pagination.pages > 0:
        page = pagination.pages
        pagination = query.order_by(Job.created_at.desc()).paginate(
            page=page, per_page=PER_PAGE, error_out=False
        )
        page_jobs = pagination.items

    # ── ATS scoring: ONE call per job, result reused for score + explanation ──
    resume_text = _latest_resume_text()
    ranked, match_explanations = rank_and_explain_jobs(resume_text, page_jobs)

    # ── Saved jobs (candidate only) ───────────────────────────────────────────
    saved_job_ids = set()
    if current_user.is_authenticated and current_user.is_candidate:
        saved_job_ids = {item.job_id for item in SavedJob.query.filter_by(candidate_id=current_user.id).all()}

    return render_template(
        "jobs.html",
        ranked_jobs=ranked,
        saved_job_ids=saved_job_ids,
        match_explanations=match_explanations,
        pagination=pagination,
        current_page=page,
        clean_filters={
            "q": keyword,
            "experience": levels,
            "work_mode": modes,
            "job_type": job_types,
            "salary_min": salary_min,
            "salary_max": salary_max,
        },
    )


@main_bp.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = Job.query.get_or_404(job_id)

    if job.status == Job.STATUS_DRAFT:
        is_owner = (
            current_user.is_authenticated
            and current_user.is_recruiter
            and current_user.recruiter_profile
            and current_user.recruiter_profile.id == job.recruiter_profile_id
        )
        if not (current_user.is_authenticated and (current_user.is_admin or is_owner)):
            abort(404)

    resume_text = _latest_resume_text()
    breakdown = match_breakdown(resume_text, job) if resume_text else None

    is_saved = current_user.is_authenticated and current_user.is_candidate and SavedJob.query.filter_by(candidate_id=current_user.id, job_id=job.id).first() is not None
    candidate_resumes = []
    primary_resume = None
    if current_user.is_authenticated and current_user.is_candidate:
        candidate_resumes = Resume.get_active_resumes(current_user.id)
        primary_resume = Resume.get_primary(current_user.id)

    return render_template(
        "job_detail.html",
        job=job,
        breakdown=breakdown,
        is_saved=is_saved,
        candidate_resumes=candidate_resumes,
        primary_resume=primary_resume,
    )


@main_bp.route("/companies/<int:company_id>")
def company_detail(company_id):
    company = RecruiterProfile.query.filter_by(
        id=company_id, approval_status=RecruiterProfile.STATUS_APPROVED
    ).first_or_404()

    # Active, unexpired jobs posted by this company
    company_jobs = Job.query.options(joinedload(Job.recruiter_profile)).filter(
        Job.recruiter_profile_id == company.id,
        Job.status == Job.STATUS_ACTIVE,
        or_(Job.application_deadline.is_(None), Job.application_deadline >= utcnow())
    ).order_by(Job.created_at.desc()).all()

    # ATS scoring for logged-in candidate
    resume_text = _latest_resume_text()
    ranked, match_explanations = rank_and_explain_jobs(resume_text, company_jobs)

    saved_job_ids = set()
    if current_user.is_authenticated and current_user.is_candidate:
        saved_job_ids = {item.job_id for item in SavedJob.query.filter_by(candidate_id=current_user.id).all()}

    return render_template(
        "company_detail.html",
        company=company,
        ranked_jobs=ranked,
        total_jobs_count=len(company_jobs),
        match_explanations=match_explanations,
        saved_job_ids=saved_job_ids,
    )


def _latest_resume_text():
    if not (current_user.is_authenticated and current_user.is_candidate):
        return None
    resume = Resume.get_primary(current_user.id)
    return resume.raw_text if resume else None


# ---------------------------------------------------------------------------
# Help & Support (User Side)
# ---------------------------------------------------------------------------

@main_bp.route("/support")
def support():
    """Help & Support center — FAQs, guides, and user's support tickets."""
    user_tickets = []
    if current_user.is_authenticated:
        user_tickets = (
            SupportTicket.query.filter_by(user_id=current_user.id)
            .order_by(SupportTicket.updated_at.desc())
            .all()
        )
    return render_template("support/index.html", tickets=user_tickets, active_nav="support")


@main_bp.route("/support/new", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def new_support_ticket():
    """Create a new support ticket (candidates and recruiters)."""
    import os
    from flask import flash, redirect, url_for
    from app.main.forms import SupportTicketForm
    from app.models.notification import Notification
    from app.utils.file_security import generate_secure_stored_filename, inspect_file_magic, FileValidationError

    if not current_user.is_authenticated:
        flash("Please log in to submit a support ticket.", "info")
        return redirect(url_for("auth.login"))

    form = SupportTicketForm()
    if form.validate_on_submit():
        attachment_filename = None
        if form.attachment.data:
            uploaded = form.attachment.data
            try:
                inspect_file_magic(uploaded.stream, uploaded.filename, allowed_category="support_attachment")
            except FileValidationError as e:
                flash(f"Attachment rejected: {str(e)}", "error")
                return render_template("support/new_ticket.html", form=form, active_nav="support")
            clean_name = generate_secure_stored_filename(uploaded.filename)
            support_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "support")
            os.makedirs(support_dir, exist_ok=True)
            uploaded.save(os.path.join(support_dir, clean_name))
            attachment_filename = clean_name

        ticket = SupportTicket(
            user_id=current_user.id,
            issue_type=form.issue_type.data,
            subject=form.subject.data.strip(),
            description=form.description.data.strip(),
            attachment_filename=attachment_filename,
            status=SupportTicket.STATUS_OPEN,
        )
        db.session.add(ticket)
        db.session.flush()

        # Add initial message to the ticket conversation thread
        initial_msg = SupportTicketMessage(
            ticket_id=ticket.id,
            sender_id=current_user.id,
            message=ticket.description,
            attachment_filename=attachment_filename,
            is_admin_response=False,
        )
        db.session.add(initial_msg)

        # Notify administrators through existing notification engine
        user_role_label = (
            "Candidate"
            if current_user.is_candidate
            else ("Recruiter" if current_user.is_recruiter else "User")
        )
        Notification.notify_admins(
            title=f"New Support Ticket #{ticket.id}",
            message=f"{current_user.full_name} ({user_role_label}) opened ticket: '{ticket.subject[:60]}'.",
            link=url_for("admin.support_ticket_detail", ticket_id=ticket.id),
        )

        db.session.commit()
        flash("Your support ticket has been submitted. Our team will get back to you shortly.", "success")
        return redirect(url_for("main.support_ticket_detail", ticket_id=ticket.id))

    return render_template("support/new_ticket.html", form=form, active_nav="support")


@main_bp.route("/support/<int:ticket_id>", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def support_ticket_detail(ticket_id):
    """View a support ticket thread and post follow-up replies."""
    import os
    from flask import flash, redirect, url_for
    from app.main.forms import SupportReplyForm
    from app.models.notification import Notification
    from app.utils.file_security import generate_secure_stored_filename, inspect_file_magic, FileValidationError

    if not current_user.is_authenticated:
        flash("Please log in to view this support ticket.", "info")
        return redirect(url_for("auth.login"))

    ticket = SupportTicket.query.get_or_404(ticket_id)

    # Strict authorization / IDOR isolation: users can only see their own tickets
    if not (current_user.is_admin or ticket.user_id == current_user.id):
        abort(404)

    form = SupportReplyForm()
    if form.validate_on_submit():
        attachment_filename = None
        if form.attachment.data:
            uploaded = form.attachment.data
            try:
                inspect_file_magic(uploaded.stream, uploaded.filename, allowed_category="support_attachment")
            except FileValidationError as e:
                flash(f"Attachment rejected: {str(e)}", "error")
                return render_template("support/ticket_detail.html", ticket=ticket, form=form, active_nav="support")
            clean_name = generate_secure_stored_filename(uploaded.filename)
            support_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "support")
            os.makedirs(support_dir, exist_ok=True)
            uploaded.save(os.path.join(support_dir, clean_name))
            attachment_filename = clean_name

        reply = SupportTicketMessage(
            ticket_id=ticket.id,
            sender_id=current_user.id,
            message=form.message.data.strip(),
            attachment_filename=attachment_filename,
            is_admin_response=False,
        )
        db.session.add(reply)

        # Reopen ticket if user responds to a resolved ticket
        if ticket.status == SupportTicket.STATUS_RESOLVED:
            ticket.status = SupportTicket.STATUS_OPEN
        ticket.updated_at = utcnow()

        # Notify admins
        Notification.notify_admins(
            title=f"New Reply on Ticket #{ticket.id}",
            message=f"{current_user.full_name} replied to ticket: '{ticket.subject[:50]}'.",
            link=url_for("admin.support_ticket_detail", ticket_id=ticket.id),
        )

        db.session.commit()
        flash("Your reply has been sent.", "success")
        return redirect(url_for("main.support_ticket_detail", ticket_id=ticket.id))

    return render_template("support/ticket_detail.html", ticket=ticket, form=form, active_nav="support")


@main_bp.route("/support/attachment/<filename>")
def support_attachment(filename):
    """Safely stream/download support ticket attachments."""
    import os
    from flask import send_from_directory
    from werkzeug.utils import secure_filename

    if not current_user.is_authenticated:
        abort(403)

    safe_name = secure_filename(filename)
    # Validate user is owner of the ticket or admin
    ticket = SupportTicket.query.filter(
        (SupportTicket.attachment_filename == safe_name)
    ).first()
    if not ticket:
        # Check messages
        msg = SupportTicketMessage.query.filter_by(attachment_filename=safe_name).first()
        if msg:
            ticket = msg.ticket

    if not ticket or not (current_user.is_admin or ticket.user_id == current_user.id):
        abort(404)

    support_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "support")
    return send_from_directory(support_dir, safe_name)


# /seed-demo removed: unauthenticated route that reset hardcoded admin credentials (security audit finding #1).
# Use `python seed_microsoft.py` from the command line in dev/staging only.
