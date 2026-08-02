import unittest

from jobhub.job_pack_matching import match_job_pack_to_jobs, normalise_address


JOBS = [
    {
        "id": 15,
        "job_no": "PB0015",
        "job_name": "Yinni St",
        "builder_name": "One Life Property Group",
        "site_address": "Yinni street, Maroochydoore",
        "contract_value": 107000,
    },
    {
        "id": 26,
        "job_no": "PB26022",
        "job_name": "Villa 108 Palm Lakes",
        "builder_name": "Palm Lakes Works",
        "site_address": "Pelican Waters",
        "contract_value": 0,
    },
    {
        "id": 27,
        "job_no": "PB26023",
        "job_name": "Villa 128 Palm Lakes",
        "builder_name": "Palm Lakes Works",
        "site_address": "Pelican Waters",
        "contract_value": 0,
    },
]


class JobPackMatchingTests(unittest.TestCase):
    def test_exact_job_number_is_decisive(self):
        result = match_job_pack_to_jobs(
            {"job_no": "PB-0015", "job_name": "Different text"}, JOBS
        )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["match"]["job_id"], 15)
        self.assertEqual(result["match"]["score"], 100)

    def test_address_abbreviation_typo_and_builder_suffix_match(self):
        result = match_job_pack_to_jobs(
            {
                "job_no": "J1050",
                "job_name": "18 Yinni Street",
                "site_address": "Yinni St, Maroochydore QLD 4558",
                "builder_client": "One Life Property Group Pty Ltd",
                "contract_value_ex_gst": 107084.80,
            },
            JOBS,
        )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["match"]["job_id"], 15)

    def test_shared_broad_address_is_ambiguous(self):
        result = match_job_pack_to_jobs(
            {
                "job_no": "",
                "job_name": "",
                "site_address": "Pelican Waters, QLD",
                "builder_client": "Palm Lakes Works Pty Ltd",
            },
            JOBS,
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["match"])

    def test_unrelated_job_does_not_auto_match(self):
        result = match_job_pack_to_jobs(
            {
                "job_no": "PB99999",
                "job_name": "Unknown Project",
                "site_address": "1 Somewhere Road, Brisbane",
                "builder_client": "Unknown Builder",
            },
            JOBS,
        )
        self.assertEqual(result["status"], "no_match")

    def test_address_normalisation_keeps_house_number(self):
        self.assertEqual(
            normalise_address("18 Puma Street, Tingalpa QLD 4173"),
            "18 puma st tingalpa",
        )


if __name__ == "__main__":
    unittest.main()

