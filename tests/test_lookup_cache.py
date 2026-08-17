import unittest

import jobhub_lookup_cache as lookup_cache


class _Clearable:
    def __init__(self, module="pb_jobhub_app", qualname="get_job_options"):
        self.__module__ = module
        self.__qualname__ = qualname
        self.clear_calls = 0

    def clear(self):
        self.clear_calls += 1


class LookupCacheRegistryTests(unittest.TestCase):
    def setUp(self):
        lookup_cache._tracked.clear()

    def tearDown(self):
        lookup_cache._tracked.clear()

    def test_rerun_registration_replaces_previous_wrapper(self):
        old_wrapper = _Clearable()
        live_wrapper = _Clearable()

        lookup_cache.track_cached(old_wrapper)
        returned = lookup_cache.track_cached(live_wrapper)

        self.assertIs(returned, live_wrapper)
        self.assertEqual(len(lookup_cache._tracked), 1)
        self.assertIs(lookup_cache._tracked[0], live_wrapper)

        lookup_cache.notify_db_write("UPDATE jobs SET status = ? WHERE id = ?")
        self.assertEqual(old_wrapper.clear_calls, 0)
        self.assertEqual(live_wrapper.clear_calls, 1)

    def test_distinct_cached_helpers_remain_registered(self):
        jobs = _Clearable(qualname="get_job_options")
        employees = _Clearable(qualname="get_employee_options")

        lookup_cache.track_cached(jobs)
        lookup_cache.track_cached(employees)

        self.assertEqual(len(lookup_cache._tracked), 2)

    def test_read_queries_do_not_invalidate_caches(self):
        wrapper = _Clearable()
        lookup_cache.track_cached(wrapper)

        lookup_cache.notify_db_write("  SELECT id FROM jobs")
        lookup_cache.notify_db_write("WITH active AS (SELECT 1) SELECT * FROM active")
        lookup_cache.notify_db_write("EXPLAIN SELECT id FROM jobs")

        self.assertEqual(wrapper.clear_calls, 0)

    def test_mutating_cte_invalidates_caches(self):
        wrapper = _Clearable()
        lookup_cache.track_cached(wrapper)

        lookup_cache.notify_db_write(
            "WITH changed AS (UPDATE jobs SET status='Active' RETURNING id) "
            "SELECT id FROM changed"
        )

        self.assertEqual(wrapper.clear_calls, 1)

    def test_explain_analyze_is_treated_as_potential_write(self):
        wrapper = _Clearable()
        lookup_cache.track_cached(wrapper)

        lookup_cache.notify_db_write(
            "EXPLAIN ANALYZE UPDATE jobs SET status='Active' WHERE id=1"
        )

        self.assertEqual(wrapper.clear_calls, 1)


if __name__ == "__main__":
    unittest.main()
