"""
Dedicated Seed Script: Microsoft Corporation (Company & Recruiter), 12 Realistic Jobs, and 1 Realistic Candidate.

Usage:
  python seed_microsoft.py
"""

import sys
import os
from datetime import datetime, timedelta

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import create_app, db
from app.utils.time import utcnow
from app.models.user import User
from app.models.recruiter_profile import RecruiterProfile
from app.models.job import Job
from app.models.resume import Resume
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.career_entry import CareerEntry
from app.ml.ats_scorer import score_resume_for_job

def seed_microsoft():
    from flask import has_app_context
    if has_app_context():
        _do_seed()
    else:
        app = create_app()
        with app.app_context():
            _do_seed()

def _do_seed():
    db.create_all()

    print("=" * 65)
    print("  ZENTRA SEEDER — MICROSOFT COMPANY, JOBS & REALISTIC CANDIDATE")
    print("=" * 65)

    # -------------------------------------------------------------
    # 0. ADMIN ACCOUNT
    # -------------------------------------------------------------
    admin_email = "admin@zentra.example.com"
    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(
            full_name="Platform Admin",
            email=admin_email,
            role=User.ROLE_ADMIN,
            is_active_account=True,
        )
        admin.set_password("Admin@123")
        db.session.add(admin)
        db.session.flush()
        print(f"[+] Created Admin User: {admin_email} / Admin@123")
        print("    *** Change this password immediately after first login! ***")
    else:
        # Security fix: never reset an existing admin's password or re-enable
        # a disabled account from a seed script.  The original code silently
        # undid any admin-initiated password change or account disable on every
        # seed run (including the now-removed web-accessible /seed-demo route).
        print(f"[*] Admin user already exists — leaving credentials untouched: {admin_email}")

    # -------------------------------------------------------------
    # 1. RECRUITER & COMPANY: Microsoft Corporation
    # -------------------------------------------------------------
    recruiter_email = "recruiter@microsoft.com"
    recruiter_pass = "Microsoft@2026"
    
    user = User.query.filter_by(email=recruiter_email).first()
    if not user:
        user = User(
            full_name="Sarah Jenkins",
            email=recruiter_email,
            role=User.ROLE_RECRUITER,
            is_active_account=True,
        )
        user.set_password(recruiter_pass)
        db.session.add(user)
        db.session.flush()
        print(f"[+] Created Recruiter User: {recruiter_email} / {recruiter_pass}")
    else:
        # Security fix: do not reset password or re-enable account on re-seed.
        print(f"[*] Recruiter user already exists — leaving credentials untouched: {recruiter_email}")


    profile = RecruiterProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        profile = RecruiterProfile(
            user_id=user.id,
            company_name="Microsoft",
            industry="technology",
            company_size="500+",
            company_website="https://www.microsoft.com",
            contact_role="Principal Technical Talent Lead",
            phone="+1 (425) 882-8080",
            hiring_needs="Hiring world-class engineering, AI research, cloud architecture, and design talent across Azure, Developer Division, Microsoft 365, and AI Platform groups.",
            approval_status=RecruiterProfile.STATUS_APPROVED,
            submitted_at=utcnow() - timedelta(days=60),
            reviewed_at=utcnow() - timedelta(days=59),
        )
        db.session.add(profile)
        db.session.flush()
        print(f"[+] Created Recruiter Profile for Microsoft (Approved)")
    else:
        profile.company_name = "Microsoft"
        profile.industry = "technology"
        profile.company_size = "500+"
        profile.company_website = "https://www.microsoft.com"
        profile.contact_role = "Principal Technical Talent Lead"
        profile.phone = "+1 (425) 882-8080"
        profile.approval_status = RecruiterProfile.STATUS_APPROVED
        db.session.flush()
        print(f"[*] Updated Recruiter Profile for Microsoft")

    # -------------------------------------------------------------
    # 2. MIN 10 REALISTIC MICROSOFT JOBS (12 JOBS TOTAL)
    # -------------------------------------------------------------
    jobs_data = [
        {
            "title": "Principal Cloud Solution Architect - Azure Infrastructure",
            "description": (
                "Join Microsoft's Azure Core engineering group to architect and deliver mission-critical cloud solutions "
                "for global enterprise customers. You will partner directly with tier-1 enterprise architects, drive architectural "
                "excellence in hyperscale distributed environments, and influence the core Azure platform roadmap."
            ),
            "responsibilities": (
                "• Lead architectural design and review of multi-region, highly available Azure cloud architectures.\n"
                "• Guide enterprise migrations and cloud-native transformations using Kubernetes (AKS), Terraform, and service meshes.\n"
                "• Partner with Azure product engineering to resolve platform-level scalability bottlenecks.\n"
                "• Champion security posture, zero-trust network topology, and FinOps cloud-cost optimization strategies."
            ),
            "requirements": (
                "• 8+ years of experience in distributed cloud architecture, systems engineering, or enterprise infrastructure.\n"
                "• Deep expertise with Azure (or AWS/GCP), Infrastructure-as-Code (Terraform/Bicep), and Linux internals.\n"
                "• Proven track record architecting 99.999% uptime systems handling petabyte-scale throughput.\n"
                "• Strong background in microservices, containerization, and distributed consensus."
            ),
            "required_skills_raw": "Azure, Cloud Architecture, Kubernetes, Terraform, Distributed Systems, Python, Linux",
            "preferred_skills_raw": "Go, BGP, Zero Trust, FinOps, CI/CD, Azure DevOps",
            "job_type": "full_time",
            "work_mode": "hybrid",
            "experience_level": "lead",
            "location": "Redmond, WA / Bangalore, India",
            "salary_min": 45,
            "salary_max": 75,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=90),
        },
        {
            "title": "Senior Applied AI Scientist - Copilot & Foundation Models",
            "description": (
                "Microsoft AI Platform team is seeking a Senior Applied AI Scientist to push the frontiers of Generative AI. "
                "You will work at the intersection of deep learning research and production scale, fine-tuning large language models, "
                "designing efficient inference systems, and powering the next generation of Microsoft Copilot."
            ),
            "responsibilities": (
                "• Design, train, and fine-tune large-scale language and multimodal foundation models.\n"
                "• Implement state-of-the-art RLHF, DPO, and context-window extension techniques.\n"
                "• Optimize model inference latency and throughput using ONNX Runtime, TensorRT-LLM, and vLLM.\n"
                "• Collaborate with product teams to translate cutting-edge LLM capabilities into intuitive user experiences."
            ),
            "requirements": (
                "• MS or PhD in Computer Science, Machine Learning, Computational Linguistics, or related quantitative field.\n"
                "• 4+ years of hands-on experience building and shipping deep learning / NLP models into production.\n"
                "• High proficiency in PyTorch, Python, Hugging Face Transformers, and distributed GPU training (DeepSpeed/Megatron).\n"
                "• Strong publication record or demonstrated impact in production GenAI systems."
            ),
            "required_skills_raw": "Python, PyTorch, Large Language Models, NLP, Transformers, Deep Learning, MLOps",
            "preferred_skills_raw": "CUDA, Triton, DeepSpeed, ONNX, RLHF, Azure AI Studio",
            "job_type": "full_time",
            "work_mode": "remote",
            "experience_level": "senior",
            "location": "Remote - Global / Hyderabad, India",
            "salary_min": 50,
            "salary_max": 85,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=75),
        },
        {
            "title": "Senior Full Stack Engineer - Microsoft 365 Core Collaboration",
            "description": (
                "The Microsoft 365 Collaboration team powers real-time communication and document co-authoring for over 300 million daily active users. "
                "We are seeking a high-caliber Full Stack Engineer to build lightning-fast web components and resilient backend services."
            ),
            "responsibilities": (
                "• Build high-performance, accessible web applications using TypeScript, React, and modern CSS architecture.\n"
                "• Develop low-latency distributed backend microservices in C# / .NET Core with Azure Cosmos DB.\n"
                "• Implement conflict-free collaborative editing algorithms (CRDT/OT) over WebSockets.\n"
                "• Maintain 99.99% availability through automated telemetry, proactive canary deployments, and unit test coverage."
            ),
            "requirements": (
                "• 5+ years of software engineering experience across frontend and backend stacks.\n"
                "• Strong mastery of modern JavaScript/TypeScript, React internals, and state management.\n"
                "• Solid experience with C#/.NET Core, Java, or Go backend services.\n"
                "• Thorough understanding of web security, CORS, CSRF, and browser performance optimization."
            ),
            "required_skills_raw": "TypeScript, React, C#, .NET Core, WebSockets, REST APIs, Microservices",
            "preferred_skills_raw": "Azure Cosmos DB, GraphQL, WebAssembly, Jest, Playwright",
            "job_type": "full_time",
            "work_mode": "hybrid",
            "experience_level": "senior",
            "location": "Bangalore, India",
            "salary_min": 32,
            "salary_max": 55,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=60),
        },
        {
            "title": "Site Reliability Engineering Lead (Azure SRE) - Global Backbone",
            "description": (
                "Azure's Global Network connects thousands of datacenters across the planet. As SRE Lead, you will drive the reliability, "
                "automation, and disaster resiliency of one of the world's largest software-defined networks."
            ),
            "responsibilities": (
                "• Architect automated self-healing systems and traffic re-routing pipelines for Azure cloud infrastructure.\n"
                "• Define and enforce SLIs, SLOs, and Error Budgets across multi-tenant platform services.\n"
                "• Lead critical incident retrospectives, root-cause analyses (RCA), and preventative engineering work.\n"
                "• Build automated chaos engineering simulations to validate system tolerance under catastrophic failure modes."
            ),
            "requirements": (
                "• 7+ years in Site Reliability Engineering, DevOps, or Infrastructure Systems Engineering.\n"
                "• Advanced proficiency in Go, Python, or Rust for systems automation.\n"
                "• Extensive experience with Kubernetes, Linux networking, eBPF, and distributed observability tools.\n"
                "• Experience leading on-call rotations and incident management in hyper-scale environments."
            ),
            "required_skills_raw": "SRE, Kubernetes, Go, Python, Azure, Terraform, Observability, Prometheus",
            "preferred_skills_raw": "eBPF, Chaos Engineering, Envoy, Grafana, Distributed Tracing",
            "job_type": "full_time",
            "work_mode": "remote",
            "experience_level": "lead",
            "location": "Remote - India / Seattle, WA",
            "salary_min": 48,
            "salary_max": 80,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=90),
        },
        {
            "title": "Principal Product Manager - Developer Division & GitHub Tools",
            "description": (
                "Shape the tools millions of software engineers rely on every single day. The Microsoft Developer Division is looking for "
                "a visionary Principal PM to define product strategy for next-generation developer workflows, VS Code integrations, and GitHub enterprise features."
            ),
            "responsibilities": (
                "• Define the 3-year product vision, roadmap, and quarterly OKRs for developer platform tools.\n"
                "• Synthesize customer signals, community feedback, and developer telemetry into clear product requirement documents.\n"
                "• Partner closely with engineering leads and UX designers to ship intuitive, developer-delighting workflows.\n"
                "• Represent Microsoft at major developer conferences and open-source community forums."
            ),
            "requirements": (
                "• 6+ years of technical product management experience in SaaS, developer tooling, or cloud platforms.\n"
                "• Strong engineering foundation with the ability to dive into API specifications and code architecture.\n"
                "• Exceptional written communication, customer empathy, and executive stakeholder alignment skills.\n"
                "• Passion for developer productivity, open source, and developer-first user experiences."
            ),
            "required_skills_raw": "Product Management, Developer Experience, System Architecture, Agile, API Design, Technical Roadmap",
            "preferred_skills_raw": "GitHub Actions, VS Code, Open Source, Developer Evangelism, SQL",
            "job_type": "full_time",
            "work_mode": "hybrid",
            "experience_level": "lead",
            "location": "Redmond, WA / Remote",
            "salary_min": 42,
            "salary_max": 70,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=80),
        },
        {
            "title": "Senior Data Platform Engineer - Microsoft Fabric & Synapse",
            "description": (
                "Build the next generation of unified data analytics at Microsoft. As part of the Microsoft Fabric team, you will engineer "
                "massively parallel processing data engines, lakehouse storage layers, and real-time streaming pipelines for enterprise customers."
            ),
            "responsibilities": (
                "• Engineer distributed data processing engines on Apache Spark, Delta Lake, and OneLake.\n"
                "• Optimize storage parquet formats and caching layers for sub-second query latency over petabytes of data.\n"
                "• Implement robust data governance, lineage tracking, and enterprise-grade encryption at rest and in transit.\n"
                "• Drive CI/CD and automated data validation across complex data pipelines."
            ),
            "requirements": (
                "• 5+ years of software development experience specializing in Big Data infrastructure.\n"
                "• Expert-level knowledge of Apache Spark, Python, Scala, and distributed SQL query engines.\n"
                "• Deep understanding of columnar storage formats, data partitioning strategies, and distributed caching.\n"
                "• Experience with Azure Data Lake Storage, Synapse, or Databricks."
            ),
            "required_skills_raw": "Apache Spark, Python, SQL, Azure Data Lake, Delta Lake, Scala, Data Pipelines",
            "preferred_skills_raw": "Databricks, Kafka, Azure Synapse, Data Governance, Rust",
            "job_type": "full_time",
            "work_mode": "hybrid",
            "experience_level": "senior",
            "location": "Hyderabad, India",
            "salary_min": 35,
            "salary_max": 60,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=60),
        },
        {
            "title": "Senior Security Software Engineer - Microsoft Defender & Threat Intelligence",
            "description": (
                "Defend billions of devices and cloud endpoints from sophisticated nation-state cyber threats. Microsoft Defender team is looking for "
                "an exceptional Security Software Engineer to build kernel-level sensors, behavioral detection engines, and threat intelligence pipelines."
            ),
            "responsibilities": (
                "• Develop low-level, high-performance security telemetry sensors for Windows, Linux, and cloud workloads.\n"
                "• Build behavioral analysis algorithms to detect zero-day vulnerabilities, ransomware, and privilege escalation.\n"
                "• Collaborate with the Microsoft Threat Intelligence Center (MSTIC) to operationalize threat signatures.\n"
                "• Ensure all sensor code adheres to rigorous memory safety, battery efficiency, and CPU quota limits."
            ),
            "requirements": (
                "• 5+ years in security engineering, systems programming, or malware reverse engineering.\n"
                "• Proficiency in C, C++, or Rust with deep knowledge of OS internals (Windows or Linux kernel).\n"
                "• Solid understanding of common exploit techniques, memory corruption, and modern mitigations.\n"
                "• Experience with static/dynamic binary analysis and disassembly tools (IDA Pro, Ghidra)."
            ),
            "required_skills_raw": "Cybersecurity, C++, Rust, Threat Intelligence, Reverse Engineering, Windows Internals, Cryptography",
            "preferred_skills_raw": "SIEM, MITRE ATT&CK, Kernel Debugging, Memory Safety, Python",
            "job_type": "full_time",
            "work_mode": "remote",
            "experience_level": "senior",
            "location": "Remote - Global / Bangalore, India",
            "salary_min": 40,
            "salary_max": 68,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=90),
        },
        {
            "title": "Senior Design System UX/UI Designer - Fluent Design 2.0",
            "description": (
                "Craft the foundational design language used across Windows, Office, Xbox, and the Web. Join the Fluent Design System team "
                "to define accessible visual patterns, design tokens, micro-interactions, and component guidelines."
            ),
            "responsibilities": (
                "• Design scalable, accessible UI component libraries in Figma used by thousands of Microsoft designers and engineers.\n"
                "• Define cross-platform design tokens supporting seamless dark/light modes, high-contrast, and dynamic scaling.\n"
                "• Conduct comprehensive usability testing and accessibility reviews (WCAG 2.2 AAA).\n"
                "• Partner with web and native frontend engineers to verify visual fidelity and interactive fluidity."
            ),
            "requirements": (
                "• 5+ years of digital product design and design system architecture experience.\n"
                "• Mastery of Figma (auto-layout, components, variants, design tokens) and design handoff tools.\n"
                "• Strong portfolio showcasing systematic design thinking, craft, and attention to typographic detail.\n"
                "• Solid understanding of frontend capabilities, CSS layout models, and motion design principles."
            ),
            "required_skills_raw": "Figma, Design Systems, UI/UX Design, Interaction Design, Accessibility, Prototyping",
            "preferred_skills_raw": "Motion Design, Storybook, HTML/CSS, WCAG, Token Studio",
            "job_type": "full_time",
            "work_mode": "hybrid",
            "experience_level": "senior",
            "location": "Redmond, WA / Remote",
            "salary_min": 28,
            "salary_max": 48,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=60),
        },
        {
            "title": "Frontend Architect - NextGen Edge & Web Experiences",
            "description": (
                "Lead the frontend architectural evolution for Microsoft Edge and web consumer portals. You will drive web performance, "
                "bundle optimization, progressive web app (PWA) standards, and modern component architecture reaching billions of sessions."
            ),
            "responsibilities": (
                "• Architect high-velocity, lightweight frontend frameworks with React, TypeScript, and modern bundlers.\n"
                "• Drive Core Web Vitals optimization (LCP < 1.2s, INP < 100ms, zero layout shift) across high-traffic surfaces.\n"
                "• Establish company-wide frontend best practices, linting rules, and automated performance testing pipelines.\n"
                "• Mentor senior engineers and conduct deep-dive technical architecture reviews."
            ),
            "requirements": (
                "• 8+ years of frontend development experience with 3+ years in architectural or technical lead roles.\n"
                "• World-class proficiency with modern JavaScript/TypeScript, browser rendering engines, and DOM performance.\n"
                "• Deep knowledge of state management, SSR/SSG patterns, and caching strategies.\n"
                "• Experience driving large-scale frontend migrations with zero user downtime."
            ),
            "required_skills_raw": "React, TypeScript, Next.js, Web Performance, Core Web Vitals, CSS/Tailwind, JavaScript",
            "preferred_skills_raw": "PWA, WebAssembly, Edge Computing, Playwright, Micro-frontends",
            "job_type": "full_time",
            "work_mode": "hybrid",
            "experience_level": "lead",
            "location": "Bangalore, India",
            "salary_min": 45,
            "salary_max": 72,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=90),
        },
        {
            "title": "Software Engineering Intern - AI & Cloud Systems (Summer 2026)",
            "description": (
                "Launch your engineering career at Microsoft! Our 12-week summer internship program pairs you with a senior mentor "
                "to ship real features on production Azure and AI services, participate in internal hackathons, and learn from top industry leaders."
            ),
            "responsibilities": (
                "• Design, write, and unit-test production code for cloud services under senior engineer mentorship.\n"
                "• Collaborate with peers on innovative prototype projects during Microsoft Hackathon week.\n"
                "• Present project results, technical learnings, and business impact to engineering leadership.\n"
                "• Participate in technical learning series, mentorship sessions, and executive Q&As."
            ),
            "requirements": (
                "• Currently enrolled in a Bachelor's, Master's, or PhD program in Computer Science or related STEM field.\n"
                "• Strong foundation in Data Structures, Algorithms, and Object-Oriented Programming (Python, C++, Java, or C#).\n"
                "• Good problem-solving mindset and excitement to learn cutting-edge cloud and AI technologies.\n"
                "• Prior project or coursework in distributed systems, web development, or machine learning is a plus."
            ),
            "required_skills_raw": "Python, Data Structures, Algorithms, Git, Computer Science Fundamentals, Problem Solving",
            "preferred_skills_raw": "C++, Java, Cloud Computing, Linux, Machine Learning Basics",
            "job_type": "internship",
            "work_mode": "hybrid",
            "experience_level": "entry",
            "location": "Bangalore / Hyderabad / Redmond",
            "salary_min": 12,
            "salary_max": 18,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=120),
        },
        {
            "title": "Technical Program Manager II - Windows Core & Silicon Co-Engineering",
            "description": (
                "Bridge the gap between cutting-edge silicon hardware (ARM, NPU, GPU) and the Windows operating system. "
                "Lead cross-functional hardware/software co-engineering initiatives with partners like Qualcomm, Intel, and AMD."
            ),
            "responsibilities": (
                "• Drive engineering execution for hardware acceleration and Neural Processing Unit (NPU) integration in Windows.\n"
                "• Define rigorous hardware validation metrics for battery efficiency, thermal dissipation, and AI compute latency.\n"
                "• Coordinate cross-company milestones with silicon partners, device OEMs, and Windows OS kernel teams.\n"
                "• Manage risk registers, resolve technical blockers, and communicate project status to executive leaders."
            ),
            "requirements": (
                "• 4+ years of technical program management or engineering experience in software/hardware integration.\n"
                "• Strong understanding of computer architecture, silicon development lifecycles, and OS fundamentals.\n"
                "• Proven ability to influence cross-functional teams without direct authority.\n"
                "• Excellent communication skills translating complex silicon specifications into actionable software roadmaps."
            ),
            "required_skills_raw": "Program Management, Silicon Architecture, ARM, Cross-functional Leadership, Windows OS, JIRA",
            "preferred_skills_raw": "Firmware, Hardware Validation, Power & Battery Optimization, NPU",
            "job_type": "full_time",
            "work_mode": "onsite",
            "experience_level": "mid",
            "location": "Redmond, WA",
            "salary_min": 35,
            "salary_max": 58,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=90),
        },
        {
            "title": "Senior QA & Test Automation Architect - Azure Mission Critical",
            "description": (
                "Azure Mission Critical services require zero-downtime tolerance. We are seeking a Test Automation Architect to design "
                "end-to-end automated testing harnesses, chaos injection suites, and performance benchmark frameworks."
            ),
            "responsibilities": (
                "• Architect and implement resilient automated testing frameworks using Playwright, Python, and C#.\n"
                "• Build high-volume load, stress, and endurance test suites validating multi-tenant API scalability.\n"
                "• Integrate automated quality gates directly into Azure DevOps and GitHub Actions CI/CD pipelines.\n"
                "• Champion test-driven development (TDD) and establish reliability metrics across the engineering organization."
            ),
            "requirements": (
                "• 5+ years of experience in test automation, quality engineering, or reliability architecture.\n"
                "• Deep hands-on proficiency in Playwright, Selenium, Pytest, or JUnit.\n"
                "• Strong coding skills in Python, C#, or Java with experience testing distributed REST and gRPC microservices.\n"
                "• Solid understanding of CI/CD pipelines, containerized test runners, and performance profiling."
            ),
            "required_skills_raw": "Test Automation, Playwright, Python, C#, CI/CD, Performance Testing, JMeter",
            "preferred_skills_raw": "Chaos Mesh, Load Testing, API Contract Testing, Azure DevOps, Docker",
            "job_type": "full_time",
            "work_mode": "remote",
            "experience_level": "senior",
            "location": "Remote - India / Redmond, WA",
            "salary_min": 30,
            "salary_max": 52,
            "status": Job.STATUS_ACTIVE,
            "application_deadline": utcnow() + timedelta(days=90),
        }
    ]

    created_jobs = []
    for jd in jobs_data:
        existing_job = Job.query.filter_by(
            recruiter_profile_id=profile.id,
            title=jd["title"]
        ).first()

        if not existing_job:
            job = Job(
                recruiter_profile_id=profile.id,
                title=jd["title"],
                description=jd["description"],
                responsibilities=jd["responsibilities"],
                requirements=jd["requirements"],
                required_skills_raw=jd["required_skills_raw"],
                preferred_skills_raw=jd["preferred_skills_raw"],
                job_type=jd["job_type"],
                work_mode=jd["work_mode"],
                experience_level=jd["experience_level"],
                location=jd["location"],
                salary_min=jd["salary_min"],
                salary_max=jd["salary_max"],
                status=jd["status"],
                created_at=utcnow() - timedelta(days=15),
                application_deadline=jd["application_deadline"],
            )
            db.session.add(job)
            db.session.flush()
            created_jobs.append(job)
            print(f"  [+] Job: {job.title}")
        else:
            existing_job.description = jd["description"]
            existing_job.responsibilities = jd["responsibilities"]
            existing_job.requirements = jd["requirements"]
            existing_job.required_skills_raw = jd["required_skills_raw"]
            existing_job.preferred_skills_raw = jd["preferred_skills_raw"]
            existing_job.salary_min = jd["salary_min"]
            existing_job.salary_max = jd["salary_max"]
            existing_job.location = jd["location"]
            existing_job.status = jd["status"]
            existing_job.application_deadline = jd["application_deadline"]
            db.session.flush()
            created_jobs.append(existing_job)
            print(f"  [*] Updated Job: {existing_job.title}")

    db.session.commit()
    print(f"[+] Total {len(created_jobs)} Microsoft jobs ready.")

    # -------------------------------------------------------------
    # 3. REALISTIC CANDIDATE ACCOUNT (Alex Chen / Aarav Sharma)
    # -------------------------------------------------------------
    candidate_email = "alex.chen@example.com"
    candidate_pass = "Candidate@123"

    cand_user = User.query.filter_by(email=candidate_email).first()
    if not cand_user:
        cand_user = User(
            full_name="Alex Chen",
            email=candidate_email,
            role=User.ROLE_CANDIDATE,
            headline="Senior Cloud & Distributed Systems Engineer | Ex-Amazon | Azure & Kubernetes",
            phone="+91 98450 12345",
            location="Bangalore, India",
            skills="Azure, Python, Kubernetes, Terraform, Distributed Systems, Go, Docker, REST APIs, Microservices, CI/CD",
            bio=(
                "Senior Software Engineer with 6+ years of experience designing and scaling resilient cloud infrastructure, "
                "distributed microservices, and high-throughput backend systems. Track record of improving API latency by 45% "
                "and architecting multi-region Kubernetes deployments serving 5M+ daily requests."
            ),
            github_url="https://github.com/alexchen-cloud",
            linkedin_url="https://linkedin.com/in/alexchen-systems",
            portfolio_url="https://alexchen.dev",
            preferred_job_role="Senior Cloud Architect / Distributed Systems Engineer",
            preferred_location="Bangalore / Remote",
            work_preference="hybrid",
            expected_salary="₹48,00,000 / $160,000",
            experience_level="senior",
            public_slug="alex-chen-cloud",
            public_profile_enabled=True,
            recruiter_discoverable=True,
            public_resume_enabled=True,
            is_active_account=True,
        )
        cand_user.set_password(candidate_pass)
        db.session.add(cand_user)
        db.session.flush()
        print(f"[+] Created Candidate User: {candidate_email}")
    else:
        cand_user.full_name = "Alex Chen"
        cand_user.headline = "Senior Cloud & Distributed Systems Engineer | Ex-Amazon | Azure & Kubernetes"
        cand_user.phone = "+91 98450 12345"
        cand_user.location = "Bangalore, India"
        cand_user.skills = "Azure, Python, Kubernetes, Terraform, Distributed Systems, Go, Docker, REST APIs, Microservices, CI/CD"
        cand_user.bio = (
            "Senior Software Engineer with 6+ years of experience designing and scaling resilient cloud infrastructure, "
            "distributed microservices, and high-throughput backend systems. Track record of improving API latency by 45% "
            "and architecting multi-region Kubernetes deployments serving 5M+ daily requests."
        )
        cand_user.github_url = "https://github.com/alexchen-cloud"
        cand_user.linkedin_url = "https://linkedin.com/in/alexchen-systems"
        cand_user.portfolio_url = "https://alexchen.dev"
        cand_user.preferred_job_role = "Senior Cloud Architect / Distributed Systems Engineer"
        cand_user.preferred_location = "Bangalore / Remote"
        cand_user.work_preference = "hybrid"
        cand_user.expected_salary = "₹48,00,000 / $160,000"
        cand_user.experience_level = "senior"
        cand_user.public_slug = "alex-chen-cloud"
        cand_user.public_profile_enabled = True
        cand_user.recruiter_discoverable = True
        cand_user.public_resume_enabled = True
        cand_user.set_password(candidate_pass)
        db.session.flush()
        print(f"[*] Updated Candidate User: {candidate_email}")

    # Career Entries (Experience, Education, Certifications, Projects)
    CareerEntry.query.filter_by(candidate_id=cand_user.id).delete()

    entries = [
        CareerEntry(
            candidate_id=cand_user.id,
            entry_type=CareerEntry.TYPE_EXPERIENCE,
            title="Senior Software Engineer (Cloud Infrastructure)",
            organization="Amazon Web Services (AWS)",
            location="Bangalore, India",
            start_date="2022-03",
            end_date="Present",
            description=(
                "• Architected and operated multi-tenant container orchestration platform powering 50k+ daily cluster deployments.\n"
                "• Reduced cross-region latency by 35% through custom Envoy proxy configurations and eBPF network tracing.\n"
                "• Led a squad of 6 engineers shipping automated infrastructure provisioning pipelines with Terraform.\n"
                "• Authored comprehensive SRE runbooks and conducted quarterly chaos engineering game days."
            )
        ),
        CareerEntry(
            candidate_id=cand_user.id,
            entry_type=CareerEntry.TYPE_EXPERIENCE,
            title="Software Engineer II (Backend & Platforms)",
            organization="Flipkart",
            location="Bangalore, India",
            start_date="2019-07",
            end_date="2022-02",
            description=(
                "• Designed high-throughput order processing microservices handling 120,000 requests/sec during Big Billion Days.\n"
                "• Optimized PostgreSQL and Redis caching layers, cutting P99 latency from 180ms to 24ms.\n"
                "• Migrated legacy monolithic services into containerized microservices running on Kubernetes."
            )
        ),
        CareerEntry(
            candidate_id=cand_user.id,
            entry_type=CareerEntry.TYPE_EDUCATION,
            title="B.Tech in Computer Science and Engineering",
            organization="Indian Institute of Technology (IIT) Madras",
            location="Chennai, India",
            start_date="2015",
            end_date="2019",
            description="Graduated with First Class Honours (CGPA: 9.1/10.0). Head of Systems & Networking Club."
        ),
        CareerEntry(
            candidate_id=cand_user.id,
            entry_type=CareerEntry.TYPE_CERTIFICATION,
            title="Microsoft Certified: Azure Solutions Architect Expert",
            organization="Microsoft",
            start_date="2024",
            credential_url="https://learn.microsoft.com/credentials/certifications/azure-solutions-architect/"
        ),
        CareerEntry(
            candidate_id=cand_user.id,
            entry_type=CareerEntry.TYPE_CERTIFICATION,
            title="Certified Kubernetes Administrator (CKA)",
            organization="Cloud Native Computing Foundation (CNCF)",
            start_date="2023",
            credential_url="https://www.cncf.io/certification/cka/"
        ),
        CareerEntry(
            candidate_id=cand_user.id,
            entry_type=CareerEntry.TYPE_PROJECT,
            title="KubeScale - Intelligent Kubernetes Horizontal Pod Autoscaler",
            organization="Open Source",
            description=(
                "Built an open-source predictive autoscaling operator for Kubernetes utilizing machine learning metrics. "
                "Over 1,200 GitHub stars and adopted by 15+ production clusters."
            ),
            credential_url="https://github.com/alexchen-cloud/kubescale"
        ),
    ]
    for entry in entries:
        db.session.add(entry)

    # Candidate Resume
    resume_raw_text = """ALEX CHEN
Bangalore, India | +91 98450 12345 | alex.chen@example.com
LinkedIn: linkedin.com/in/alexchen-systems | GitHub: github.com/alexchen-cloud | Portfolio: alexchen.dev

PROFESSIONAL SUMMARY
Senior Cloud & Distributed Systems Engineer with 6+ years of experience architecting hyperscale cloud solutions, distributed backend microservices, and Kubernetes infrastructure. Proven expertise in Microsoft Azure, Terraform, Python, Go, and Site Reliability Engineering. Certified Azure Solutions Architect Expert and CKA.

CORE TECHNICAL SKILLS
• Cloud & Infrastructure: Microsoft Azure (AKS, Cosmos DB, Blob Storage, VNets, Azure DevOps), AWS, Terraform, Docker, Kubernetes, Helm
• Programming Languages: Python, Go, C#, SQL, TypeScript, Bash
• Distributed Systems: Microservices, gRPC, REST APIs, Kafka, Redis, PostgreSQL, Distributed Caching
• Observability & DevOps: Prometheus, Grafana, OpenTelemetry, CI/CD pipelines, GitOps (ArgoCD), Linux Internals

PROFESSIONAL EXPERIENCE
Senior Software Engineer | Amazon Web Services (AWS), Bangalore
March 2022 – Present
• Architected and maintained distributed control plane services running on Kubernetes managing 50,000+ daily cluster workloads.
• Optimized networking stack and service mesh routing, reducing average microservice latency by 35%.
• Led Infrastructure-as-Code (IaC) migration using Terraform, standardizing automated provisioning across 12 engineering squads.
• Mentored 5 junior and mid-level engineers, running code reviews and architectural design syncs.

Software Engineer II | Flipkart, Bangalore
July 2019 – February 2022
• Developed high-volume checkout microservices processing peak volumes of 120,000 RPS during flash sales with 99.995% uptime.
• Redesigned database indexing and query caching strategies on PostgreSQL and Redis, improving database P99 latency by 86%.
• Integrated automated contract testing and canary release pipelines, decreasing deployment rollbacks by 60%.

EDUCATION
Indian Institute of Technology (IIT) Madras | Chennai, India
Bachelor of Technology in Computer Science & Engineering (2015 – 2019)
CGPA: 9.1 / 10.0 | Institute Merit Scholarship recipient

CERTIFICATIONS
• Microsoft Certified: Azure Solutions Architect Expert (2024)
• Certified Kubernetes Administrator - CKA, Linux Foundation (2023)
• AWS Certified Solutions Architect – Associate (2022)

OPEN SOURCE & NOTABLE PROJECTS
• KubeScale: An open-source predictive pod autoscaling controller written in Go. (1,200+ GitHub Stars).
• Azure-IaC-Blueprint: Production-ready Terraform templates for highly available multi-region Azure topologies.
"""

    # -------------------------------------------------------------
    # 4. APPLICATIONS TO MICROSOFT JOBS (Clean up existing apps/events first)
    # -------------------------------------------------------------
    existing_apps = Application.query.filter_by(candidate_id=cand_user.id).all()
    for ea in existing_apps:
        ApplicationEvent.query.filter_by(application_id=ea.id).delete()
        db.session.delete(ea)
    db.session.flush()

    # Safely clean up previous resumes now that no application references them
    Resume.query.filter_by(candidate_id=cand_user.id).delete()
    db.session.flush()

    resume = Resume(
        candidate_id=cand_user.id,
        source="paste",
        name="Alex_Chen_Principal_Cloud_Resume_2026.pdf",
        target_role="Principal Cloud Solution Architect",
        raw_text=resume_raw_text,
        last_ats_score=94.5,
        last_matched_keywords="Azure, Cloud Architecture, Kubernetes, Terraform, Distributed Systems, Python, Linux, Microservices, CI/CD",
        last_missing_keywords="BGP, Zero Trust",
        is_primary=True,
        created_at=utcnow() - timedelta(days=20),
        updated_at=utcnow() - timedelta(days=2),
    )
    db.session.add(resume)
    db.session.flush()
    print(f"[+] Created Primary Resume for Candidate (Score: 94.5%)")

    # Apply to 1st Microsoft Job: Principal Cloud Solution Architect
    job1 = created_jobs[0]
    score1 = score_resume_for_job(resume_raw_text, job1)["score"]
    resume.last_ats_score = float(score1)

    app1 = Application(
        job_id=job1.id,
        candidate_id=cand_user.id,
        resume_id=resume.id,
        match_score=float(score1),
        cover_note=(
            "Dear Microsoft Hiring Team,\n\n"
            "I am thrilled to apply for the Principal Cloud Solution Architect role. Having spent 6+ years scaling distributed "
            "Kubernetes architectures, optimizing multi-region network routing, and holding the Azure Solutions Architect Expert "
            "certification, I am deeply passionate about driving architectural excellence for Azure Core customers.\n\n"
            "I look forward to discussing how my experience with high-throughput cloud infrastructure aligns with Microsoft's mission."
        ),
        status="applied",
        applied_at=utcnow() - timedelta(days=10),
    )
    db.session.add(app1)

    # Apply to 2nd Microsoft Job: SRE Lead - Azure Global Network
    job2 = created_jobs[3]
    score2 = score_resume_for_job(resume_raw_text, job2)["score"]
    app2 = Application(
        job_id=job2.id,
        candidate_id=cand_user.id,
        resume_id=resume.id,
        match_score=float(score2),
        cover_note=(
            "Hello,\n\n"
            "I am applying for the Site Reliability Engineering Lead position. My experience building automated self-healing "
            "systems in Go and orchestrating mission-critical Kubernetes clusters at AWS directly matches the scale and reliability "
            "goals of Azure's Global Network.\n\n"
            "Best regards,\nAlex Chen"
        ),
        status="applied",
        applied_at=utcnow() - timedelta(days=7),
    )
    db.session.add(app2)

    db.session.commit()
    print(f"[+] Submitted 2 realistic applications to Microsoft jobs (Live ATS scores: {score1}%, {score2}%, Status: 'applied')")

    print("\n" + "=" * 65)
    print("  SEEDING SUCCESSFUL! LOGIN CREDENTIALS BELOW:")
    print("=" * 65)
    print("  [1] MICROSOFT RECRUITER ACCOUNT:")
    print(f"      Email / ID : {recruiter_email}")
    print(f"      Password   : {recruiter_pass}")
    print(f"      Company    : Microsoft Corporation (Approved)")
    print(f"      Jobs Posted: {len(created_jobs)} Active Jobs")
    print()
    print("  [2] CANDIDATE ACCOUNT:")
    print(f"      Email / ID : {candidate_email}")
    print(f"      Password   : {candidate_pass}")
    print(f"      Name       : Alex Chen")
    print(f"      Headline   : Senior Cloud & Distributed Systems Engineer")
    print(f"      ATS Score  : 94.5% (Primary Resume Active)")
    print(f"      Status     : Applied & in 'interview' stage with Microsoft")
    print()
    print("  [3] ADMIN ACCOUNT (Platform Oversight):")
    print(f"      Email / ID : admin@zentra.example.com")
    print(f"      Password   : Admin@123")
    print("=" * 65)

if __name__ == "__main__":
    seed_microsoft()
