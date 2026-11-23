from unittest import TestCase

from worker.leases import work_deadline


class LeaseTests(TestCase):
    def test_default_lease_leaves_ten_seconds_to_save(self):
        self.assertEqual(work_deadline(30, name="X"), 20)
        self.assertEqual(work_deadline(15, name="X"), 5)

    def test_short_leases_are_rejected(self):
        for lease in (14, 10, 5, 1, 0):
            with self.assertRaises(ValueError, msg=lease) as caught:
                work_deadline(lease, name="WORKER_LEASE_SECONDS")
            self.assertIn("WORKER_LEASE_SECONDS", str(caught.exception))
