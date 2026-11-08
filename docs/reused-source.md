# Reused society source

Source: `travello0r/klados` at `e49ca76c5d4d975971f02eb848ecb7bb23f3614c`.
Selected society application, configuration, SQL migrations, synthetic fixtures and
regression tests are reused without Git history. No runtime data or private agent
memory is included. Original namespaces and data formats are retained.

The society rules, personalities, prompts, observer, scheduler, money ledger and
JSON store are unchanged. `arena/worker.py` adds an explicit mock-provider mode;
`arena/society_public.py` locks the author before counting the human reward quota.
`arena/app.py`, `arena/runtime.py`, `arena/mock_provider.py` and `arena/demo.py` are
new local integration code. The web entry point excludes arena/voting routes.
All old SQL migrations remain for schema/auth compatibility; unused arena tables
are not exposed as product features. PR #64's `/etc/klados-control/KILLED` remains
the gateway default; there are no TEE mounts or controller dependencies.

Foundation lint/type checks retain their strict scope for new typed modules.
Imported untyped modules/tests are excluded from style/type rewrites; behavioral
regression tests run unchanged. Broad namespace/refactor work is deferred.
