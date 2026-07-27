import sys
import types
from unittest import TestCase

import pandas as pd

if "streamlit" not in sys.modules:
    streamlit_stub = types.ModuleType("streamlit")

    def cache_resource(*_args, **_kwargs):
        def decorate(function):
            return function

        return decorate

    streamlit_stub.cache_resource = cache_resource
    sys.modules["streamlit"] = streamlit_stub

from jobhub_progress_tracker import _sync_external_from_estimate


class ProgressSyncPerformanceTests(TestCase):
    def test_unchanged_external_lines_do_not_write(self):
        writes = []
        batches = []

        def query(sql, _params=()):
            if "FROM estimate_line_items" in sql:
                return pd.DataFrame(
                    [
                        {
                            "id": 9,
                            "section": "External",
                            "description": "North wall",
                            "qty": 120.0,
                            "unit": "m2",
                            "substrate": "Fibre cement",
                            "work_location": "External",
                        }
                    ]
                )
            return pd.DataFrame(
                [
                    {
                        "estimate_line_id": 9,
                        "area_name": "North wall",
                        "substrate": "Fibre cement",
                        "measured_m2": 120.0,
                    }
                ]
            )

        context = {
            "df_query": query,
            "execute": lambda sql, params=(): writes.append((sql, params)),
            "execute_many": lambda sql, rows: batches.append((sql, list(rows))),
        }
        added = _sync_external_from_estimate(context, 1, 2, "automatic")
        self.assertEqual(added, 0)
        self.assertEqual(writes, [])
        self.assertEqual(batches, [])

    def test_changed_and_new_lines_are_batched(self):
        batches = []

        def query(sql, _params=()):
            if "FROM estimate_line_items" in sql:
                return pd.DataFrame(
                    [
                        {
                            "id": 9,
                            "section": "External",
                            "description": "North wall",
                            "qty": 125.0,
                            "unit": "m2",
                            "substrate": "Fibre cement",
                            "work_location": "External",
                        },
                        {
                            "id": 10,
                            "section": "External",
                            "description": "South soffit",
                            "qty": 30.0,
                            "unit": "m2",
                            "substrate": "FCS",
                            "work_location": "External",
                        },
                    ]
                )
            return pd.DataFrame(
                [
                    {
                        "estimate_line_id": 9,
                        "area_name": "North wall",
                        "substrate": "Fibre cement",
                        "measured_m2": 120.0,
                    }
                ]
            )

        context = {
            "df_query": query,
            "execute": lambda *_args, **_kwargs: self.fail("batch helper should be used"),
            "execute_many": lambda sql, rows: batches.append((sql, list(rows))),
        }
        added = _sync_external_from_estimate(context, 1, 2, "automatic")
        self.assertEqual(added, 1)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0][1]), 1)
        self.assertEqual(len(batches[1][1]), 1)
