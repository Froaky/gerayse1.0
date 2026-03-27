---
name: django-feature-slice
description: implement a vertical feature slice in a Django monolith using models, forms, views, templates, urls, and tests, with HTMX when useful. use when building or changing a user-facing workflow end to end.
---

Implement one complete Django feature slice at a time.

Always:
1. Restate the user workflow and identify the affected roles.
2. Map the change across models, forms, services, views, templates, URLs, and tests.
3. Prefer Django forms and server validation over ad hoc front-end logic.
4. Use HTMX for incremental UI interactions when it reduces friction without adding front-end complexity.
5. Keep view code thin and move reusable business logic into explicit services or domain methods when complexity grows.
6. Finish the slice with validation, tests, and a clear summary of what is now operational.

Output format:
- Goal
- Workflow
- Files or layers involved
- Planned changes
- Tests to add
- Validation checklist
