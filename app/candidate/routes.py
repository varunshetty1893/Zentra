import os
import uuid
import json
import re
from app.utils.time import utcnow
from flask import Blueprint, render_template, redirect, url_for, flash, current_app, send_file, request, jsonify
from flask_login import login_required, current_user, logout_user
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import db
from app.models.resume import Resume
from app.models.user import User
from app.models.job import Job
from app.models.application import Application
from app.models.career_entry import CareerEntry
from app.models.saved_job import SavedJob
from app.models.application_event import ApplicationEvent
from app.models.notification import Notification
from app.candidate.forms import ATSCheckForm, ResumeBuilderForm, ProfileSettingsForm, PrivacySettingsForm, DeleteAccountForm
from app.utils.decorators import role_required
from app.utils.file_security import inspect_file_magic, generate_secure_stored_filename, FileValidationError
from app.utils.security import limiter
from app.ml.resume_parser import extract_text
from app.ml.ats_scorer import score_resume, score_resume_for_job
from app.ml.category_classifier import predict_category, category_reference_text
from app.ml.bullet_improver import improve_resume_bullet
from app.ml.job_matcher import rank_jobs_for_resume
from app.ai import ai_service
from app.utils.resume_pdf import (
    build_resume_pdf,
    build_structured_resume_pdf,
    structured_resume_to_plain_text,
    parse_resume_to_structured_dict,
)

candidate_bp = Blueprint("candidate", __name__, template_folder="../templates/candidate")


@candidate_bp.route("/dashboard")
@role_required("candidate")
def dashboard():
    candidate_applications = Application.query.filter_by(candidate_id=current_user.id)

    # Recent-5 list for display and the true total are two separate
    # queries now — the list length was previously (mis)used as the count.
    applications = (
        candidate_applications.options(joinedload(Application.job).joinedload(Job.recruiter_profile))
        .order_by(Application.applied_at.desc())
        .limit(5)
        .all()
    )
    total_applications = candidate_applications.count()

    primary_resume = Resume.get_primary(current_user.id)

    recommended = []
    if primary_resume:
        # Jobs already applied to are excluded up front — keeps them out of
        # "Recommended for you" and means fewer jobs get scored below.
        applied_job_ids = db.session.query(Application.job_id).filter_by(candidate_id=current_user.id)
        active_jobs = (
            Job.query.options(joinedload(Job.recruiter_profile))
            .filter(
                Job.status == Job.STATUS_ACTIVE,
                or_(Job.application_deadline.is_(None), Job.application_deadline >= utcnow()),
                Job.id.notin_(applied_job_ids),
            )
            .all()
        )
        ranked = rank_jobs_for_resume(primary_resume.raw_text, active_jobs)
        recommended = ranked[:3]

    return render_template(
        "candidate/dashboard.html",
        applications=applications,
        total_applications=total_applications,
        latest_resume=primary_resume,
        resumes=Resume.query.filter_by(candidate_id=current_user.id).order_by(Resume.created_at.desc()).all(),
        recommended=recommended,
    )


@candidate_bp.route("/resume-ai", methods=["GET", "POST"])
@login_required
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def resume_ai():
    form = ATSCheckForm()
    active_jobs = (
        Job.query.options(joinedload(Job.recruiter_profile))
        .filter(
            Job.status == Job.STATUS_ACTIVE,
            or_(Job.application_deadline.is_(None), Job.application_deadline >= utcnow()),
        )
        .order_by(Job.created_at.desc())
        .all()
    )
    form.selected_job_id.choices = [("", "-- Select an active platform job --")] + [
        (str(j.id), f"{j.title} — {j.recruiter_profile.company_name if j.recruiter_profile else 'Company'} ({j.location or 'Remote'})")
        for j in active_jobs
    ]

    primary_resume = (
        Resume.get_primary(current_user.id)
        if (current_user.is_authenticated and current_user.is_candidate)
        else None
    )
    result = None

    if form.validate_on_submit():
        resume_text = (form.resume_text.data or "").strip()

        uploaded = form.resume_file.data
        stored_filename = None
        original_filename = None
        if uploaded and uploaded.filename:
            try:
                inspect_file_magic(uploaded.stream, uploaded.filename, allowed_category="resume")
            except FileValidationError as e:
                flash(f"File upload security error: {str(e)}", "error")
                return render_template("resume_ai.html", form=form, result=None, active_jobs=active_jobs)

            original_filename = secure_filename(uploaded.filename)
            stored_filename = generate_secure_stored_filename(original_filename)
            filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
            uploaded.save(filepath)
            try:
                resume_text = extract_text(filepath)
            except Exception:
                # A file can pass the magic-byte/structure check in
                # inspect_file_magic() and still fail to parse (e.g. a
                # DOCX whose document.xml is present but malformed) —
                # don't let that surface as an unhandled 500.
                flash("Couldn't read that resume file — it may be corrupted. Try re-saving it or paste your resume text instead.", "error")
                return render_template("resume_ai.html", form=form, result=None, active_jobs=active_jobs)

        if not resume_text:
            flash("Upload a resume file or paste your resume text.", "error")
            return render_template("resume_ai.html", form=form, result=None, active_jobs=active_jobs)

        analysis_mode = form.analysis_mode.data or "specific_job"
        job_selection_type = form.job_selection_type.data or "paste"
        detected_category = None
        selected_job_obj = None

        if analysis_mode == "general":
            detected_category = predict_category(resume_text)
            reference_text = category_reference_text(detected_category) if detected_category else ""
            result = score_resume(resume_text, reference_text)
            result["detected_category"] = detected_category
            result["analysis_mode"] = "general"
        else:
            if job_selection_type == "select" and form.selected_job_id.data:
                try:
                    selected_job_obj = Job.query.filter_by(
                        id=int(form.selected_job_id.data),
                        status=Job.STATUS_ACTIVE
                    ).first()
                except (ValueError, TypeError):
                    selected_job_obj = None

            if selected_job_obj:
                result = score_resume_for_job(resume_text, selected_job_obj)
                result["target_job"] = {
                    "id": selected_job_obj.id,
                    "title": selected_job_obj.title,
                    "company": selected_job_obj.recruiter_profile.company_name if selected_job_obj.recruiter_profile else "",
                }
            elif form.jd_text.data and form.jd_text.data.strip():
                jd_text = form.jd_text.data.strip()
                jd_lower = jd_text.lower()
                matched_job = None
                for j in active_jobs:
                    if j.title and len(j.title) >= 6 and j.title.lower() in jd_lower:
                        matched_job = j
                        break
                    desc_snippet = (j.description or "").strip()[:80].lower()
                    if desc_snippet and len(desc_snippet) >= 30 and desc_snippet in jd_lower:
                        matched_job = j
                        break

                if matched_job:
                    result = score_resume_for_job(resume_text, matched_job)
                    result["target_job"] = {
                        "id": matched_job.id,
                        "title": matched_job.title,
                        "company": matched_job.recruiter_profile.company_name if matched_job.recruiter_profile else "",
                    }
                else:
                    result = score_resume(resume_text, jd_text)
            else:
                detected_category = predict_category(resume_text)
                reference_text = category_reference_text(detected_category) if detected_category else ""
                result = score_resume(resume_text, reference_text)
                result["detected_category"] = detected_category

        if current_user.is_authenticated and current_user.is_candidate:
            previous_score = (
                Resume.query.filter_by(candidate_id=current_user.id)
                .filter(Resume.last_ats_score.isnot(None))
                .order_by(Resume.created_at.desc())
                .first()
            )
            candidate_resumes = Resume.query.filter_by(candidate_id=current_user.id).all()
            matched_resume = None
            clean_test_text = resume_text.strip()
            for r in candidate_resumes:
                r_text = (r.raw_text or "").strip()
                if r_text and (
                    r_text == clean_test_text
                    or (len(clean_test_text) > 80 and clean_test_text in r_text)
                    or (len(r_text) > 80 and r_text in clean_test_text)
                ):
                    matched_resume = r
                    break

            if matched_resume:
                # Update existing saved resume with fresh ATS metrics
                matched_resume.last_ats_score = result["score"]
                matched_resume.last_matched_keywords = ", ".join(result["matched_keywords"])
                matched_resume.last_missing_keywords = ", ".join(result["missing_keywords"])
            elif not candidate_resumes:
                # Candidate has zero resumes — onboard this as their initial primary resume
                new_name = (form.resume_name.data or "").strip() or (original_filename or "Primary Resume")
                resume = Resume(
                    candidate_id=current_user.id,
                    source="upload" if stored_filename else "paste",
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    raw_text=resume_text,
                    name=new_name,
                    target_role=form.target_role.data.strip() if form.target_role.data else None,
                    last_ats_score=result["score"],
                    last_matched_keywords=", ".join(result["matched_keywords"]),
                    last_missing_keywords=", ".join(result["missing_keywords"]),
                    is_primary=True,
                )
                db.session.add(resume)
            elif form.resume_name.data and form.resume_name.data.strip():
                # Candidate explicitly gave a title to save this as a new targeted resume
                has_primary = any(r.is_primary for r in candidate_resumes)
                resume = Resume(
                    candidate_id=current_user.id,
                    source="upload" if stored_filename else "paste",
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    raw_text=resume_text,
                    name=form.resume_name.data.strip(),
                    target_role=form.target_role.data.strip() if form.target_role.data else None,
                    last_ats_score=result["score"],
                    last_matched_keywords=", ".join(result["matched_keywords"]),
                    last_missing_keywords=", ".join(result["missing_keywords"]),
                    is_primary=not has_primary,
                )
                db.session.add(resume)
            else:
                # Testing against an ad-hoc JD: update the primary resume's latest benchmark score
                primary_r = Resume.get_primary(current_user.id)
                if primary_r:
                    primary_r.last_ats_score = result["score"]
                    primary_r.last_matched_keywords = ", ".join(result["matched_keywords"])
                    primary_r.last_missing_keywords = ", ".join(result["missing_keywords"])

            if previous_score and result["score"] > previous_score.last_ats_score:
                db.session.add(Notification(
                    candidate_id=current_user.id,
                    title="Your ATS score improved",
                    message=f"Your latest analysis scored {result['score']}% — up from {round(previous_score.last_ats_score)}%.",
                    link=url_for("candidate.ats_history"),
                ))
            db.session.commit()

    history = []
    improvement = None
    if current_user.is_authenticated and current_user.is_candidate:
        history = Resume.query.filter_by(candidate_id=current_user.id).filter(Resume.last_ats_score.isnot(None)).order_by(Resume.created_at.desc()).limit(6).all()
        if len(history) > 1:
            improvement = round(history[0].last_ats_score - history[-1].last_ats_score)
    return render_template("resume_ai.html", form=form, result=result, ats_history=history, ats_improvement=improvement, active_jobs=active_jobs, primary_resume=primary_resume)


@candidate_bp.route("/resume-builder", methods=["GET", "POST"])
@role_required("candidate")
def resume_builder():
    if request.method == "POST" and not request.is_json:
        full_name = request.form.get("full_name") or current_user.full_name or ""
        title = request.form.get("title") or ""
        email = request.form.get("email") or current_user.email or ""
        phone = request.form.get("phone") or ""
        summary = request.form.get("summary") or ""
        experience = request.form.get("experience") or ""
        education = request.form.get("education") or ""
        skills = request.form.get("skills") or ""
        resume_name = (request.form.get("resume_name") or f"{full_name or 'My'} Resume").strip()
        target_role = (request.form.get("target_role") or title).strip() or None

        structured_data = {
            "__structured__": True,
            "template": "modern",
            "personal": {
                "full_name": full_name,
                "title": title,
                "email": email,
                "phone": phone,
            },
            "summary": summary,
            "experience": [{"title": "", "company": "", "description": experience, "bullets": [b.strip() for b in experience.split("\n") if b.strip()]}] if experience else [],
            "education": [{"degree": education, "institution": ""}] if education else [],
            "skills": {"other": [s.strip() for s in skills.split(",") if s.strip()]},
            "projects": [],
            "certifications": [],
            "achievements": [],
        }

        has_primary = Resume.query.filter_by(candidate_id=current_user.id, is_primary=True).first() is not None
        resume = Resume(
            candidate_id=current_user.id,
            source="builder",
            raw_text=json.dumps(structured_data),
            name=resume_name,
            target_role=target_role,
            is_primary=not has_primary,
        )
        db.session.add(resume)
        db.session.commit()
        flash("Resume saved successfully.", "success")

        pdf_buffer = build_structured_resume_pdf(structured_data, template="modern")
        safe_name = (full_name or "resume").strip().replace(" ", "_")
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{safe_name}_resume.pdf",
        )

    resume_id = request.args.get("resume_id", type=int)
    target_job_id = request.args.get("target_job_id", type=int)

    resume_obj = None
    if resume_id:
        resume_obj = Resume.query.filter_by(id=resume_id, candidate_id=current_user.id).first()

    active_jobs = (
        Job.query.filter(
            Job.status == Job.STATUS_ACTIVE,
            or_(Job.application_deadline.is_(None), Job.application_deadline >= utcnow()),
        )
        .order_by(Job.created_at.desc())
        .all()
    )
    target_job = None
    if target_job_id:
        target_job = Job.query.filter(
            Job.id == target_job_id,
            Job.status == Job.STATUS_ACTIVE,
            or_(Job.application_deadline.is_(None), Job.application_deadline >= utcnow()),
        ).first()

    if resume_obj:
        initial_data = parse_resume_to_structured_dict(resume_obj.raw_text, resume_obj.name, resume_obj.target_role)
    else:
        # Prepopulate with current user profile information
        initial_data = {
            "__structured__": True,
            "template": "modern",
            "personal": {
                "full_name": current_user.full_name or "",
                "title": current_user.headline or "",
                "email": current_user.email or "",
                "phone": current_user.phone or "",
                "location": current_user.location or "",
                "linkedin": current_user.linkedin_url or "",
                "github": current_user.github_url or "",
                "portfolio": current_user.portfolio_url or "",
            },
            "summary": current_user.bio or "",
            "experience": [
                {
                    "title": e.title,
                    "company": e.organization or "",
                    "location": e.location or "",
                    "start_date": e.start_date or "",
                    "end_date": e.end_date or "",
                    "current": False,
                    "bullets": [b.strip() for b in e.description.split("\n") if b.strip()] if e.description else []
                }
                for e in current_user.career_entries if e.entry_type == "experience"
            ],
            "education": [
                {
                    "degree": ed.title,
                    "field": "",
                    "institution": ed.organization or "",
                    "year": ed.end_date or "",
                    "gpa": ""
                }
                for ed in current_user.career_entries if ed.entry_type == "education"
            ],
            "skills": {
                "languages": [],
                "frameworks": [],
                "databases": [],
                "tools": [],
                "cloud": [],
                "other": [s.strip() for s in (current_user.skills or "").split(",") if s.strip()],
            },
            "projects": [
                {
                    "name": p.title,
                    "role": "",
                    "technologies": [],
                    "link": p.credential_url or "",
                    "github": "",
                    "bullets": [b.strip() for b in p.description.split("\n") if b.strip()] if p.description else []
                }
                for p in current_user.career_entries if p.entry_type == "project"
            ],
            "certifications": [
                {
                    "name": c.title,
                    "issuer": c.organization or "",
                    "year": c.end_date or "",
                    "url": c.credential_url or ""
                }
                for c in current_user.career_entries if c.entry_type == "certification"
            ],
            "achievements": [],
        }

    return render_template(
        "candidate/resume_builder.html",
        resume_obj=resume_obj,
        target_job=target_job,
        active_jobs=active_jobs,
        initial_data=initial_data,
    )


def _rescore_applications_for_resume(resume):
    """Re-score every Application pinned to `resume` after its raw_text changed.

    Application.resume_id is a fixed snapshot set at apply-time, so this is
    the only path that can make an already-submitted application's stored
    score stale from the candidate side (uploading a brand-new resume via
    Resume AI never touches an existing application, since that always
    creates a new Resume row that no Application references yet).
    """
    apps = Application.query.filter_by(resume_id=resume.id).all()
    if not apps:
        return
    now = utcnow()
    for application in apps:
        if not application.job:
            continue
        fresh_score = score_resume_for_job(resume.raw_text, application.job)["score"]
        application.match_score = fresh_score
        application.scored_at = now
    db.session.commit()


@candidate_bp.route("/api/resume-builder/save", methods=["POST"])
@role_required("candidate")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def api_save_resume_builder():
    data = request.get_json(silent=True) or {}
    resume_data = data.get("resume_data")
    if not isinstance(resume_data, dict):
        return jsonify({"status": "error", "message": "Invalid resume data structure"}), 400

    resume_id = data.get("resume_id")
    resume_name = str(data.get("name") or "My Resume").strip()[:150]
    target_role = str(data.get("target_role") or "").strip()[:150] or None
    target_job_id = data.get("target_job_id")

    # Serialize structured data into raw_text
    serialized_text = json.dumps(resume_data)
    plain_text = structured_resume_to_plain_text(resume_data)

    # Compute ATS Score
    target_job = None
    if target_job_id:
        try:
            target_job = Job.query.filter_by(
                id=int(target_job_id),
                status=Job.STATUS_ACTIVE
            ).first()
        except (ValueError, TypeError):
            pass

    if target_job:
        ats_result = score_resume_for_job(plain_text, target_job)
    else:
        detected_category = predict_category(plain_text)
        reference_text = category_reference_text(detected_category) if detected_category else ""
        ats_result = score_resume(plain_text, reference_text)
        ats_result["detected_category"] = detected_category

    # Save or update Resume
    if resume_id:
        resume = Resume.query.filter_by(id=resume_id, candidate_id=current_user.id).first()
        if not resume:
            return jsonify({"status": "error", "message": "Resume not found"}), 404
        content_changed = resume.raw_text != serialized_text
        resume.name = resume_name
        resume.target_role = target_role
        resume.raw_text = serialized_text
        resume.last_ats_score = ats_result["score"]
        resume.last_matched_keywords = ", ".join(ats_result.get("matched_keywords", []))
        resume.last_missing_keywords = ", ".join(ats_result.get("missing_keywords", []))
        if content_changed:
            # This resume row is edited in place (unlike Resume AI, which
            # always creates a new row) — any Application already pinned to
            # this resume_id now has a stale match_score, since scoring was
            # computed against the old raw_text. Re-score just those rows;
            # applications tied to other resumes of this candidate are
            # unaffected.
            _rescore_applications_for_resume(resume)
    else:
        has_primary = Resume.query.filter_by(candidate_id=current_user.id, is_primary=True).first() is not None
        resume = Resume(
            candidate_id=current_user.id,
            source="builder",
            raw_text=serialized_text,
            name=resume_name,
            target_role=target_role,
            last_ats_score=ats_result["score"],
            last_matched_keywords=", ".join(ats_result.get("matched_keywords", [])),
            last_missing_keywords=", ".join(ats_result.get("missing_keywords", [])),
            is_primary=not has_primary,
        )
        db.session.add(resume)

    db.session.commit()

    return jsonify({
        "status": "success",
        "resume_id": resume.id,
        "ats_result": ats_result,
        "updated_at": (resume.updated_at or resume.created_at).strftime("%d %b %Y, %H:%M"),
    })


@candidate_bp.route("/api/improve-bullet", methods=["POST"])
@role_required("candidate")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def api_improve_bullet():
    data = request.get_json(silent=True) or {}
    bullet = str(data.get("bullet") or "").strip()
    title = str(data.get("title") or "").strip()
    company = str(data.get("company") or "").strip()

    if not bullet:
        return jsonify({"status": "error", "message": "Bullet text is required"}), 400
    if len(bullet) > 2000:
        return jsonify({"status": "error", "message": "Bullet text too long (max 2000 characters)"}), 400
    if len(title) > 200:
        return jsonify({"status": "error", "message": "Title too long (max 200 characters)"}), 400
    if len(company) > 200:
        return jsonify({"status": "error", "message": "Company name too long (max 200 characters)"}), 400

    improved, provider_used = ai_service.improve_bullet(bullet, title=title, company=company)
    return jsonify({
        "status": "success",
        "original": bullet,
        "suggestion": improved,
        "provider_used": provider_used,
    })


@candidate_bp.route("/api/generate-summary", methods=["POST"])
@role_required("candidate")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def api_generate_summary():
    data = request.get_json(silent=True) or {}
    headline = str(data.get("headline") or current_user.headline or "").strip()[:200]
    target_role = str(data.get("target_role") or "").strip()[:200]
    raw_skills = data.get("skills")
    if isinstance(raw_skills, list):
        skills = [str(s).strip()[:100] for s in raw_skills[:30] if str(s).strip()]
    else:
        skills = [s.strip() for s in (current_user.skills or "").split(",") if s.strip()][:30]

    raw_snippets = data.get("experience_snippets")
    if isinstance(raw_snippets, list):
        experience_snippets = [str(sn).strip()[:500] for sn in raw_snippets[:15] if str(sn).strip()]
    else:
        experience_snippets = [
            f"{e.title} at {e.organization}" for e in current_user.career_entries if e.entry_type == "experience"
        ][:15]

    summary, provider_used = ai_service.generate_summary(
        headline=headline,
        skills=skills,
        experience_snippets=experience_snippets,
        target_role=target_role,
    )
    return jsonify({
        "status": "success",
        "summary": summary,
        "provider_used": provider_used,
    })



@candidate_bp.route("/api/analyze-live", methods=["POST"])
@role_required("candidate")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def api_analyze_live():
    data = request.get_json(silent=True) or {}
    resume_data = data.get("resume_data")
    if not isinstance(resume_data, dict):
        resume_data = {}
    target_job_id = data.get("target_job_id")
    custom_jd = str(data.get("jd_text") or "").strip()
    if len(custom_jd) > 50000:
        return jsonify({"status": "error", "message": "Job description too long (max 50000 chars)"}), 400

    plain_text = structured_resume_to_plain_text(resume_data)

    target_job = None
    if target_job_id:
        try:
            target_job = Job.query.filter_by(
                id=int(target_job_id),
                status=Job.STATUS_ACTIVE
            ).first()
        except (ValueError, TypeError):
            pass

    if target_job:
        ats_result = score_resume_for_job(plain_text, target_job)
    elif custom_jd:
        ats_result = score_resume(plain_text, custom_jd)
    else:
        detected_category = predict_category(plain_text)
        reference_text = category_reference_text(detected_category) if detected_category else ""
        ats_result = score_resume(plain_text, reference_text)
        ats_result["detected_category"] = detected_category

    return jsonify({
        "status": "success",
        "ats_result": ats_result,
    })


@candidate_bp.route("/my-resumes")
@role_required("candidate")
def my_resumes():
    resumes = Resume.query.filter_by(candidate_id=current_user.id).order_by(Resume.created_at.desc()).all()
    return render_template("candidate/my_resumes.html", resumes=resumes)


@candidate_bp.route("/resumes/<int:resume_id>/set-primary", methods=["POST"])
@role_required("candidate")
def set_primary_resume(resume_id):
    resume = Resume.query.filter_by(id=resume_id, candidate_id=current_user.id).first_or_404()
    # Unset is_primary on all candidate resumes then set on this one
    Resume.query.filter_by(candidate_id=current_user.id).update({"is_primary": False})
    resume.is_primary = True
    db.session.commit()
    flash(f"'{resume.name or 'Resume'}' set as your primary resume.", "success")
    return redirect(url_for("candidate.my_resumes"))


@candidate_bp.route("/resumes/<int:resume_id>/duplicate", methods=["POST"])
@role_required("candidate")
def duplicate_resume(resume_id):
    original = Resume.query.filter_by(id=resume_id, candidate_id=current_user.id).first_or_404()
    copy_name = f"Copy of {original.name or 'Resume'}"
    copy_resume = Resume(
        candidate_id=current_user.id,
        source=original.source,
        original_filename=original.original_filename,
        stored_filename=original.stored_filename,
        raw_text=original.raw_text,
        name=copy_name,
        target_role=original.target_role,
        last_ats_score=original.last_ats_score,
        last_matched_keywords=original.last_matched_keywords,
        last_missing_keywords=original.last_missing_keywords,
        is_primary=False,
    )
    db.session.add(copy_resume)
    db.session.commit()
    flash(f"Resume duplicated as '{copy_name}'.", "success")
    return redirect(url_for("candidate.my_resumes"))


@candidate_bp.route("/resumes/<int:resume_id>/delete", methods=["POST"])
@role_required("candidate")
def delete_resume(resume_id):
    resume = Resume.query.filter_by(id=resume_id, candidate_id=current_user.id).first_or_404()
    was_primary = resume.is_primary
    stored_name = resume.stored_filename
    db.session.delete(resume)
    db.session.commit()

    if was_primary:
        # Promote newest remaining resume to primary
        remaining = Resume.query.filter_by(candidate_id=current_user.id).order_by(Resume.created_at.desc()).first()
        if remaining:
            remaining.is_primary = True
            db.session.commit()

    # Reference-aware cleanup: delete physical file ONLY if no other resume uses it
    if stored_name:
        remaining_count = Resume.query.filter_by(stored_filename=stored_name).count()
        if remaining_count == 0:
            upload_folder = os.path.abspath(current_app.config["UPLOAD_FOLDER"])
            file_path = os.path.abspath(os.path.join(upload_folder, stored_name))
            if os.path.commonpath([upload_folder, file_path]) == upload_folder and os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

    flash("Resume deleted.", "info")
    return redirect(url_for("candidate.my_resumes"))


@candidate_bp.route("/resumes/upload", methods=["POST"])
@role_required("candidate")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTHENTICATED", "120 per minute"))
def upload_resume():
    """Directly upload a resume file (PDF or DOCX) into My Resumes."""
    if "resume_file" not in request.files:
        flash("Please select a resume file to upload.", "error")
        return redirect(url_for("candidate.my_resumes"))

    file = request.files["resume_file"]
    if not file or not file.filename:
        flash("Please select a valid resume file.", "error")
        return redirect(url_for("candidate.my_resumes"))

    try:
        inspect_file_magic(file.stream, file.filename, allowed_category="resume")
    except FileValidationError as e:
        flash(f"File upload error: {str(e)}", "error")
        return redirect(url_for("candidate.my_resumes"))

    orig_filename = secure_filename(file.filename)
    stored_filename = generate_secure_stored_filename(orig_filename)
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
    file.save(filepath)

    try:
        raw_text = extract_text(filepath)
    except Exception:
        flash("Could not read that resume file — it may be damaged or password-protected.", "error")
        return redirect(url_for("candidate.my_resumes"))

    if not raw_text or len(raw_text.strip()) < 20:
        flash("The uploaded file did not contain enough readable text.", "error")
        return redirect(url_for("candidate.my_resumes"))

    # Parse structured components
    structured_data = parse_resume_to_structured_dict(raw_text)

    # Calculate initial baseline ATS score
    detected_cat = predict_category(raw_text)
    ref_text = category_reference_text(detected_cat) if detected_cat else ""
    ats_score_res = score_resume(raw_text, ref_text)

    # Name and role
    custom_name = (request.form.get("resume_name") or "").strip()
    if not custom_name:
        custom_name = orig_filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()

    custom_role = (request.form.get("target_role") or "").strip()
    if not custom_role:
        custom_role = structured_data.get("headline") or (detected_cat.title() if detected_cat else "General Role")

    # Check primary
    has_primary = Resume.query.filter_by(candidate_id=current_user.id, is_primary=True).first() is not None
    make_primary = bool(request.form.get("is_primary")) or not has_primary

    if make_primary:
        Resume.query.filter_by(candidate_id=current_user.id).update({"is_primary": False})

    new_resume = Resume(
        candidate_id=current_user.id,
        source="upload",
        original_filename=orig_filename,
        stored_filename=stored_filename,
        raw_text=raw_text,
        name=custom_name,
        target_role=custom_role,
        last_ats_score=ats_score_res["score"],
        last_matched_keywords=", ".join(ats_score_res.get("matched_keywords", [])),
        last_missing_keywords=", ".join(ats_score_res.get("missing_keywords", [])),
        is_primary=make_primary,
    )
    db.session.add(new_resume)
    db.session.commit()

    flash(f"Resume '{custom_name}' uploaded successfully!", "success")
    return redirect(url_for("candidate.my_resumes"))


@candidate_bp.route("/api/resumes/<int:resume_id>/preview", methods=["GET"])
@role_required("candidate")
def api_resume_preview(resume_id):
    """Return structured and plain text resume data for in-browser modal viewing."""
    resume = Resume.query.filter_by(id=resume_id, candidate_id=current_user.id).first_or_404()
    structured = parse_resume_to_structured_dict(resume.raw_text or "", resume.name or "", resume.target_role or "")
    return jsonify({
        "status": "success",
        "resume": {
            "id": resume.id,
            "name": resume.name or "Untitled Resume",
            "target_role": resume.target_role or "General Role",
            "source": resume.source,
            "created_at": resume.created_at.strftime("%d %b %Y"),
            "is_primary": resume.is_primary,
            "last_ats_score": round(resume.last_ats_score) if resume.last_ats_score is not None else None,
            "last_matched_keywords": resume.last_matched_keywords or "",
            "raw_text": resume.raw_text or "",
            "structured": structured,
            "pdf_url": url_for("candidate.download_resume_pdf", resume_id=resume.id),
            "edit_url": url_for("candidate.resume_builder", resume_id=resume.id),
        }
    })


@candidate_bp.route("/resumes/<int:resume_id>/pdf")
@role_required("candidate")
def download_resume_pdf(resume_id):
    resume = Resume.query.filter_by(id=resume_id, candidate_id=current_user.id).first_or_404()
    structured = parse_resume_to_structured_dict(resume.raw_text, resume.name, resume.target_role)
    template = structured.get("template", "modern")
    pdf_buffer = build_structured_resume_pdf(structured, template=template)

    raw_name = (structured.get("personal", {}).get("full_name") or resume.name or "Candidate").strip()
    clean_parts = re.sub(r"[^\w\s-]", "", raw_name).split()
    clean_name = "_".join(clean_parts) or "Candidate"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"Zentra_Resume_for_{clean_name}.pdf",
    )


@candidate_bp.route("/resume-builder/download-pdf", methods=["POST"])
@role_required("candidate")
def export_builder_pdf():
    data = request.get_json() or {}
    resume_data = data.get("resume_data") or {}
    template = data.get("template", "modern")

    pdf_buffer = build_structured_resume_pdf(resume_data, template=template)
    raw_name = (resume_data.get("personal", {}).get("full_name") or "Candidate").strip()
    clean_parts = re.sub(r"[^\w\s-]", "", raw_name).split()
    clean_name = "_".join(clean_parts) or "Candidate"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"Zentra_Resume_for_{clean_name}.pdf",
    )


@candidate_bp.route("/settings", methods=["GET", "POST"])
@role_required("candidate")
def settings():
    form = ProfileSettingsForm(obj=current_user)

    if form.validate_on_submit():
        current_user.full_name = form.full_name.data.strip()
        current_user.headline = form.headline.data
        current_user.phone = form.phone.data
        current_user.location = form.location.data
        current_user.skills = form.skills.data
        current_user.bio = form.bio.data
        for field in ("github_url", "linkedin_url", "portfolio_url", "preferred_job_role", "preferred_location", "work_preference", "expected_salary", "experience_level"):
            setattr(current_user, field, getattr(form, field).data or None)
        uploaded = form.avatar.data
        if not current_user.avatar_filename and (not uploaded or not uploaded.filename):
            flash("Profile photo is required. Please upload a clear photo of yourself.", "error")
            return redirect(url_for("candidate.settings"))
        if uploaded and uploaded.filename:
            try:
                inspect_file_magic(uploaded.stream, uploaded.filename, allowed_category="avatar")
            except FileValidationError as e:
                flash(f"Avatar upload security error: {str(e)}", "error")
                return redirect(url_for("candidate.settings"))

            clean_name = secure_filename(uploaded.filename)
            filename = f"avatar_{generate_secure_stored_filename(clean_name)}"
            uploaded.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
            current_user.avatar_filename = filename
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("candidate.settings"))

    career_entries = {
        entry_type: [entry for entry in current_user.career_entries if entry.entry_type == entry_type]
        for entry_type in CareerEntry.TYPES
    }
    return render_template("candidate/settings.html", form=form, career_entries=career_entries)


@candidate_bp.route("/api/retouch-bio", methods=["POST"])
@role_required("candidate")
def api_retouch_bio():
    data = request.get_json(silent=True) or {}
    raw_bio = str(data.get("raw_bio", "")).strip()
    headline = str(data.get("headline", "")).strip() or (current_user.headline or "")
    skills_raw = str(data.get("skills", "")).strip() or (current_user.skills or "")
    skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
    target_role = str(data.get("target_role", "")).strip() or (current_user.preferred_job_role or headline or "")

    retouched_text, provider = ai_service.retouch_bio(
        raw_bio=raw_bio,
        headline=headline,
        skills=skills,
        target_role=target_role,
    )
    return jsonify({
        "status": "success",
        "retouched_bio": retouched_text,
        "provider": provider,
    })


@candidate_bp.route("/applications")
@role_required("candidate")
def applications():
    status = request.args.get("status", "all")
    query = Application.query.options(
        joinedload(Application.job).joinedload(Job.recruiter_profile)
    ).filter_by(candidate_id=current_user.id)
    if status != "all" and status in Application.STATUSES:
        query = query.filter_by(status=status)
    return render_template("candidate/applications.html", applications=query.order_by(Application.applied_at.desc()).all(), active_status=status)


@candidate_bp.route("/ats-history")
@role_required("candidate")
def ats_history():
    history = Resume.query.filter_by(candidate_id=current_user.id).filter(Resume.last_ats_score.isnot(None)).order_by(Resume.created_at.desc()).all()
    improvement = round(history[0].last_ats_score - history[-1].last_ats_score) if len(history) > 1 else None
    return render_template("candidate/ats_history.html", history=history, improvement=improvement)


@candidate_bp.route("/notifications")
@role_required("candidate")
def notifications():
    items = current_user.notifications
    for item in items:
        item.is_read = True
    db.session.commit()
    return render_template("candidate/notifications.html", notifications=items)


@candidate_bp.route("/privacy", methods=["GET", "POST"])
@role_required("candidate")
def privacy_settings():
    privacy_form = PrivacySettingsForm(obj=current_user)
    delete_form = DeleteAccountForm()
    if privacy_form.validate_on_submit():
        slug = (privacy_form.public_slug.data or "").strip().lower() or _default_public_slug()
        taken = User.query.filter(User.public_slug == slug, User.id != current_user.id).first()
        if taken:
            privacy_form.public_slug.errors.append("That profile address is already taken. Try another.")
        else:
            current_user.public_slug = slug
            current_user.public_profile_enabled = privacy_form.public_profile_enabled.data
            current_user.recruiter_discoverable = privacy_form.recruiter_discoverable.data
            current_user.public_resume_enabled = privacy_form.public_resume_enabled.data
            db.session.commit()
            flash("Privacy settings saved.", "success")
            return redirect(url_for("candidate.privacy_settings"))
    return render_template("candidate/privacy_settings.html", privacy_form=privacy_form, delete_form=delete_form)


@candidate_bp.route("/delete-account", methods=["POST"])
@role_required("candidate")
def delete_account():
    form = DeleteAccountForm()
    if not form.validate_on_submit() or form.confirmation.data.strip() != "DELETE":
        flash("To delete your account, type DELETE exactly as shown.", "error")
        return redirect(url_for("candidate.privacy_settings"))

    avatar_file = current_user.avatar_filename
    resume_filenames = [r.stored_filename for r in current_user.resumes if r.stored_filename]

    db.session.delete(current_user)
    db.session.commit()
    logout_user()

    upload_folder = os.path.abspath(current_app.config["UPLOAD_FOLDER"])

    # Clean avatar file
    if avatar_file:
        file_path = os.path.abspath(os.path.join(upload_folder, avatar_file))
        if os.path.commonpath([upload_folder, file_path]) == upload_folder and os.path.isfile(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

    # Reference-aware cleanup for resumes: only delete if no remaining resume references it
    for r_file in resume_filenames:
        still_referenced = Resume.query.filter_by(stored_filename=r_file).first() is not None
        if not still_referenced:
            file_path = os.path.abspath(os.path.join(upload_folder, r_file))
            if os.path.commonpath([upload_folder, file_path]) == upload_folder and os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

    flash("Your account and stored profile data have been deleted.", "success")
    return redirect(url_for("main.landing"))


def _default_public_slug():
    base = secure_filename(current_user.full_name).lower().replace("_", "-").strip("-") or "candidate"
    slug = base
    suffix = 2
    while User.query.filter(User.public_slug == slug, User.id != current_user.id).first():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


@candidate_bp.route("/applications/<int:application_id>")
@role_required("candidate")
def application_detail(application_id):
    application = Application.query.filter_by(id=application_id, candidate_id=current_user.id).first_or_404()
    return render_template("candidate/application_detail.html", application=application)


@candidate_bp.route("/jobs/<int:job_id>/save", methods=["POST"])
@role_required("candidate")
def save_job(job_id):
    job = Job.query.filter_by(id=job_id, status=Job.STATUS_ACTIVE).first_or_404()
    if not SavedJob.query.filter_by(candidate_id=current_user.id, job_id=job.id).first():
        db.session.add(SavedJob(candidate_id=current_user.id, job_id=job.id))
        db.session.commit()
        flash("Job saved. You can find it under Saved jobs.", "success")
    return redirect(request.referrer or url_for("main.job_detail", job_id=job.id))


@candidate_bp.route("/jobs/<int:job_id>/unsave", methods=["POST"])
@role_required("candidate")
def unsave_job(job_id):
    saved = SavedJob.query.filter_by(candidate_id=current_user.id, job_id=job_id).first_or_404()
    db.session.delete(saved)
    db.session.commit()
    flash("Job removed from saved jobs.", "info")
    return redirect(request.referrer or url_for("candidate.saved_jobs"))


@candidate_bp.route("/saved-jobs")
@role_required("candidate")
def saved_jobs():
    saved = (
        SavedJob.query.options(joinedload(SavedJob.job).joinedload(Job.recruiter_profile))
        .filter_by(candidate_id=current_user.id)
        .order_by(SavedJob.created_at.desc())
        .all()
    )
    resume = Resume.get_primary(current_user.id)
    items = [(item, score_resume_for_job(resume.raw_text, item.job)["score"] if resume else None) for item in saved]
    return render_template("candidate/saved_jobs.html", saved_jobs=items)


def _parse_career_date_order(start_str: str, end_str: str) -> bool:
    """Returns False if end_str is strictly before start_str."""
    if not start_str or not end_str:
        return True
    
    s_clean = start_str.strip().lower()
    e_clean = end_str.strip().lower()

    if any(w in e_clean for w in ("present", "current", "ongoing", "now", "till date", "til date")):
        return True

    s_years = re.findall(r"\b(19\d\d|20\d\d)\b", start_str)
    e_years = re.findall(r"\b(19\d\d|20\d\d)\b", end_str)

    if s_years and e_years:
        s_yr = int(s_years[-1])
        e_yr = int(e_years[-1])
        if e_yr < s_yr:
            return False
        if e_yr == s_yr:
            months = {
                "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            }
            s_mo = next((m_val for m_k, m_val in months.items() if m_k in s_clean), None)
            e_mo = next((m_val for m_k, m_val in months.items() if m_k in e_clean), None)
            if s_mo and e_mo and e_mo < s_mo:
                return False
    return True


@candidate_bp.route("/profile/career-entry", methods=["POST"])
@role_required("candidate")
def add_career_entry():
    entry_type = (request.form.get("entry_type") or "").strip()
    title = (request.form.get("title") or "").strip()
    organization = (request.form.get("organization") or "").strip() or None
    start_date = (request.form.get("start_date") or "").strip() or None
    end_date = (request.form.get("end_date") or "").strip() or None
    location = (request.form.get("entry_location") or "").strip() or None
    description = (request.form.get("description") or "").strip() or None
    credential_url = (request.form.get("credential_url") or "").strip() or None

    if entry_type not in CareerEntry.TYPES or not title:
        flash("Choose an entry type and enter its title before saving.", "error")
        return redirect(url_for("candidate.settings", step=3) + "#career")

    if len(title) < 2:
        flash("Title must be at least 2 characters.", "error")
        return redirect(url_for("candidate.settings", step=3) + "#career")

    if entry_type != "project" and (not organization or len(organization) < 2):
        flash("Organisation / Institution is required and must be at least 2 characters.", "error")
        return redirect(url_for("candidate.settings", step=3) + "#career")

    if start_date and end_date and not _parse_career_date_order(start_date, end_date):
        flash(f"Invalid dates: End date ('{end_date}') cannot be earlier than start date ('{start_date}').", "error")
        return redirect(url_for("candidate.settings", step=3) + "#career")

    # Prevent duplicate career entries
    existing = CareerEntry.query.filter(
        CareerEntry.candidate_id == current_user.id,
        CareerEntry.entry_type == entry_type,
        db.func.lower(CareerEntry.title) == title.lower(),
        db.func.lower(db.func.coalesce(CareerEntry.organization, '')) == (organization or '').lower(),
    ).first()
    if existing:
        flash(f"This {entry_type} entry ('{title}') is already added to your profile.", "error")
        return redirect(url_for("candidate.settings", step=3) + "#career")

    entry = CareerEntry(
        candidate_id=current_user.id,
        entry_type=entry_type,
        title=title,
        organization=organization,
        location=location,
        start_date=start_date,
        end_date=end_date,
        description=description,
        credential_url=credential_url,
    )
    db.session.add(entry)
    db.session.commit()
    flash(f"{entry_type.title()} added to your profile.", "success")
    return redirect(url_for("candidate.settings", step=3) + "#career")


@candidate_bp.route("/profile/career-entry/<int:entry_id>/delete", methods=["POST"])
@role_required("candidate")
def delete_career_entry(entry_id):
    entry = CareerEntry.query.filter_by(id=entry_id, candidate_id=current_user.id).first_or_404()
    db.session.delete(entry)
    db.session.commit()
    flash("Career entry removed.", "success")
    return redirect(url_for("candidate.settings", step=3) + "#career")


@candidate_bp.route("/jobs/<int:job_id>/apply", methods=["POST"])
@role_required("candidate")
def apply(job_id):
    job = Job.query.get_or_404(job_id)

    # Server-side job status enforcement — never rely on frontend button visibility
    if job.status != Job.STATUS_ACTIVE:
        flash("This job is no longer accepting applications.", "error")
        return redirect(url_for("main.job_detail", job_id=job.id))

    # Server-side deadline enforcement — backend check independent of UI
    if job.application_deadline and utcnow() > job.application_deadline:
        flash("The application deadline for this job has passed.", "error")
        return redirect(url_for("main.job_detail", job_id=job.id))

    resume = Resume.get_primary(current_user.id)
    if resume is None:
        flash("Add a resume via Resume AI before applying.", "error")
        return redirect(url_for("candidate.resume_ai"))

    already_applied = Application.query.filter_by(job_id=job.id, candidate_id=current_user.id).first()
    if already_applied:
        flash("You've already applied to this job.", "info")
        return redirect(url_for("main.job_detail", job_id=job.id))

    result = score_resume_for_job(resume.raw_text, job)

    application = Application(
        job_id=job.id,
        candidate_id=current_user.id,
        resume_id=resume.id,
        match_score=result["score"],
        scored_at=utcnow(),
    )
    db.session.add(application)
    db.session.flush()
    db.session.add(ApplicationEvent(application_id=application.id, status=Application.STATUS_APPLIED, note="Application submitted"))
    db.session.add(Notification(
        candidate_id=current_user.id,
        title="Application submitted",
        message=f"Your application for {job.title} at {job.recruiter_profile.company_name} was submitted.",
        link=url_for("candidate.application_detail", application_id=application.id),
    ))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("You've already applied to this job.", "info")
        return redirect(url_for("main.job_detail", job_id=job.id))

    flash("Application submitted.", "success")
    return redirect(url_for("main.job_detail", job_id=job.id))

