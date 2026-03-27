---
name: django-project-bootstrap
description: bootstrap a new Django + PostgreSQL project for an operational admin system using Django templates and HTMX. use when starting from a blank repository or setting up the first project structure, settings, apps, auth, templates, and foundations.
---

Set up the project so feature work can proceed cleanly.

Always:
1. Define a simple Django monolith structure with clear domain apps.
2. Configure PostgreSQL and environment-based settings early.
3. Establish auth, user ownership, permissions, and admin foundations before complex workflows.
4. Prepare base templates, layout, shared components, and HTMX-ready partial structure.
5. Keep the bootstrap minimal but production-minded: settings, database, static files, templates, tests, and core conventions.
6. End with the next vertical slice to implement first.

Suggested app areas:
- users
- cash boxes
- shifts
- movements
- closings
- reports

Output format:
- Goal
- Project structure
- Core apps
- Foundational configuration
- First migrations or setup steps
- Recommended next slice
