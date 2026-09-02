# Third-party notices

## JobHuntBot

- Source: https://github.com/DanielPan12/JobHuntBot
- Reviewed commit: `f35c67e34ed95d7d170e88c254c7bcc68a533ede`
- Local copy: `vendor/JobHuntBot`
- License: MIT
- Copyright (c) 2026 Yvonne He (original ApplyPilot)
- Copyright (c) 2026 DanielPan12 (JobHuntBot adaptation)

The local development copy keeps the upstream MIT license at
`vendor/JobHuntBot/LICENSE`. The `vendor/` snapshot is intentionally excluded
from the deployable Git repository; the license and source remain available at
the upstream repository linked above.

This application independently reimplements the useful workflow concepts—job pool,
application evidence, blocker tracking, outcomes, and follow-up scheduling—using its
existing Python, Streamlit, and SQLite architecture. The upstream dashboard HTML,
Node server, CSV mutation code, templates, and wording were not copied into the app.

The local upstream snapshot is retained for reference. It should remain on empty or
non-sensitive sample data because the reviewed dashboard has no authentication or
CSRF protection and does not safely constrain externally supplied job URLs.
