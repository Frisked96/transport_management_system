"""
Custom test runner for Transport Management System (TMS).
Defaults to parallel execution across all CPU cores and suppresses repetitive
database cloning/destroying logs to keep terminal output clean and fast.
"""
from django.db import connections
from django.test.runner import DiscoverRunner, get_max_test_processes


class TMSTestRunner(DiscoverRunner):
    """
    Optimized test runner that:
    1. Defaults to --parallel auto (utilizing all available CPU cores).
    2. Gracefully falls back to parallel=1 if --pdb is specified.
    3. Suppresses repetitive 'Cloning test database...' and 'Destroying test database...'
       lines produced by parallel worker databases.
    """

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        # Make --parallel auto the default unless explicitly overridden
        for action in parser._actions:
            if action.dest == "parallel":
                action.default = "auto"
                break

    def __init__(self, *args, **kwargs):
        # Resolve 'auto' or default 0 to CPU process count
        parallel = kwargs.get("parallel")
        pdb = kwargs.get("pdb", False)

        if pdb:
            kwargs["parallel"] = 1
        elif parallel in (0, None, "auto"):
            kwargs["parallel"] = get_max_test_processes()

        super().__init__(*args, **kwargs)

    def _suppress_worker_db_logs(self):
        for conn in connections.all():
            orig_log = getattr(conn.creation, "_orig_log", conn.creation.log)
            conn.creation._orig_log = orig_log

            def quiet_log(msg, *args, _orig=orig_log, **kwargs):
                if msg.startswith("Cloning test database") or msg.startswith("Destroying test database"):
                    return
                _orig(msg, *args, **kwargs)

            conn.creation.log = quiet_log

    def setup_databases(self, **kwargs):
        self._suppress_worker_db_logs()
        return super().setup_databases(**kwargs)

    def teardown_databases(self, old_config, **kwargs):
        self._suppress_worker_db_logs()
        return super().teardown_databases(old_config, **kwargs)
