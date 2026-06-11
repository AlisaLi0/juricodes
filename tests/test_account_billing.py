import importlib
import os
import tempfile
import unittest


class AccountBillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LEAGLE_DB_PATH"] = os.path.join(self.tmp.name, "test.db")
        os.environ["FREEMIUS_PRODUCT_ID"] = "31701"
        os.environ["FREEMIUS_SECRET_KEY"] = "test-secret"
        os.environ["FREEMIUS_PLANS"] = "pro:52037:68208,max:52038:68209,day_pass:52039:68210"
        os.environ["FREEMIUS_SANDBOX"] = "false"
        import server.db as db
        import server.billing as billing
        self.db = importlib.reload(db)
        self.billing = importlib.reload(billing)
        self.db.init_db()

    def test_public_billing_config_exposes_live_pricing_ids(self):
        cfg = self.billing.public_config()
        self.assertEqual(cfg["plans"]["pro"], "52037")
        self.assertEqual(cfg["pricing"]["pro"], "68208")
        self.assertEqual(cfg["pricing"]["max"], "68209")
        self.assertEqual(cfg["pricing"]["day_pass"], "68210")
        self.assertFalse(cfg["sandbox"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_verified_email_links_oauth_identities(self):
        google = self.db.upsert_user("google", "g-1", email="user@example.com", name="Google User")
        x = self.db.upsert_user("x", "x-1", email="USER@example.com", name="X User")
        self.assertEqual(google.id, x.id)
        self.assertEqual(self.db.get_user_by_email("user@example.com").id, google.id)

    def test_empty_x_email_does_not_synthesize_or_merge(self):
        google = self.db.upsert_user("google", "g-1", email="user@example.com", name="Google User")
        x = self.db.upsert_user("x", "x-1", email="", name="X User")
        self.assertNotEqual(google.id, x.id)
        self.assertEqual(x.email, "")

    def test_existing_identity_later_email_merges_into_account(self):
        x = self.db.upsert_user("x", "x-1", email="", name="X User")
        google = self.db.upsert_user("google", "g-1", email="user@example.com", name="Google User")
        x_again = self.db.upsert_user("x", "x-1", email="user@example.com", name="X User")
        self.assertEqual(x_again.id, google.id)
        self.assertNotEqual(x.id, x_again.id)

    def test_freemius_event_is_idempotent(self):
        user = self.db.upsert_user("google", "g-1", email="paid@example.com", name="Paid")
        evt = {
            "id": "evt-1",
            "type": "license.activated",
            "objects": {
                "user": {"email": "paid@example.com"},
                "license": {"id": "lic-1", "plan_id": "52038", "expiration": "2026-07-01 12:00:00"},
            },
        }
        first = self.billing.handle_event(evt)
        second = self.billing.handle_event(evt)
        self.assertEqual(first["action"], "activate")
        self.assertEqual(second["action"], "duplicate")
        self.assertEqual(self.db.get_user(user.id).plan, "max")
        import sqlite3
        conn = sqlite3.connect(os.environ["LEAGLE_DB_PATH"])
        try:
            period_end = conn.execute(
                "SELECT period_end FROM subscriptions WHERE provider_ref='lic-1'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertIsInstance(period_end, int)
        self.assertGreater(period_end, 0)

    def test_freemius_event_type_strips_whitespace(self):
        user = self.db.upsert_user("google", "g-1", email="paid2@example.com", name="Paid 2")
        evt = {
            "id": "evt-space",
            "type": " license.activated ",
            "objects": {
                "user": {"email": "paid2@example.com"},
                "license": {"id": "lic-space", "plan_id": "52037"},
            },
        }
        result = self.billing.handle_event(evt)
        self.assertEqual(result["action"], "activate")
        self.assertEqual(self.db.get_user(user.id).plan, "pro")


if __name__ == "__main__":
    unittest.main()
