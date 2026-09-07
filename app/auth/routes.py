from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app import db
from app.models.user import User
from app.auth.forms import LoginForm, SignupForm, ResetPasswordRequestForm, ResetPasswordForm
from app.utils.security import limiter, login_tracker
from app.utils.mail import send_password_reset_email, send_welcome_email

auth_bp = Blueprint("auth", __name__, template_folder="../templates/auth")

_RESET_SALT = "password-reset"
_RESET_MAX_AGE_SECONDS = 3600  # 1 hour


def _generate_reset_token(user):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    payload = {
        "email": user.email,
        "pwd_fp": user.password_hash[-12:] if user.password_hash else "",
    }
    return serializer.dumps(payload, salt=_RESET_SALT)


def _verify_reset_token(token):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        payload = serializer.loads(token, salt=_RESET_SALT, max_age=_RESET_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired, Exception):
        return None

    if not isinstance(payload, dict) or "email" not in payload:
        return None

    user = User.query.filter_by(email=payload["email"]).first()
    if not user or not user.is_active_account:
        return None

    # Enforce one-time use: invalidates token if password was already changed
    if payload.get("pwd_fp") != (user.password_hash[-12:] if user.password_hash else ""):
        return None

    return user


def _redirect_for_role(user):
    if user.is_admin:
        return redirect(url_for("admin.dashboard"))
    if user.is_recruiter:
        return redirect(url_for("recruiter.dashboard"))
    return redirect(url_for("candidate.dashboard"))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTH", "5 per minute; 20 per hour"))
def login():
    if current_user.is_authenticated:
        return _redirect_for_role(current_user)

    # Auto-seed removed: seeding from a login page is a security anti-pattern
    # (unauthenticated DB writes, hardcoded credentials). Use `python seed_microsoft.py` instead.

    login_form = LoginForm(prefix="login")
    signup_form = SignupForm(prefix="signup")

    if login_form.submit.data and login_form.validate_on_submit():
        ip = request.remote_addr or ""
        email = (login_form.email.data or "").lower().strip()

        # Check exponential backoff cooldown
        backoff = login_tracker.get_backoff_info(ip=ip, email=email)
        if backoff["is_throttled"]:
            flash(
                f"Too many failed login attempts. Please wait {backoff['remaining_cooldown']} seconds before trying again.",
                "error",
            )
            return render_template("auth/login.html", login_form=login_form, signup_form=signup_form)

        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(login_form.password.data):
            login_tracker.record_failure(ip=ip, email=email)
            flash("Incorrect email or password.", "error")
        elif not user.is_active_account:
            login_tracker.record_failure(ip=ip, email=email)
            flash("This account has been disabled. Contact support.", "error")
        else:
            login_tracker.record_success(ip=ip, email=email)
            login_user(user, remember=login_form.remember_me.data)
            next_page = request.args.get("next")
            if next_page and next_page.startswith("/") and not next_page.startswith("//"):
                return redirect(next_page)
            return _redirect_for_role(user)

    return render_template("auth/login.html", login_form=login_form, signup_form=signup_form)


@auth_bp.route("/signup", methods=["POST"])
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTH", "5 per minute; 20 per hour"))
def signup():
    """Candidate signup only. Recruiters register via recruiter.register."""
    login_form = LoginForm(prefix="login")
    signup_form = SignupForm(prefix="signup")

    if signup_form.validate_on_submit():
        from app.models.admin_setting import AdminSetting
        if AdminSetting.get("registration_open", "true").lower() == "false":
            flash("Candidate registration is currently disabled by administrator.", "error")
            return render_template("auth/login.html", login_form=login_form, signup_form=signup_form)

        existing = User.query.filter_by(email=signup_form.email.data.lower().strip()).first()
        if existing:
            flash("An account with this email already exists.", "error")
        else:
            user = User(
                full_name=signup_form.full_name.data.strip(),
                email=signup_form.email.data.lower().strip(),
                role=User.ROLE_CANDIDATE,
            )
            user.set_password(signup_form.password.data)
            db.session.add(user)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash("An account with this email already exists.", "error")
                return render_template("auth/login.html", login_form=login_form, signup_form=signup_form)
            login_user(user)
            send_welcome_email(user.email, user.full_name)
            flash("Welcome to Zentra!", "success")
            return redirect(url_for("candidate.dashboard"))

    return render_template("auth/login.html", login_form=login_form, signup_form=signup_form)


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    flash("You've been signed out.", "info")
    return redirect(url_for("main.landing"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTH", "5 per minute; 20 per hour"))
def forgot_password():
    if current_user.is_authenticated:
        return _redirect_for_role(current_user)

    form = ResetPasswordRequestForm()
    reset_link = None

    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        is_dev = current_app.debug or current_app.testing or current_app.config.get("ENV") == "development"
        if user and user.is_active_account:
            token = _generate_reset_token(user)
            link = url_for("auth.reset_password", token=token, _external=True)
            send_password_reset_email(user.email, link)
            if is_dev:
                reset_link = link
                flash("Development mode: A reset link has been generated below.", "info")
            else:
                flash("If that email is registered, instructions to reset your password have been sent.", "info")
        else:
            flash("If that email is registered, instructions to reset your password have been sent.", "info")

    return render_template("auth/forgot_password.html", form=form, reset_link=reset_link)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTH", "5 per minute; 20 per hour"))
def reset_password(token):
    user = _verify_reset_token(token)
    if user is None:
        flash("That reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash("Password updated — you can log in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form)


# ---------------------------------------------------------------------------
# Google OAuth — /auth/google/login  and  /auth/google/callback
# ---------------------------------------------------------------------------
from app.auth.oauth import oauth  # noqa: E402 — avoids circular import at module top


@auth_bp.route("/google/login")
def google_login():
    """Redirect the user to Google's OAuth consent screen."""
    if current_user.is_authenticated:
        return _redirect_for_role(current_user)
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
@limiter.limit(lambda: current_app.config.get("RATELIMIT_AUTH", "5 per minute; 20 per hour"))
def google_callback():
    """Handle the token exchange after Google redirects back."""
    from sqlalchemy.exc import IntegrityError

    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash("Google sign-in was cancelled or failed. Please try again.", "error")
        return redirect(url_for("auth.login"))

    # ID-token contains verified claims — no extra /userinfo call needed
    id_info = token.get("userinfo") or {}
    google_id = id_info.get("sub", "")
    email = (id_info.get("email") or "").lower().strip()
    full_name = (id_info.get("name") or "").strip() or email.split("@")[0]

    if not google_id or not email:
        flash("Could not retrieve your Google account details. Please try again.", "error")
        return redirect(url_for("auth.login"))

    # 1. Look up by google_id first (returning Google user)
    user = User.query.filter_by(google_id=google_id).first()

    # 2. Fall back to email match — links an existing email account to Google
    if user is None:
        user = User.query.filter_by(email=email).first()
        if user is not None:
            # Bind google_id so future logins skip the email lookup
            user.google_id = google_id
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

    # 3. Brand-new user — create a candidate account
    if user is None:
        from app.models.admin_setting import AdminSetting
        if AdminSetting.get("registration_open", "true").lower() == "false":
            flash("New registrations are currently closed. Please contact support.", "error")
            return redirect(url_for("auth.login"))

        user = User(
            full_name=full_name,
            email=email,
            google_id=google_id,
            role=User.ROLE_CANDIDATE,
            is_active_account=True,
        )
        # No password set — google_id is the credential
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An account with that email already exists. Please sign in with your password.", "error")
            return redirect(url_for("auth.login"))

    if not user.is_active_account:
        flash("This account has been disabled. Please contact support.", "error")
        return redirect(url_for("auth.login"))

    login_user(user)
    flash(f"Welcome, {user.full_name.split()[0]}!", "success")
    return _redirect_for_role(user)
