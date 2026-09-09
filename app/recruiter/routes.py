import re
from datetime import timedelta, datetime
from app.utils.time import utcnow
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, abort
from flask_login import login_user, login_required, current_user
from sqlalchemy import func, or_, and_

from app import db
from app.models.user import User
from app.models.recruiter_profile import RecruiterProfile
from app.models.job import Job
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.notification import Notification
from app.models.resume import Resume
from app.recruiter.forms import RecruiterRegistrationForm, JobPostForm
from app.utils.decorators import role_required, approved_recruiter_required
from app.utils.security import limiter, clean_profile_field, clean_website_url
from app.ml.extractor import (
    extract_structured_jd,
    extract_structured_resume,
    clean_text,
)
from app.ml.ats_scorer import score_resume_for_job, structured_jd_for_job
from app.ai import ai_service

recruiter_bp = Blueprint("recruiter", __name__, template_folder="../templates/recruiter")

# Score thresholds used to flag/auto-group candidates. Centralized so the
# meaning of "top match" or "shortlist-worthy" stays consistent everywhere
# it's used instead of drifting as separate hardcoded numbers per page.
TOP_MATCH_SCORE_THRESHOLD = 80   # Dashboard "top-tier match" alert
SHORTLIST_SCORE_THRESHOLD = 40   # Auto-include on the Shortlist page
COMPARE_DEFAULT_TOP_N = 3        # How many candidates Compare preselects by default

# Allowed application status transitions, keyed by current status. Shared by
# both the single-candidate status control and the bulk-action endpoint so a
# recruiter can't reach an invalid transition (e.g. Applied -> Hired) just by
# using the bulk buttons instead of the single one.
APPLICATION_VALID_TRANSITIONS = {
    Application.STATUS_APPLIED: [Application.STATUS_UNDER_REVIEW, Application.STATUS_SHORTLISTED, Application.STATUS_REJECTED],
    Application.STATUS_UNDER_REVIEW: [Application.STATUS_SHORTLISTED, Application.STATUS_INTERVIEW, Application.STATUS_REJECTED],
    Application.STATUS_SHORTLISTED: [Application.STATUS_INTERVIEW, Application.STATUS_HIRED, Application.STATUS_REJECTED],
    Application.STATUS_INTERVIEW: [Application.STATUS_HIRED, Application.STATUS_SHORTLISTED, Application.STATUS_REJECTED],
    Application.STATUS_REJECTED: [Application.STATUS_APPLIED, Application.STATUS_UNDER_REVIEW, Application.STATUS_SHORTLISTED, Application.STATUS_INTERVIEW],
    Application.STATUS_HIRED: [Application.STATUS_REJECTED],
}


def _clean_profile_field(value, max_len):
    return clean_profile_field(value, max_len)


def _clean_website_url(value):
    return clean_website_url(value)


# Job fields that feed into scoring (see ats_scorer.build_jd_text /
# job_skill_lists — every one of these ends up in the JD text or the
# explicit skill lists passed to score_resume_for_job). edit_job() diffs
# against this set to decide whether an edit needs to re-score the job's
# applications; keep it in sync with build_jd_text/job_skill_lists if either
# changes which Job attributes it reads.
SCORE_RELEVANT_JOB_FIELDS = (
    "title", "description", "responsibilities", "requirements",
    "required_skills_raw", "preferred_skills_raw",
)


def _structured_resume_evidence(application):
    """Shared, per-application structured-resume extraction used by both
    applicants() and compare_candidates(). Both routes independently ran
    extract_structured_resume() on the same application's resume and then
    pulled the same four things back out of it (skills set, years of
    experience, seniority, degrees) before going on to format them very
    differently for their own templates (capped match-evidence text vs. a
    side-by-side skills_map table) — this only covers the shared
    extraction step; each caller still does its own JD-skill-list diffing
    and presentation formatting, since those genuinely differ per view.
    """
    resume_text = application.resume.raw_text if application.resume else ""
    structured_resume = extract_structured_resume(resume_text)
    return {
        "structured_resume": structured_resume,
        "res_skills": structured_resume.get("skills_set") or set(structured_resume.get("skills", [])),
        "res_years": structured_resume.get("experience", {}).get("min_years"),
        "res_seniority": structured_resume.get("experience", {}).get("seniority", "mid"),
        "res_degrees": structured_resume.get("education", {}).get("degrees", []),
        "has_education": bool(structured_resume.get("education", {}).get("has_education")),
    }


def _match_label(score):
    """Bucket a match score into the Strong/Good/Low label used on pipeline
    cards. Kept in one place so the cutoffs can't drift between stages."""
    score = score or 0
    if score >= TOP_MATCH_SCORE_THRESHOLD:
        return "Strong"
    if score >= 50:
        return "Good"
    return "Low"


def _latest_event_at(application, status):
    """Timestamp of the most recent ApplicationEvent with the given status,
    used to show e.g. 'Shortlisted 2 days ago' / hire date / rejection date
    without needing a dedicated column for every stage transition."""
    event = next((e for e in reversed(application.events) if e.status == status), None)
    return event.created_at if event else None


def _status_before_latest(application):
    """The status the application was in immediately before its current
    (most recent) status — used to show 'Stage: Under Review' on a
    Rejected card, and by the Undo action to know what to revert to."""
    events = application.events  # ordered oldest -> newest
    if len(events) >= 2:
        return events[-2].status
    return Application.STATUS_APPLIED



def _shortlist_source(application):
    """Determine whether a candidate was shortlisted manually by a recruiter
    or automatically by the AI auto-shortlist action, by inspecting the
    most recent 'shortlisted' ApplicationEvent note.

    Returns a dict with:
      'label'  — short display string ('AI Auto-Shortlisted' | 'Manually Shortlisted')
      'is_auto' — bool
    """
    shortlist_event = next(
        (e for e in reversed(application.events)
         if e.status == Application.STATUS_SHORTLISTED),
        None,
    )
    is_auto = bool(
        shortlist_event and shortlist_event.note
        and shortlist_event.note.startswith("Auto-shortlisted by AI")
    )
    return {
        "is_auto": is_auto,
        "label": "AI Auto-Shortlisted" if is_auto else "Manually Shortlisted",
    }


def _pipeline_card_context(application):
    """Build the extra per-card fields the pipeline templates need, on top
    of the raw Application row: match label, JD skill match evidence,
    and stage-specific dates pulled from the event history."""
    ctx = {
        "match_label": _match_label(application.match_score),
    }
    if application.status in (Application.STATUS_APPLIED, Application.STATUS_UNDER_REVIEW):
        evidence = _structured_resume_evidence(application)
        ctx["res_years"] = evidence["res_years"]
        ctx["res_skills_top"] = list(evidence["res_skills"])[:4]
        if application.status == Application.STATUS_UNDER_REVIEW and application.job:
            structured_jd = structured_jd_for_job(application.job)
            jd_req_skills = structured_jd.get("required_skills", []) or structured_jd.get("technical_skills", [])
            res_skills = evidence["res_skills"]
            ctx["matched_count"] = len([s for s in jd_req_skills if s in res_skills])
            ctx["missing_count"] = len([s for s in jd_req_skills if s not in res_skills])
    if application.status == Application.STATUS_SHORTLISTED:
        ctx["shortlisted_at"] = _latest_event_at(application, Application.STATUS_SHORTLISTED)
    if application.status == Application.STATUS_INTERVIEW:
        ctx["is_upcoming"] = bool(
            application.interview_date and application.interview_date >= utcnow() and not application.interview_completed
        )
    if application.status == Application.STATUS_HIRED:
        ctx["hired_at"] = _latest_event_at(application, Application.STATUS_HIRED)
    if application.status == Application.STATUS_REJECTED:
        ctx["rejected_at"] = _latest_event_at(application, Application.STATUS_REJECTED)
        ctx["rejected_from_status"] = _status_before_latest(application)
    return ctx


def _auto_close_other_applications_on_hire(hired_application):
    """When a candidate is hired for one job, auto-close their other
    still-open applications — across every job, including other
    recruiters', since a hired candidate is off the market everywhere,
    not just in this recruiter's pipeline. "Open" means not already
    hired or rejected.
    """
    other_open_apps = Application.query.filter(
        Application.candidate_id == hired_application.candidate_id,
        Application.id != hired_application.id,
        Application.status.notin_([Application.STATUS_HIRED, Application.STATUS_REJECTED]),
    ).all()
    for other in other_open_apps:
        prev_status = other.status
        other.status = Application.STATUS_REJECTED
        db.session.add(ApplicationEvent(
            application_id=other.id,
            status=Application.STATUS_REJECTED,
            note=f"Automatically closed from stage '{prev_status}' — candidate was hired for a different role (Job #{hired_application.job_id}).",
        ))
        db.session.add(Notification(
            candidate_id=other.candidate_id,
            title=f"Application closed: {other.job.title}",
            message="This application was automatically closed because you were hired for a different role.",
            link=f"/candidate/applications/{other.id}",
        ))


def _reopen_auto_closed_applications_on_unhire(unhired_application):
    """If a candidate's HIRED status is reversed (HIRED -> REJECTED), restore any other
    applications that were auto-closed solely due to this hire to their previous stage.

    Previously this searched only the *most recent* event, which meant that any
    recruiter note added after the auto-close event would break the string-match
    and silently leave the application rejected forever.  Now we search *all*
    events for the application and find the correct auto-close event regardless
    of what happened after it.
    """
    tag = f"(Job #{unhired_application.job_id})"
    other_rejected_apps = Application.query.filter(
        Application.candidate_id == unhired_application.candidate_id,
        Application.id != unhired_application.id,
        Application.status == Application.STATUS_REJECTED,
    ).all()

    for other in other_rejected_apps:
        # Search all events for this application for the specific auto-close note
        # from this hire, rather than assuming it is the most recent event.
        auto_close_event = (
            ApplicationEvent.query
            .filter(
                ApplicationEvent.application_id == other.id,
                ApplicationEvent.note.like("Automatically closed from stage %"),
                ApplicationEvent.note.like(f"%{tag}%"),
            )
            .order_by(ApplicationEvent.created_at.desc())
            .first()
        )

        if auto_close_event:
            match = re.search(r"Automatically closed from stage '([^']+)'", auto_close_event.note)
            target_status = match.group(1) if match and match.group(1) in Application.STATUSES else Application.STATUS_APPLIED
            other.status = target_status
            db.session.add(ApplicationEvent(
                application_id=other.id,
                status=target_status,
                note=f"Automatically reopened to '{target_status}' after prior hire was reversed.",
            ))
            db.session.add(Notification(
                candidate_id=other.candidate_id,
                title=f"Application reopened: {other.job.title}",
                message=f"Your application for {other.job.title} has been reopened at stage '{target_status.replace('_', ' ').title()}'.",
                link=f"/candidate/applications/{other.id}",
            ))


def refresh_match_scores(applications):
    """Recompute match_score for a list of Application rows using the same
    canonical scorer everywhere (score_resume_for_job), and persist any that
    drifted from the stored value.

    As of the recompute-on-write migration, match_score is kept fresh at the
    source (apply-time, and targeted re-scores from edit_job()/resume edits)
    instead of being recomputed on every page read. This function is no
    longer called from any read route — it's kept as a narrow-scope repair
    utility (see admin.backfill_match_scores) for backfilling rows that
    predate scored_at, or recovering from any scorer/data drift.
    """
    changed = False
    for app in applications:
        if not app.job:
            continue
        # Fall back to the candidate's free-text profile experience when no
        # resume file is on file — matches the fallback candidate_intelligence()
        # uses for its live score, so a resume-less candidate doesn't show a
        # score there but a stale/None score on every list page.
        if app.resume and app.resume.raw_text:
            resume_text = app.resume.raw_text
        else:
            resume_text = (app.candidate.experience or "") if app.candidate else ""
        if not resume_text:
            continue
        fresh_score = score_resume_for_job(resume_text, app.job)["score"]
        if app.match_score != fresh_score:
            app.match_score = fresh_score
            app.scored_at = utcnow()
            changed = True
    if changed:
        db.session.commit()


def _rescore_applications_for_job(job):
    """Re-score active Application rows for `job` after one of its scoring inputs
    (SCORE_RELEVANT_JOB_FIELDS) changed. Decided applications (HIRED, REJECTED)
    preserve their historical scores.
    """
    apps = Application.query.filter(
        Application.job_id == job.id,
        Application.status.notin_([Application.STATUS_HIRED, Application.STATUS_REJECTED]),
    ).all()
    if not apps:
        return
    now = utcnow()
    for application in apps:
        if application.resume and application.resume.raw_text:
            resume_text = application.resume.raw_text
        else:
            resume_text = (application.candidate.experience or "") if application.candidate else ""
        if not resume_text:
            continue
        fresh_score = score_resume_for_job(resume_text, job)["score"]
        application.match_score = fresh_score
        application.scored_at = now
    db.session.commit()


@recruiter_bp.route("/register", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTH", "5 per minute; 20 per hour"))
def register():
    form = RecruiterRegistrationForm()

    if form.validate_on_submit():
        from app.models.admin_setting import AdminSetting
        if AdminSetting.get("recruiter_registration_open", "true").lower() == "false":
            flash("Recruiter registration is currently disabled by administrator.", "error")
            return render_template("recruiters.html", form=form)

        existing = User.query.filter_by(email=form.work_email.data.lower().strip()).first()
        if existing:
            flash("An account with this work email already exists.", "error")
            return render_template("recruiters.html", form=form)

        user = User(
            full_name=form.contact_name.data.strip(),
            email=form.work_email.data.lower().strip(),
            role=User.ROLE_RECRUITER,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()

        auto_approve = AdminSetting.get("auto_approve_recruiters", "false").lower() == "true"
        status = RecruiterProfile.STATUS_APPROVED if auto_approve else RecruiterProfile.STATUS_PENDING

        profile = RecruiterProfile(
            user_id=user.id,
            company_name=form.company_name.data.strip(),
            industry=form.industry.data,
            company_size=form.company_size.data,
            company_website=form.company_website.data,
            contact_role=form.contact_role.data,
            phone=form.phone.data,
            hiring_needs=form.hiring_needs.data,
            approval_status=status,
            reviewed_at=utcnow() if auto_approve else None,
        )
        db.session.add(profile)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("An account with this work email already exists.", "error")
            return render_template("recruiters.html", form=form)

        # Notify administrators about new recruiter registration
        Notification.notify_admins(
            title=f"New Recruiter Registration: {profile.company_name}",
            message=f"{user.full_name} ({user.email}) registered {profile.company_name} (Status: {status.capitalize()}).",
            link=url_for("admin.recruiter_detail", profile_id=profile.id),
        )
        db.session.commit()

        login_user(user)
        if auto_approve:
            flash("Welcome to Zentra! Your company account has been auto-approved and you can post jobs immediately.", "success")
        else:
            flash(
                "Thanks — your company is submitted for review. "
                "You'll be able to post jobs once an admin approves your account.",
                "success",
            )
        return redirect(url_for("recruiter.dashboard"))

    return render_template("recruiters.html", form=form)


# ----------------------------------------------------------------------
# 1. DASHBOARD
# ----------------------------------------------------------------------
@recruiter_bp.route("/dashboard")
@role_required("recruiter")
def dashboard():
    profile = current_user.recruiter_profile
    jobs = []
    active_jobs_count = 0
    total_applicants_count = 0
    shortlisted_count = 0
    interviews_count = 0
    recent_activity = []
    hiring_alerts = []

    if profile and profile.approval_status == RecruiterProfile.STATUS_APPROVED:
        jobs = Job.query.filter_by(recruiter_profile_id=profile.id).order_by(Job.created_at.desc()).all()
        active_jobs_count = sum(1 for j in jobs if j.status == Job.STATUS_ACTIVE)
        
        job_ids = [j.id for j in jobs]
        if job_ids:
            all_apps = Application.query.filter(Application.job_id.in_(job_ids)).all()
            total_applicants_count = len(all_apps)
            shortlisted_count = sum(1 for a in all_apps if a.status == Application.STATUS_SHORTLISTED)
            interviews_count = sum(1 for a in all_apps if a.status == Application.STATUS_INTERVIEW)
            
            # Recent application events / applications
            recent_activity = (
                Application.query.filter(Application.job_id.in_(job_ids))
                .order_by(Application.applied_at.desc())
                .limit(6)
                .all()
            )
            
            unreviewed = [a for a in all_apps if a.status == Application.STATUS_APPLIED]
            if unreviewed:
                hiring_alerts.append({
                    "type": "info",
                    "title": f"{len(unreviewed)} new application{'s' if len(unreviewed) != 1 else ''} awaiting review",
                    "desc": "Check your incoming candidate applications and AI match scores.",
                    "link": url_for("recruiter.candidates"),
                })
            
            top_matches = [a for a in all_apps if a.status == Application.STATUS_APPLIED and (a.match_score or 0) >= TOP_MATCH_SCORE_THRESHOLD]
            if top_matches:
                hiring_alerts.append({
                    "type": "success",
                    "title": f"{len(top_matches)} top-tier match candidate{'s' if len(top_matches) != 1 else ''} ({TOP_MATCH_SCORE_THRESHOLD}%+ score)",
                    "desc": "High AI relevance candidate matches are waiting in your pipeline.",
                    "link": url_for("recruiter.pipeline"),
                })

    return render_template(
        "recruiter/dashboard.html",
        profile=profile,
        jobs=jobs,
        active_jobs_count=active_jobs_count,
        total_applicants_count=total_applicants_count,
        shortlisted_count=shortlisted_count,
        interviews_count=interviews_count,
        recent_activity=recent_activity,
        hiring_alerts=hiring_alerts,
        active_nav="dashboard",
    )


# ----------------------------------------------------------------------
# 2. JOBS (Workspace & AI Analysis)
# ----------------------------------------------------------------------
@recruiter_bp.route("/jobs")
@role_required("recruiter")
def jobs():
    profile = current_user.recruiter_profile
    if not profile or profile.approval_status != RecruiterProfile.STATUS_APPROVED:
        return redirect(url_for("recruiter.dashboard"))

    status_filter = request.args.get("status", "all").lower().strip()
    q = request.args.get("q", "").strip()

    query = Job.query.filter_by(recruiter_profile_id=profile.id)
    if status_filter in [Job.STATUS_ACTIVE, Job.STATUS_DRAFT, Job.STATUS_PAUSED, Job.STATUS_CLOSED]:
        query = query.filter_by(status=status_filter)

    if q:
        query = query.filter(
            or_(
                Job.title.ilike(f"%{q}%"),
                Job.location.ilike(f"%{q}%"),
                Job.description.ilike(f"%{q}%"),
            )
        )

    all_jobs = query.order_by(Job.created_at.desc()).all()
    
    counts = {
        "all": Job.query.filter_by(recruiter_profile_id=profile.id).count(),
        "active": Job.query.filter_by(recruiter_profile_id=profile.id, status=Job.STATUS_ACTIVE).count(),
        "draft": Job.query.filter_by(recruiter_profile_id=profile.id, status=Job.STATUS_DRAFT).count(),
        "paused": Job.query.filter_by(recruiter_profile_id=profile.id, status=Job.STATUS_PAUSED).count(),
        "closed": Job.query.filter_by(recruiter_profile_id=profile.id, status=Job.STATUS_CLOSED).count(),
    }

    # Attach stats for each job
    job_cards = []
    for j in all_jobs:
        apps = j.applications
        job_cards.append({
            "job": j,
            "total_applicants": len(apps),
            "shortlisted": sum(1 for a in apps if a.status == Application.STATUS_SHORTLISTED),
            "interviews": sum(1 for a in apps if a.status == Application.STATUS_INTERVIEW),
            "hired": sum(1 for a in apps if a.status == Application.STATUS_HIRED),
            "avg_match": round(sum(a.match_score for a in apps if a.match_score) / len(apps), 1) if apps else None,
        })

    return render_template(
        "recruiter/jobs.html",
        job_cards=job_cards,
        status_filter=status_filter,
        q=q,
        counts=counts,
        active_nav="jobs",
    )


@recruiter_bp.route("/jobs/new", methods=["GET", "POST"])
@approved_recruiter_required
def new_job():
    form = JobPostForm()
    if form.validate_on_submit():
        job_status = form.status.data if hasattr(form, "status") and form.status.data in Job.STATUSES else Job.STATUS_ACTIVE
        job = Job(
            recruiter_profile_id=current_user.recruiter_profile.id,
            title=form.title.data.strip(),
            description=form.description.data,
            responsibilities=form.responsibilities.data,
            requirements=form.requirements.data,
            required_skills_raw=form.required_skills_raw.data.strip(),
            preferred_skills_raw=(form.preferred_skills_raw.data or "").strip(),
            job_type=form.job_type.data,
            work_mode=form.work_mode.data,
            experience_level=form.experience_level.data,
            location=form.location.data,
            salary_min=_safe_int(form.salary_min.data),
            salary_max=_safe_int(form.salary_max.data),
            status=job_status,
            application_deadline=form.application_deadline.data,
        )
        db.session.add(job)
        db.session.commit()

        # Notify administrators about the new job posting
        comp_name = current_user.recruiter_profile.company_name if current_user.recruiter_profile else "A recruiter"
        Notification.notify_admins(
            title=f"New Job Posted: {job.title}",
            message=f"{comp_name} published a new job opening for '{job.title}'.",
            link=url_for("admin.job_detail", job_id=job.id),
        )
        db.session.commit()

        flash(f"Job saved as '{job_status.capitalize()}'. Zentra AI has analyzed the requirements.", "success")
        return redirect(url_for("recruiter.job_overview", job_id=job.id))
    from datetime import datetime
    return render_template("recruiter/job_form.html", form=form, active_nav="jobs", now=utcnow())


@recruiter_bp.route("/jobs/<int:job_id>/status", methods=["POST"])
@approved_recruiter_required
def update_job_status(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()
    
    new_status = request.form.get("status", "").strip().lower()
    return_to = request.form.get("return_to", "jobs")
    
    if new_status in Job.STATUSES:
        job.status = new_status
        db.session.commit()
        flash(f"Job status updated to '{new_status.capitalize()}'.", "success")
    else:
        flash("Invalid job status specified.", "error")
        
    if return_to == "overview":
        return redirect(url_for("recruiter.job_overview", job_id=job.id))
    return redirect(url_for("recruiter.jobs"))


@recruiter_bp.route("/jobs/<int:job_id>/overview")
@approved_recruiter_required
def job_overview(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()
    
    apps = job.applications
    total_applicants = len(apps)
    
    progress = {
        "applied": sum(1 for a in apps if a.status == Application.STATUS_APPLIED),
        "under_review": sum(1 for a in apps if a.status == Application.STATUS_UNDER_REVIEW),
        "shortlisted": sum(1 for a in apps if a.status == Application.STATUS_SHORTLISTED),
        "interview": sum(1 for a in apps if a.status == Application.STATUS_INTERVIEW),
        "hired": sum(1 for a in apps if a.status == Application.STATUS_HIRED),
        "rejected": sum(1 for a in apps if a.status == Application.STATUS_REJECTED),
    }
    
    # Run structured JD analysis with ATS extractor engine
    structured_jd = structured_jd_for_job(job)

    top_candidates = sorted(apps, key=lambda a: a.match_score or 0, reverse=True)[:5]

    return render_template(
        "recruiter/job_overview.html",
        job=job,
        structured_jd=structured_jd,
        total_applicants=total_applicants,
        progress=progress,
        shortlisted=progress["shortlisted"],
        interviews=progress["interview"],
        hired=progress["hired"],
        top_candidates=top_candidates,
        active_nav="jobs",
        active_tab="overview",
    )


@recruiter_bp.route("/jobs/<int:job_id>/applicants")
@approved_recruiter_required
def applicants(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()

    apps = (
        Application.query.filter_by(job_id=job.id)
        .order_by(Application.match_score.desc())
        .all()
    )
    if apps:
        refresh_match_scores(apps)

    # Paginate BEFORE running AI evidence extraction — that extraction is
    # per-candidate NLP work, so once a job has hundreds of applicants we
    # only want to pay that cost for the page actually being viewed.
    page = request.args.get("page", 1, type=int)
    per_page = 15
    total_apps = len(apps)
    total_pages = max(1, (total_apps + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    paged_apps = apps[start:start + per_page]

    structured_jd = structured_jd_for_job(job)
    jd_req_skills = structured_jd.get("required_skills", []) or structured_jd.get("technical_skills", [])
    jd_min_years = structured_jd.get("experience", {}).get("min_years")
    jd_degrees = structured_jd.get("education", {}).get("degrees", [])

    # Enrich each application with deep matching evidence
    app_evidence = []
    for app in paged_apps:
        evidence = _structured_resume_evidence(app)
        res_skills = evidence["res_skills"]

        matched_skills = [s for s in jd_req_skills if s in res_skills]
        missing_skills = [s for s in jd_req_skills if s not in res_skills]

        res_years = evidence["res_years"]
        if res_years is not None and jd_min_years is not None:
            exp_match_text = f"{res_years:g} yrs / {jd_min_years:g}+ yrs req"
            exp_matched = res_years >= jd_min_years
        elif res_years is not None:
            exp_match_text = f"{res_years:g} yrs experience"
            exp_matched = True
        else:
            exp_match_text = f"{job.experience_level.capitalize()} level"
            exp_matched = True

        res_degs = evidence["res_degrees"]
        if res_degs:
            edu_text = f"{res_degs[0]} degree"
            edu_matched = True
        else:
            edu_text = "Education listed" if evidence["has_education"] else "No degree listed"
            edu_matched = evidence["has_education"]

        app_evidence.append({
            "application": app,
            "matched_skills": matched_skills[:5],
            "missing_skills": missing_skills[:4],
            "exp_match_text": exp_match_text,
            "exp_matched": exp_matched,
            "edu_text": edu_text,
            "edu_matched": edu_matched,
            "score": app.match_score or 0,
        })

    return render_template(
        "recruiter/applicants.html",
        job=job,
        applications=apps,
        app_evidence=app_evidence,
        page=page,
        total_pages=total_pages,
        total_apps=total_apps,
        active_nav="jobs",
        active_tab="applicants",
    )


@recruiter_bp.route("/jobs/<int:job_id>/applicants/bulk-status", methods=["POST"])
@approved_recruiter_required
def bulk_update_applicant_status(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()

    new_status = request.form.get("status", "").strip()
    raw_ids = request.form.getlist("application_ids")
    ids_str = request.form.get("application_ids_str", "")

    app_ids = []
    for val in raw_ids:
        if str(val).isdigit():
            app_ids.append(int(val))
    if ids_str:
        for part in ids_str.split(","):
            if part.strip().isdigit():
                app_ids.append(int(part.strip()))

    app_ids = list(set(app_ids))

    if not app_ids:
        flash("No candidates were selected for bulk action.", "error")
        return redirect(url_for("recruiter.applicants", job_id=job.id))

    if new_status not in Application.STATUSES:
        flash("Invalid target status specified.", "error")
        return redirect(url_for("recruiter.applicants", job_id=job.id))

    if job.status == Job.STATUS_CLOSED and new_status != Application.STATUS_REJECTED:
        flash(
            "This job is closed — the only bulk action available now is rejecting remaining "
            "candidates. Reopen the job first to continue processing applicants.",
            "error",
        )
        return redirect(url_for("recruiter.applicants", job_id=job.id))

    apps_to_update = Application.query.filter(
        Application.id.in_(app_ids),
        Application.job_id == job.id,
    ).all()

    # Enforce the same status-transition rules as the single-candidate
    # control — silently skip any selected application that can't legally
    # reach new_status from its current status, rather than forcing it
    # through. This is the same map update_application_status() uses.
    valid_apps = []
    skipped = 0
    for app in apps_to_update:
        if new_status == app.status or new_status in APPLICATION_VALID_TRANSITIONS.get(app.status, []):
            valid_apps.append(app)
        else:
            skipped += 1
    apps_to_update = valid_apps

    if not apps_to_update:
        flash("None of the selected candidates can move to that status from their current stage.", "error")
        return redirect(url_for("recruiter.applicants", job_id=job.id))

    status_label = new_status.replace("_", " ").title()
    for app in apps_to_update:
        old_status = app.status
        app.status = new_status
        db.session.add(ApplicationEvent(
            application_id=app.id,
            status=new_status,
            note=f"Bulk updated to '{status_label}' by recruiter.",
        ))
        db.session.add(Notification(
            candidate_id=app.candidate_id,
            title=f"Application update: {job.title}",
            message=f"Your application status has been updated to '{status_label}'.",
            link=f"/candidate/applications/{app.id}",
        ))
        if new_status == Application.STATUS_HIRED:
            _auto_close_other_applications_on_hire(app)
        elif old_status == Application.STATUS_HIRED and new_status == Application.STATUS_REJECTED:
            _reopen_auto_closed_applications_on_unhire(app)

    db.session.commit()
    msg = f"Successfully updated {len(apps_to_update)} candidate{'s' if len(apps_to_update) != 1 else ''} to '{status_label}'."
    if skipped:
        msg += f" Skipped {skipped} candidate{'s' if skipped != 1 else ''} that can't move to that status from their current stage."
    flash(msg, "success")
    return redirect(url_for("recruiter.applicants", job_id=job.id))


@recruiter_bp.route("/jobs/<int:job_id>/shortlist")
@approved_recruiter_required
def job_shortlist(job_id):
    """Step 3 — AI Review: shows AI-recommended candidates (Applied/Under Review
    scoring >= threshold) awaiting a Shortlist or Reject decision. Does NOT show
    already-shortlisted candidates — those live on the separate Shortlisted page."""
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()

    # Threshold: URL param for preview, else fall back to the job's persisted
    # value so the setting survives page reloads. Clamped to 0-100.
    threshold = request.args.get("threshold", type=float)
    if threshold is None:
        threshold = float(job.shortlist_threshold if job.shortlist_threshold is not None else SHORTLIST_SCORE_THRESHOLD)
    threshold = max(0.0, min(100.0, threshold))

    # Count already-shortlisted for the step-bar badge only — don't render them here.
    shortlisted_count = Application.query.filter_by(
        job_id=job.id, status=Application.STATUS_SHORTLISTED
    ).count()

    # Threshold comparison uses round() — same as every UI card display.
    # Ensures a candidate shown as "40%" is never excluded by a 40% threshold
    # due to raw float drift (e.g. 39.6 < 40.0 even though both round to 40).
    def _rounded_score(a):
        return round(a.match_score or 0)

    # ── AI Recommended — Awaiting Recruiter Review ────────────────────────
    # Only Applied / Under Review candidates scoring >= threshold.
    # Shortlisted, Interview, Hired, Rejected candidates are excluded —
    # they have already been actioned and belong on other pages.
    eligible_apps = sorted(
        [
            a for a in Application.query.filter_by(job_id=job.id)
            .filter(Application.status.in_([
                Application.STATUS_APPLIED, Application.STATUS_UNDER_REVIEW
            ])).all()
            if _rounded_score(a) >= threshold
        ],
        key=lambda a: a.match_score or 0,
        reverse=True,
    )

    auto_eligible_count = len(eligible_apps)

    # Paginate
    page = request.args.get("page", 1, type=int)
    per_page = 15
    total_apps = len(eligible_apps)
    total_pages = max(1, (total_apps + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    paged_apps = eligible_apps[start:start + per_page]

    return render_template(
        "recruiter/job_shortlist.html",
        job=job,
        applications=paged_apps,
        shortlisted_count=shortlisted_count,
        page=page,
        total_pages=total_pages,
        total_apps=total_apps,
        active_nav="jobs",
        active_tab="shortlist",
        threshold=threshold,
        auto_eligible_count=auto_eligible_count,
    )


@recruiter_bp.route("/jobs/<int:job_id>/shortlist/auto", methods=["POST"])
@approved_recruiter_required
def auto_shortlist(job_id):
    """Bulk-move every Applied/Under-Review candidate at or above the given
    match-score threshold into 'shortlisted'.

    Rules enforced:
    - Job must belong to the current recruiter (server-side, not just UI).
    - Only Applied / Under Review candidates are eligible.
    - Comparison uses round(match_score) for consistency with the UI display.
    - Idempotent: already-shortlisted candidates are ignored by the status
      filter, so running twice does not create duplicate events or moves.
    - Manual shortlist decisions are never overwritten.
    - Threshold is persisted on the job after each successful run.
    """
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()

    threshold = request.form.get("threshold", SHORTLIST_SCORE_THRESHOLD, type=float)
    if threshold is None:
        threshold = SHORTLIST_SCORE_THRESHOLD
    threshold = max(0.0, min(100.0, threshold))

    # Only consider Applied / Under Review — already-shortlisted, rejected,
    # interview, and hired candidates are never touched by this action.
    eligible = Application.query.filter_by(job_id=job.id).filter(
        Application.status.in_([Application.STATUS_APPLIED, Application.STATUS_UNDER_REVIEW])
    ).all()

    moved = 0
    for app in eligible:
        if round(app.match_score or 0) >= threshold:
            app.status = Application.STATUS_SHORTLISTED
            db.session.add(ApplicationEvent(
                application_id=app.id,
                status=Application.STATUS_SHORTLISTED,
                # Note prefix must start with "Auto-shortlisted by AI" —
                # _shortlist_source() relies on this prefix to classify origin.
                note=f"Auto-shortlisted by AI (match score {round(app.match_score or 0)}% >= {int(threshold)}% threshold).",
            ))
            db.session.add(Notification(
                candidate_id=app.candidate_id,
                title=f"Application update: {job.title}",
                message=f"Your application has been shortlisted (AI match score: {round(app.match_score or 0)}%).",
                link=f"/candidate/applications/{app.id}",
            ))
            moved += 1

    # Persist the threshold so the next page load remembers the recruiter's
    # chosen setting without needing a URL param.
    job.shortlist_threshold = int(threshold)
    db.session.commit()

    if moved:
        flash(f"Auto-shortlisted {moved} candidate{'s' if moved != 1 else ''} scoring {int(threshold)}% or higher.", "success")
    else:
        flash(f"No Applied/Under Review candidates currently score {int(threshold)}% or higher — threshold saved.", "info")

    # After auto-shortlist, go to the Shortlisted page so the recruiter can
    # immediately see who was just moved.
    return redirect(url_for("recruiter.job_shortlisted", job_id=job.id))


@recruiter_bp.route("/jobs/<int:job_id>/shortlisted")
@approved_recruiter_required
def job_shortlisted(job_id):
    """Step 4 — Shortlisted: shows only confirmed shortlisted candidates.
    No threshold controls, no AI recommendations — this is purely a view of
    who has been shortlisted (manually or via auto-shortlist) and is awaiting
    interview scheduling in the Pipeline."""
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()

    shortlisted_apps = sorted(
        Application.query.filter_by(
            job_id=job.id, status=Application.STATUS_SHORTLISTED
        ).all(),
        key=lambda a: a.match_score or 0,
        reverse=True,
    )

    # Source context (Manual vs AI) from ApplicationEvent history.
    shortlisted_contexts = {a.id: _shortlist_source(a) for a in shortlisted_apps}

    # Pending AI review count — shown in step bar badge on step 3.
    def _rounded_score(a):
        return round(a.match_score or 0)

    threshold = float(
        job.shortlist_threshold if job.shortlist_threshold is not None
        else SHORTLIST_SCORE_THRESHOLD
    )
    pending_review_count = Application.query.filter_by(job_id=job.id).filter(
        Application.status.in_([Application.STATUS_APPLIED, Application.STATUS_UNDER_REVIEW])
    ).count()  # badge-only — not filtered by threshold on this page

    # Paginate
    page = request.args.get("page", 1, type=int)
    per_page = 15
    total = len(shortlisted_apps)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    paged_apps = shortlisted_apps[start:start + per_page]

    return render_template(
        "recruiter/job_shortlisted.html",
        job=job,
        applications=paged_apps,
        shortlisted_contexts=shortlisted_contexts,
        page=page,
        total_pages=total_pages,
        total=total,
        pending_review_count=pending_review_count,
        active_nav="jobs",
        active_tab="shortlisted",
    )


@recruiter_bp.route("/jobs/<int:job_id>/compare")
@approved_recruiter_required
def compare_candidates(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()
    
    # Get selected application IDs from query params (e.g. ?apps=1,2,3)
    apps_param = request.args.get("apps", "")
    app_ids = [int(i.strip()) for i in apps_param.split(",") if i.strip().isdigit()]

    all_job_apps = Application.query.filter_by(job_id=job.id).all()
    all_job_apps = sorted(all_job_apps, key=lambda a: a.match_score or 0, reverse=True)

    if not app_ids:
        # Default to the top N applications by match score
        app_ids = [a.id for a in all_job_apps[:COMPARE_DEFAULT_TOP_N]]

    selected_apps = [a for a in all_job_apps if a.id in app_ids]

    structured_jd = structured_jd_for_job(job)
    all_jd_skills = structured_jd.get("required_skills", []) + structured_jd.get("bonus_skills", [])
    if not all_jd_skills:
        all_jd_skills = structured_jd.get("technical_skills", [])[:10]

    # Build comparison columns
    candidate_cols = []
    for app in selected_apps:
        evidence = _structured_resume_evidence(app)
        res_skills = evidence["res_skills"]

        # Build skills map: skill -> bool (has skill)
        skills_map = {skill: (skill in res_skills) for skill in all_jd_skills}
        matched_count = sum(1 for has in skills_map.values() if has)

        res_years = evidence["res_years"]
        res_seniority = evidence["res_seniority"]
        res_degs = evidence["res_degrees"]

        candidate_cols.append({
            "application": app,
            "candidate": app.candidate,
            "score": app.match_score or 0,
            "skills_map": skills_map,
            "matched_count": matched_count,
            "missing_skills": [s for s in all_jd_skills if s not in res_skills],
            "experience_years": f"{res_years:g} yrs" if res_years else "Not stated",
            "seniority": res_seniority.capitalize(),
            "degrees": ", ".join(res_degs) if res_degs else "General degree",
        })

    return render_template(
        "recruiter/compare.html",
        job=job,
        all_jd_skills=all_jd_skills,
        candidate_cols=candidate_cols,
        all_job_apps=all_job_apps,
        selected_app_ids=app_ids,
        active_nav="jobs",
    )


@recruiter_bp.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@approved_recruiter_required
def edit_job(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()
    form = JobPostForm(obj=job)
    if form.validate_on_submit():
        # Snapshot the scoring-relevant fields before mutating, so we can
        # tell after the fact whether this edit needs to re-score the job's
        # applications (see SCORE_RELEVANT_JOB_FIELDS).
        before = {field: getattr(job, field) for field in SCORE_RELEVANT_JOB_FIELDS}

        job.title = form.title.data.strip()
        job.description = form.description.data
        job.responsibilities = form.responsibilities.data
        job.requirements = form.requirements.data
        job.required_skills_raw = form.required_skills_raw.data.strip()
        job.preferred_skills_raw = (form.preferred_skills_raw.data or "").strip()
        job.job_type = form.job_type.data
        job.work_mode = form.work_mode.data
        job.experience_level = form.experience_level.data
        job.location = form.location.data
        job.salary_min = _safe_int(form.salary_min.data)
        job.salary_max = _safe_int(form.salary_max.data)
        if form.application_deadline.data:
            job.application_deadline = form.application_deadline.data

        needs_rescore = any(getattr(job, field) != before[field] for field in SCORE_RELEVANT_JOB_FIELDS)
        db.session.commit()
        if needs_rescore:
            _rescore_applications_for_job(job)
        flash("Job updated.", "success")
        return redirect(url_for("recruiter.jobs"))
    return render_template("recruiter/job_form.html", form=form, editing=True, job=job, active_nav="jobs", now=utcnow())


@recruiter_bp.route("/jobs/<int:job_id>/close", methods=["POST"])
@approved_recruiter_required
def close_job(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()
    job.status = Job.STATUS_CLOSED
    db.session.commit()
    flash("Job closed — it will no longer appear in job search.", "info")
    return redirect(url_for("recruiter.jobs"))


@recruiter_bp.route("/jobs/<int:job_id>/reopen", methods=["POST"])
@approved_recruiter_required
def reopen_job(job_id):
    job = Job.query.filter_by(
        id=job_id, recruiter_profile_id=current_user.recruiter_profile.id
    ).first_or_404()
    job.status = Job.STATUS_ACTIVE
    db.session.commit()
    if job.is_deadline_passed:
        # Reopening only flips status - it doesn't touch application_deadline.
        # A job whose deadline already passed will show "Active" here but
        # candidate-side still blocks new applications against that same
        # deadline, so tell the recruiter now instead of leaving them to
        # wonder why applicants aren't coming in.
        flash(
            "Job reopened, but its application deadline has already passed — "
            "candidates won't be able to apply until you edit the job and set a new deadline.",
            "warning",
        )
    else:
        flash("Job reopened — it is live again on the jobs board.", "success")
    return redirect(url_for("recruiter.jobs"))


# ----------------------------------------------------------------------
# 3. CANDIDATES & CANDIDATE INTELLIGENCE
# ----------------------------------------------------------------------
@recruiter_bp.route("/candidates")
@role_required("recruiter")
def candidates():
    profile = current_user.recruiter_profile
    if not profile or profile.approval_status != RecruiterProfile.STATUS_APPROVED:
        return redirect(url_for("recruiter.dashboard"))

    q = request.args.get("q", "").strip()
    job_id_filter = request.args.get("job_id", "").strip()
    exp_filter = request.args.get("experience", "").strip()
    min_score = request.args.get("score", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 12

    recruiter_jobs = Job.query.filter_by(recruiter_profile_id=profile.id).order_by(Job.title).all()
    job_ids = [j.id for j in recruiter_jobs]

    # If a specific posting is picked from the "Posted Jobs" dropdown, only
    # ever consider that one job for matching/scoring/filtering below —
    # everywhere else in this function that used the full job_ids list.
    scoped_job_id = None
    if job_id_filter:
        try:
            scoped_job_id = int(job_id_filter)
        except ValueError:
            scoped_job_id = None
        if scoped_job_id not in job_ids:
            scoped_job_id = None  # ignore a job_id that isn't this recruiter's

    active_job_ids = [scoped_job_id] if scoped_job_id else job_ids

    candidates_query = User.query.filter_by(role=User.ROLE_CANDIDATE, is_active_account=True)

    # This page is the recruiter's applicant list, not the whole platform's
    # candidate pool — only show people who actually applied to one of this
    # recruiter's own jobs (or, if a job is picked from the dropdown, only
    # people who applied to that specific job).
    if active_job_ids:
        applied_cand_ids_subquery = (
            db.session.query(Application.candidate_id)
            .filter(Application.job_id.in_(active_job_ids))
            .distinct()
            .subquery()
        )
        candidates_query = candidates_query.filter(User.id.in_(applied_cand_ids_subquery))
    else:
        candidates_query = candidates_query.filter(User.id == -1)

    if q:
        candidates_query = candidates_query.filter(
            or_(
                User.full_name.ilike(f"%{q}%"),
                User.headline.ilike(f"%{q}%"),
                User.skills.ilike(f"%{q}%"),
                User.location.ilike(f"%{q}%"),
            )
        )
    if exp_filter:
        candidates_query = candidates_query.filter_by(experience_level=exp_filter)

    candidates_list = candidates_query.all()

    # Re-sync match scores to ensure candidate cards always reflect true live ATS suitability
    if active_job_ids and candidates_list:
        all_cand_apps = []
        for cand in candidates_list:
            all_cand_apps.extend([a for a in cand.applications if a.job_id in active_job_ids])
        if all_cand_apps:
            refresh_match_scores(all_cand_apps)

    candidate_records = []
    for cand in candidates_list:
        # Scoped to the picked job when one is selected, otherwise every
        # application the candidate has with this recruiter — this is what
        # makes the dropdown actually filter to what's *relevant* to that
        # job rather than just narrowing who's in the list.
        cand_apps = [a for a in cand.applications if a.job_id in active_job_ids] if active_job_ids else []
        best_score = max([a.match_score for a in cand_apps if a.match_score is not None], default=None)
        skills = [s.strip() for s in (cand.skills or "").split(",") if s.strip()]

        candidate_records.append({
            "user": cand,
            "skills": skills[:6],
            "all_skills": skills,
            "applications": cand_apps,
            "applied_job_titles": [a.job.title for a in cand_apps],
            "best_match_score": best_score,
            "latest_resume": cand.resumes[-1] if cand.resumes else None,
            "primary_app": cand_apps[0] if cand_apps else None,
        })

    if min_score:
        try:
            score_val = float(min_score)
            candidate_records = [c for c in candidate_records if c["best_match_score"] and c["best_match_score"] >= score_val]
        except ValueError:
            pass

    total_candidates = len(candidate_records)
    total_pages = max(1, (total_candidates + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    paged_records = candidate_records[start:start + per_page]

    return render_template(
        "recruiter/candidates.html",
        candidates=paged_records,
        q=q,
        job_id_filter=str(scoped_job_id) if scoped_job_id else "",
        recruiter_jobs=recruiter_jobs,
        exp_filter=exp_filter,
        min_score=min_score,
        page=page,
        total_pages=total_pages,
        total_candidates=total_candidates,
        active_nav="candidates",
    )


@recruiter_bp.route("/candidates/<int:candidate_id>/intelligence")
@role_required("recruiter")
def candidate_intelligence(candidate_id):
    profile = current_user.recruiter_profile
    if not profile or profile.approval_status != RecruiterProfile.STATUS_APPROVED:
        return redirect(url_for("recruiter.dashboard"))

    candidate = User.query.filter_by(id=candidate_id, role=User.ROLE_CANDIDATE, is_active_account=True).first_or_404()
    
    # Recruiter's jobs
    recruiter_jobs = Job.query.filter_by(recruiter_profile_id=profile.id).order_by(Job.created_at.desc()).all()
    job_ids = [j.id for j in recruiter_jobs]

    # Applications submitted by this candidate to this recruiter's jobs
    applications = Application.query.filter(
        Application.candidate_id == candidate.id,
        Application.job_id.in_(job_ids)
    ).order_by(Application.applied_at.desc()).all() if job_ids else []

    # Keep all application scores synchronized with canonical ATS scoring engine
    if applications:
        refresh_match_scores(applications)

    # Privacy Enforcement: deny access if candidate is not discoverable and has no applications to recruiter's jobs
    if not candidate.recruiter_discoverable and not applications:
        abort(404)

    # Selected job context — intentionally restricted to jobs this candidate actually
    # applied to. Previously any job in the recruiter's job list could be picked here,
    # which let the Role Suitability score be computed against a role the candidate
    # never applied for (confusing recruiters, e.g. scoring a Cybersecurity applicant
    # against an unrelated Cloud Infrastructure Engineer posting). Only the applied-to
    # job(s) are valid targets now.
    applied_job_ids = {a.job_id for a in applications}
    selected_job_id = request.args.get("job_id", type=int)
    target_job = None
    target_app = None

    if selected_job_id and selected_job_id in applied_job_ids:
        target_app = next(a for a in applications if a.job_id == selected_job_id)
        target_job = target_app.job
    elif applications:
        target_app = applications[0]
        target_job = target_app.job

    # Latest Resume
    resume = candidate.resumes[-1] if candidate.resumes else None
    resume_text = resume.raw_text if resume else (candidate.experience or "")

    # Run deep ATS scoring and structured intelligence
    match_data = None
    structured_jd = None
    structured_resume = extract_structured_resume(resume_text)

    if target_job:
        structured_jd = structured_jd_for_job(target_job)
        if resume_text:
            match_data = score_resume_for_job(resume_text, target_job)

    # AI Dossier & Interview Questions Generation
    ai_briefing = None
    ai_interview_questions = None
    ai_provider_used = "local"
    if target_job and match_data:
        cand_skills = [s.strip() for s in (candidate.skills or "").split(",") if s.strip()]
        ai_briefing, p1 = ai_service.generate_candidate_summary(
            candidate_name=candidate.full_name,
            headline=candidate.headline or "",
            skills=cand_skills,
            experience_summary=candidate.experience or "",
            job_title=target_job.title,
            match_score=match_data.get("score", 0),
        )
        ai_interview_questions, p2 = ai_service.generate_interview_questions(
            job_title=target_job.title,
            requirements=target_job.requirements or target_job.description or "",
            candidate_skills=cand_skills,
            missing_skills=match_data.get("missing_keywords", []),
        )
        ai_provider_used = p1

    # Recruiter timeline events & notes
    timeline_events = []
    if target_app:
        timeline_events = target_app.events

    return render_template(
        "recruiter/candidate_intelligence.html",
        candidate=candidate,
        resume=resume,
        structured_resume=structured_resume,
        target_job=target_job,
        target_app=target_app,
        applications=applications,
        recruiter_jobs=recruiter_jobs,
        match_data=match_data,
        structured_jd=structured_jd,
        timeline_events=timeline_events,
        ai_briefing=ai_briefing,
        ai_interview_questions=ai_interview_questions,
        ai_provider_used=ai_provider_used,
        active_nav="candidates",
    )


@recruiter_bp.route("/api/improve-jd", methods=["POST"])
@approved_recruiter_required
@limiter.limit("10 per minute; 50 per hour")
def api_improve_jd():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    raw_desc = str(data.get("description") or "").strip()
    reqs = str(data.get("requirements") or "").strip()

    if not title:
        return jsonify({"status": "error", "message": "Job title is required"}), 400

    improved_jd, provider_used = ai_service.improve_job_description(title, raw_desc, requirements=reqs)
    return jsonify({
        "status": "success",
        "improved": improved_jd,
        "provider_used": provider_used,
    })


@recruiter_bp.route("/applications/<int:application_id>/intelligence")
@approved_recruiter_required
def application_intelligence(application_id):
    app = (
        Application.query.join(Job)
        .filter(
            Application.id == application_id,
            Job.recruiter_profile_id == current_user.recruiter_profile.id
        )
        .first_or_404()
    )
    return redirect(url_for("recruiter.candidate_intelligence", candidate_id=app.candidate_id, job_id=app.job_id))


@recruiter_bp.route("/applications/<int:application_id>/add-note", methods=["POST"])
@approved_recruiter_required
def add_recruiter_note(application_id):
    app = (
        Application.query.join(Job)
        .filter(
            Application.id == application_id,
            Job.recruiter_profile_id == current_user.recruiter_profile.id
        )
        .first_or_404()
    )
    note_text = request.form.get("note", "").strip()
    if note_text:
        event = ApplicationEvent(
            application_id=app.id,
            status=app.status,
            note=f"Recruiter Note: {note_text}"
        )
        db.session.add(event)
        db.session.commit()
        flash("Private recruiter note added to candidate timeline.", "success")
    
    return redirect(url_for("recruiter.candidate_intelligence", candidate_id=app.candidate_id, job_id=app.job_id))


# ----------------------------------------------------------------------
# 4. HIRING PIPELINE (Interactive Kanban)
# ----------------------------------------------------------------------
@recruiter_bp.route("/pipeline")
@role_required("recruiter")
def pipeline():
    profile = current_user.recruiter_profile
    if not profile or profile.approval_status != RecruiterProfile.STATUS_APPROVED:
        return redirect(url_for("recruiter.dashboard"))

    selected_job_id = request.args.get("job_id", type=int)
    search_query = request.args.get("q", "").strip()
    recruiter_jobs = Job.query.filter_by(recruiter_profile_id=profile.id).order_by(Job.created_at.desc()).all()
    job_ids = [j.id for j in recruiter_jobs]

    # Default to this recruiter's own first job so the dropdown and the
    # board always reflect a single job they posted (no cross-job "All" view).
    if not selected_job_id and job_ids:
        selected_job_id = job_ids[0]

    app_query = Application.query
    if selected_job_id and selected_job_id in job_ids:
        app_query = app_query.filter_by(job_id=selected_job_id)
    elif job_ids:
        app_query = app_query.filter(Application.job_id.in_(job_ids))
    else:
        app_query = app_query.filter(Application.id == -1)

    # Name-only search is how "Varun" vs "Varun Shetty" mixups happen — match
    # on name AND email so the recruiter can tell candidates apart by email
    # too, and always show email on the card regardless of search.
    if search_query:
        app_query = app_query.join(User, Application.candidate_id == User.id).filter(
            or_(
                User.full_name.ilike(f"%{search_query}%"),
                User.email.ilike(f"%{search_query}%"),
            )
        )

    all_apps = app_query.order_by(Application.applied_at.desc()).all()

    full_columns = {
        "applied": [a for a in all_apps if a.status == Application.STATUS_APPLIED],
        "under_review": [a for a in all_apps if a.status == Application.STATUS_UNDER_REVIEW],
        "shortlisted": [a for a in all_apps if a.status == Application.STATUS_SHORTLISTED],
        "interview": [a for a in all_apps if a.status == Application.STATUS_INTERVIEW],
        "hired": [a for a in all_apps if a.status == Application.STATUS_HIRED],
        "rejected": [a for a in all_apps if a.status == Application.STATUS_REJECTED],
    }

    VALID_STAGES = {"applied", "under_review", "shortlisted", "interview", "hired", "rejected"}
    active_stage = request.args.get("stage", "applied").strip()
    if active_stage not in VALID_STAGES:
        active_stage = "applied"

    # Paginate each stage independently (5 per page) — each tab tracks its
    # own page number (e.g. ?applied_page=2) so switching tabs client-side
    # doesn't lose another stage's page position.
    PIPELINE_PAGE_SIZE = 5
    columns = {}
    for stage_key, items in full_columns.items():
        total_count = len(items)
        total_pages = max(1, (total_count + PIPELINE_PAGE_SIZE - 1) // PIPELINE_PAGE_SIZE)
        page = request.args.get(f"{stage_key}_page", 1, type=int) or 1
        page = max(1, min(page, total_pages))
        start = (page - 1) * PIPELINE_PAGE_SIZE
        paged = items[start:start + PIPELINE_PAGE_SIZE]
        # Card context (match label, skill evidence, stage dates) is only
        # computed for the page actually being viewed — same reasoning as
        # applicants(): the resume NLP extraction is real per-candidate
        # work, so we don't want to pay it for every application in every
        # stage on every pipeline load.
        card_context = {a.id: _pipeline_card_context(a) for a in paged}
        columns[stage_key] = {
            "apps": paged,
            "card_context": card_context,
            "total_count": total_count,
            "page": page,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        }

    return render_template(
        "recruiter/pipeline.html",
        jobs=recruiter_jobs,
        selected_job_id=selected_job_id,
        search_query=search_query,
        columns=columns,
        total_count=len(all_apps),
        active_stage=active_stage,
        active_nav="pipeline",
        today_str=utcnow().strftime("%Y-%m-%d"),
    )


# ----------------------------------------------------------------------
# 5. ANALYTICS
# ----------------------------------------------------------------------
@recruiter_bp.route("/analytics")
@role_required("recruiter")
def analytics():
    profile = current_user.recruiter_profile
    if not profile or profile.approval_status != RecruiterProfile.STATUS_APPROVED:
        return redirect(url_for("recruiter.dashboard"))

    jobs = Job.query.filter_by(recruiter_profile_id=profile.id).all()
    job_ids = [j.id for j in jobs]
    
    total_apps = 0
    shortlisted = 0
    interviews = 0
    hired = 0
    rejected = 0
    scores = []
    job_stats = []
    
    if job_ids:
        all_apps = Application.query.filter(Application.job_id.in_(job_ids)).all()
        total_apps = len(all_apps)
        shortlisted = sum(1 for a in all_apps if a.status == Application.STATUS_SHORTLISTED)
        interviews = sum(1 for a in all_apps if a.status == Application.STATUS_INTERVIEW)
        hired = sum(1 for a in all_apps if a.status == Application.STATUS_HIRED)
        rejected = sum(1 for a in all_apps if a.status == Application.STATUS_REJECTED)
        scores = [a.match_score for a in all_apps if a.match_score is not None]

        for j in jobs:
            j_apps = [a for a in all_apps if a.job_id == j.id]
            avg_score = (sum(a.match_score for a in j_apps if a.match_score) / len(j_apps)) if j_apps else 0
            job_stats.append({
                "job": j,
                "applicants_count": len(j_apps),
                "shortlisted_count": sum(1 for a in j_apps if a.status == Application.STATUS_SHORTLISTED),
                "hired_count": sum(1 for a in j_apps if a.status == Application.STATUS_HIRED),
                "avg_score": round(avg_score, 1),
            })

    shortlist_rate = round((shortlisted / total_apps * 100), 1) if total_apps > 0 else 0
    interview_rate = round((interviews / total_apps * 100), 1) if total_apps > 0 else 0
    hire_rate = round((hired / total_apps * 100), 1) if total_apps > 0 else 0
    avg_match = round(sum(scores) / len(scores), 1) if scores else 0
    
    c_90 = sum(1 for s in scores if s >= 90)
    c_75 = sum(1 for s in scores if 75 <= s < 90)
    c_60 = sum(1 for s in scores if 60 <= s < 75)
    c_low = sum(1 for s in scores if s < 60)

    match_distribution = {
        "count_90": c_90,
        "count_75": c_75,
        "count_60": c_60,
        "count_low": c_low,
        "pct_90": round((c_90 / total_apps * 100), 1) if total_apps > 0 else 0,
        "pct_75": round((c_75 / total_apps * 100), 1) if total_apps > 0 else 0,
        "pct_60": round((c_60 / total_apps * 100), 1) if total_apps > 0 else 0,
        "pct_low": round((c_low / total_apps * 100), 1) if total_apps > 0 else 0,
    }

    return render_template(
        "recruiter/analytics.html",
        total_apps=total_apps,
        shortlist_rate=shortlist_rate,
        interview_rate=interview_rate,
        hire_rate=hire_rate,
        avg_match=avg_match,
        hired=hired,
        match_distribution=match_distribution,
        job_stats=job_stats,
        active_nav="analytics",
    )


# ----------------------------------------------------------------------
# 6. NOTIFICATIONS
# ----------------------------------------------------------------------
@recruiter_bp.route("/notifications")
@role_required("recruiter")
def notifications():
    user_notifications = (
        Notification.query.filter_by(candidate_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return render_template("recruiter/notifications.html", notifications=user_notifications, active_nav="notifications")


@recruiter_bp.route("/notifications/read-all", methods=["POST"])
@role_required("recruiter")
def mark_notifications_read():
    Notification.query.filter_by(candidate_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("recruiter.notifications"))


# ----------------------------------------------------------------------
# 7. PROFILE & SETTINGS
# ----------------------------------------------------------------------
@recruiter_bp.route("/profile", methods=["GET", "POST"])
@role_required("recruiter")
def profile():
    recruiter_profile = current_user.recruiter_profile
    if request.method == "POST":
        full_name = _clean_profile_field(request.form.get("full_name"), 150)
        contact_role = _clean_profile_field(request.form.get("contact_role"), 150)
        phone = _clean_profile_field(request.form.get("phone"), 30)

        if full_name:
            current_user.full_name = full_name
        if recruiter_profile:
            recruiter_profile.contact_role = contact_role
            recruiter_profile.phone = phone
        db.session.commit()
        flash("Your profile was updated successfully.", "success")
        return redirect(url_for("recruiter.profile"))

    return render_template("recruiter/profile.html", profile=recruiter_profile, active_nav="profile")


@recruiter_bp.route("/company", methods=["GET", "POST"])
@role_required("recruiter")
def company_profile():
    recruiter_profile = current_user.recruiter_profile
    if request.method == "POST":
        company_name = _clean_profile_field(request.form.get("company_name"), 200)
        industry = _clean_profile_field(request.form.get("industry"), 100)
        company_size = _clean_profile_field(request.form.get("company_size"), 50)
        raw_website = request.form.get("company_website", "").strip()
        hiring_needs = _clean_profile_field(request.form.get("hiring_needs"), 5000)

        if recruiter_profile:
            if company_name:
                recruiter_profile.company_name = company_name
            recruiter_profile.industry = industry
            recruiter_profile.company_size = company_size
            if raw_website:
                cleaned_website = _clean_website_url(raw_website)
                if not cleaned_website:
                    flash("Company website must be a valid http:// or https:// URL — it wasn't saved.", "error")
                else:
                    recruiter_profile.company_website = cleaned_website
            else:
                recruiter_profile.company_website = ""
            recruiter_profile.hiring_needs = hiring_needs
            db.session.commit()
            flash("Company profile updated successfully.", "success")
        return redirect(url_for("recruiter.company_profile"))

    return render_template("recruiter/company_profile.html", profile=recruiter_profile, active_nav="company_profile")


@recruiter_bp.route("/settings", methods=["GET", "POST"])
@role_required("recruiter")
def settings():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "").strip()
        if new_password:
            if not current_password or not current_user.check_password(current_password):
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 8:
                flash("Password must be at least 8 characters long.", "error")
            elif len(new_password) > 128:
                flash("Password exceeds the maximum allowed length.", "error")
            else:
                current_user.set_password(new_password)
                db.session.commit()
                flash("Password updated successfully.", "success")
        return redirect(url_for("recruiter.settings"))

    return render_template("recruiter/settings.html", active_nav="settings")


# ----------------------------------------------------------------------
# APPLICATION STATUS CONTROLLER
# ----------------------------------------------------------------------
@recruiter_bp.route("/applications/<int:application_id>/status", methods=["POST"])
@approved_recruiter_required
def update_application_status(application_id):
    application = (
        Application.query
        .join(Job, Application.job_id == Job.id)
        .filter(
            Application.id == application_id,
            Job.recruiter_profile_id == current_user.recruiter_profile.id,
        )
        .first_or_404()
    )
    new_status = request.form.get("status", "").strip()
    return_to = request.form.get("return_to", "applicants")

    if new_status not in Application.STATUSES:
        flash("Invalid status specified.", "error")
        return redirect(url_for("recruiter.applicants", job_id=application.job_id))

    if application.job.status == Job.STATUS_CLOSED and new_status != Application.STATUS_REJECTED:
        flash(
            "This job is closed — the only pipeline action available now is rejecting remaining "
            "candidates. Reopen the job first to continue processing this applicant.",
            "error",
        )
        return redirect(url_for("recruiter.applicants", job_id=application.job_id))

    if new_status != application.status and new_status not in APPLICATION_VALID_TRANSITIONS.get(application.status, []):
        flash(f"Cannot transition application directly from '{application.status.title()}' to '{new_status.title()}'.", "error")
        return redirect(url_for("recruiter.applicants", job_id=application.job_id))

    old_status = application.status
    note = request.form.get("note", "").strip() or None

    # Scheduling (or rescheduling) an interview: the recruiter picks a date
    # and, optionally, a time/type/interviewer/meeting link on the pipeline
    # form. Only the date is required — everything else is optional so
    # other pages that move an application to "interview" without these
    # details keep working. Rescheduling reuses this same branch: the
    # status doesn't change (still "interview"), but the guard above lets
    # a same-status "transition" through untouched.
    interview_date = None
    is_scheduling = new_status == Application.STATUS_INTERVIEW
    if is_scheduling:
        raw_interview_date = request.form.get("interview_date", "").strip()
        if raw_interview_date:
            raw_interview_time = request.form.get("interview_time", "").strip()
            try:
                if raw_interview_time:
                    interview_date = datetime.strptime(f"{raw_interview_date} {raw_interview_time}", "%Y-%m-%d %H:%M")
                else:
                    interview_date = datetime.strptime(raw_interview_date, "%Y-%m-%d")
            except ValueError:
                flash("That interview date/time doesn't look valid. Please try again.", "error")
                return redirect(url_for("recruiter.pipeline", job_id=application.job_id, stage=old_status))
        elif old_status != Application.STATUS_INTERVIEW:
            flash("Pick an interview date to schedule.", "error")
            return redirect(url_for("recruiter.pipeline", job_id=application.job_id, stage=old_status))

    application.status = new_status
    if interview_date:
        application.interview_date = interview_date
    if is_scheduling:
        application.interview_type = request.form.get("interview_type", "").strip() or application.interview_type
        application.interviewer_name = request.form.get("interviewer_name", "").strip() or application.interviewer_name
        application.meeting_link = request.form.get("meeting_link", "").strip() or application.meeting_link
        # Any (re)scheduling action clears a prior "completed" mark — the
        # recruiter is actively re-setting up this interview.
        application.interview_completed = False

    if new_status == Application.STATUS_REJECTED:
        application.rejection_reason = request.form.get("rejection_reason", "").strip() or None

    status_label = new_status.replace("_", " ").title()
    if interview_date:
        event_note = note or f"Interview scheduled for {interview_date.strftime('%d %b %Y, %I:%M %p') if raw_interview_time else interview_date.strftime('%d %b %Y')}."
        notif_message = f"Your interview for {application.job.title} has been scheduled."
    else:
        event_note = note or f"Status updated to {new_status.replace('_', ' ')} by recruiter."
        notif_message = f"Your application status has been updated to '{status_label}'."

    db.session.add(ApplicationEvent(
        application_id=application.id,
        status=new_status,
        note=event_note,
    ))
    db.session.add(Notification(
        candidate_id=application.candidate_id,
        title=f"Application update: {application.job.title}",
        message=notif_message,
        link=f"/candidate/applications/{application.id}",
    ))

    if new_status == Application.STATUS_HIRED:
        _auto_close_other_applications_on_hire(application)
    elif old_status == Application.STATUS_HIRED and new_status != Application.STATUS_HIRED:
        _reopen_auto_closed_applications_on_unhire(application)

    db.session.commit()
    if interview_date:
        flash(f"Interview scheduled for {interview_date.strftime('%d %b %Y')}. The candidate has been notified.", "success")
    else:
        flash(f"Candidate status updated to '{status_label}'.", "success")

    if return_to == "pipeline":
        return redirect(url_for("recruiter.pipeline", job_id=application.job_id, stage=new_status))
    elif return_to == "intelligence":
        return redirect(url_for("recruiter.candidate_intelligence", candidate_id=application.candidate_id, job_id=application.job_id))
    elif return_to == "shortlist":
        return redirect(url_for(
            "recruiter.job_shortlist",
            job_id=application.job_id,
            threshold=request.form.get("threshold", type=float),
            page=request.form.get("page", type=int),
        ))
    return redirect(url_for("recruiter.applicants", job_id=application.job_id))


@recruiter_bp.route("/applications/<int:application_id>/interview/complete", methods=["POST"])
@approved_recruiter_required
def mark_interview_complete(application_id):
    """Close out an interview without changing status — the recruiter still
    needs to decide Hire/Reject/back-to-Shortlist afterwards."""
    application = (
        Application.query
        .join(Job, Application.job_id == Job.id)
        .filter(
            Application.id == application_id,
            Job.recruiter_profile_id == current_user.recruiter_profile.id,
        )
        .first_or_404()
    )
    if application.status != Application.STATUS_INTERVIEW:
        flash("Only an application currently in the Interview stage can be marked complete.", "error")
        return redirect(url_for("recruiter.pipeline", job_id=application.job_id))

    application.interview_completed = True
    db.session.add(ApplicationEvent(
        application_id=application.id,
        status=Application.STATUS_INTERVIEW,
        note="Interview marked complete by recruiter.",
    ))
    db.session.commit()
    flash("Interview marked as complete.", "success")
    return redirect(url_for("recruiter.pipeline", job_id=application.job_id, stage="interview"))


@recruiter_bp.route("/applications/<int:application_id>/undo", methods=["POST"])
@approved_recruiter_required
def undo_application_status(application_id):
    """Revert a Hire or Reject back to whatever stage the candidate was in
    immediately before that action — a safety net for a mis-click, not a
    general-purpose status editor (hence restricted to those two statuses)."""
    application = (
        Application.query
        .join(Job, Application.job_id == Job.id)
        .filter(
            Application.id == application_id,
            Job.recruiter_profile_id == current_user.recruiter_profile.id,
        )
        .first_or_404()
    )

    if application.status not in (Application.STATUS_HIRED, Application.STATUS_REJECTED):
        flash("Undo is only available right after a Hire or Reject action.", "error")
        return redirect(url_for("recruiter.pipeline", job_id=application.job_id))

    old_status = application.status
    prev_status = _status_before_latest(application)

    application.status = prev_status
    if prev_status != Application.STATUS_REJECTED:
        application.rejection_reason = None

    db.session.add(ApplicationEvent(
        application_id=application.id,
        status=prev_status,
        note=f"Undone: reverted from '{old_status.replace('_', ' ')}' back to '{prev_status.replace('_', ' ')}' (recruiter undo).",
    ))
    db.session.add(Notification(
        candidate_id=application.candidate_id,
        title=f"Application update: {application.job.title}",
        message=f"Your application status was reverted back to '{prev_status.replace('_', ' ').title()}'.",
        link=f"/candidate/applications/{application.id}",
    ))

    if old_status == Application.STATUS_HIRED and prev_status != Application.STATUS_HIRED:
        _reopen_auto_closed_applications_on_unhire(application)

    db.session.commit()
    flash(f"Undone — candidate moved back to '{prev_status.replace('_', ' ').title()}'.", "success")
    return redirect(url_for("recruiter.pipeline", job_id=application.job_id, stage=application.status))


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
