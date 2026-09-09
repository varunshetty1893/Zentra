def register_filters(app):
    @app.template_filter("split_csv")
    def split_csv(value):
        """Turn a comma-separated string column into a clean list for loops."""
        if not value:
            return []
        return [v.strip() for v in value.split(",") if v.strip()]

    @app.template_filter("salary_range")
    def salary_range(job):
        if job.salary_min and job.salary_max:
            return f"₹{job.salary_min}–{job.salary_max} LPA"
        return "Not disclosed"

    @app.template_filter("timesince")
    def timesince(value):
        """Humanize a naive-UTC datetime as '2 days ago' / 'Just now' etc."""
        if not value:
            return ""
        from app.utils.time import utcnow
        delta = utcnow() - value
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "Just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min{'s' if minutes != 1 else ''} ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hr{'s' if hours != 1 else ''} ago"
        days = hours // 24
        if days < 30:
            return f"{days} day{'s' if days != 1 else ''} ago"
        months = days // 30
        if months < 12:
            return f"{months} month{'s' if months != 1 else ''} ago"
        years = months // 12
        return f"{years} yr{'s' if years != 1 else ''} ago"
