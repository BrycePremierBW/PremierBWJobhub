import unittest

from jobhub.document_centre_versioning_guard import (
    document_family_key,
    normalise_document_family_name,
)


class DocumentCentreVersioningTests(unittest.TestCase):
    def test_revision_suffixes_share_document_family(self):
        self.assertEqual(
            normalise_document_family_name("A101 Floor Plan - Rev B.pdf"),
            normalise_document_family_name("A101 Floor Plan - Rev C.pdf"),
        )
        self.assertEqual(
            normalise_document_family_name("Colour Schedule_v2.xlsx"),
            normalise_document_family_name("Colour Schedule_v3.xlsx"),
        )

    def test_family_key_stays_same_across_revisions(self):
        first = document_family_key(
            category="Job Specific",
            document_type="Plans / Architectural Drawings",
            entity_type="job",
            entity_id=16,
            job_id=16,
            file_name="A101 Floor Plan Rev B.pdf",
        )
        second = document_family_key(
            category="Job Specific",
            document_type="Plans / Architectural Drawings",
            entity_type="job",
            entity_id=16,
            job_id=16,
            file_name="A101 Floor Plan Rev C.pdf",
        )
        self.assertEqual(first, second)

    def test_different_jobs_do_not_share_family(self):
        first = document_family_key(
            category="Job Specific",
            document_type="Colour Schedule",
            entity_type="job",
            entity_id=16,
            job_id=16,
            file_name="Colour Schedule Rev A.pdf",
        )
        second = document_family_key(
            category="Job Specific",
            document_type="Colour Schedule",
            entity_type="job",
            entity_id=17,
            job_id=17,
            file_name="Colour Schedule Rev A.pdf",
        )
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
