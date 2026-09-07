# Real three-tenant PostgreSQL run

This directory is an executable demo configuration, not a fixture source. Every
tenant polls public career sites through the normal `career_site` adapter and
persists jobs, groups, run state, and outcomes in PostgreSQL.

The bot image is used as the production runtime container. Its command is the
normal multi-tenant scheduler, so no Telegram credentials or publishing are
needed for this ingest-only run.
