from datetime import date, timedelta

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from hub.models import SqrSubmission
from hub.services import sqr_prototype as proto

TODAY = date(2026, 10, 21)


def make(**overrides):
    """Unsaved submission; enough for the pure derivation helpers."""
    defaults = {"status": "reviewed", "proposal_status": "", "customer_name": "Acme", "project_title": "Refresh"}
    defaults.update(overrides)
    return SqrSubmission(**defaults)


class StageDerivationTests(SimpleTestCase):
    def test_stage_follows_proposal_delivery_and_billing(self):
        cases = [
            ({}, ("opportunity", "")),
            ({"proposal_status": "negotiation_review"}, ("opportunity", "")),
            ({"proposal_status": "closed_lost"}, ("closed", "lost")),
            ({"proposal_status": "closed_canceled"}, ("closed", "canceled")),
            ({"proposal_status": "closed_won"}, ("delivery", "")),
            ({"proposal_status": "closed_won", "overall_status": "completed"}, ("billing", "")),
            ({"proposal_status": "closed_won", "delivery_health": "completed"}, ("billing", "")),
            ({"proposal_status": "closed_won", "delivery_actual_finish_date": TODAY}, ("billing", "")),
            ({"proposal_status": "closed_won", "overall_status": "completed", "revenue_status": "billed"}, ("closed", "billed")),
            ({"proposal_status": "closed_won", "revenue_date": TODAY}, ("closed", "billed")),
            ({"proposal_status": "closed_won", "overall_status": "cancelled"}, ("closed", "canceled")),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(proto.derive_stage(make(**fields)), expected)

    def test_explicit_not_yet_billed_overrides_a_billed_date(self):
        submission = make(proposal_status="closed_won", overall_status="completed", revenue_date=TODAY,
                          revenue_status="not_yet_billed")
        self.assertEqual(proto.derive_stage(submission), ("billing", ""))


class AttentionTests(SimpleTestCase):
    def flags(self, stage, **fields):
        return {flag["key"]: flag for flag in proto.derive_attention(make(**fields), stage, TODAY)}

    def test_status_flags(self):
        self.assertIn("revision", self.flags("opportunity", status="for_revision"))
        self.assertIn("awaiting", self.flags("opportunity", status="submitted"))
        self.assertNotIn("awaiting", self.flags("opportunity", status="reviewed"))

    def test_validity_window(self):
        expired = self.flags("opportunity", validity_due_date=TODAY - timedelta(days=1))["validity"]
        self.assertEqual((expired["label"], expired["tone"]), ("Validity expired", "danger"))
        today = self.flags("opportunity", validity_due_date=TODAY)["validity"]
        self.assertEqual(today["label"], "Validity ends today")
        soon = self.flags("opportunity", validity_due_date=TODAY + timedelta(days=1))["validity"]
        self.assertEqual(soon["label"], "Validity ends in 1 day")
        self.assertNotIn("validity", self.flags("opportunity", validity_due_date=TODAY + timedelta(days=8)))
        self.assertNotIn("validity", self.flags("delivery", validity_due_date=TODAY - timedelta(days=30)))

    def test_delivery_and_billing_flags(self):
        flags = self.flags("delivery", delivery_target_finish_date=TODAY - timedelta(days=2), delivery_health="at_risk")
        self.assertEqual(flags["overdue"]["tone"], "danger")
        self.assertEqual(flags["health"]["label"], "At risk")
        self.assertEqual(self.flags("delivery", delivery_health="off_track")["health"]["label"], "Off track")
        self.assertIn("billing", self.flags("billing"))
        self.assertNotIn("overdue", self.flags("billing", delivery_target_finish_date=TODAY - timedelta(days=2)))


class NextStepTests(SimpleTestCase):
    def step(self, stage="opportunity", reason="", is_pm=True, **fields):
        return proto.derive_next_step(make(**fields), stage, reason, is_pm=is_pm)

    def test_review_is_offered_to_pm_only(self):
        self.assertEqual(self.step(status="submitted")["action"], "review")
        self.assertEqual(self.step(status="submitted", is_pm=False)["action"], "")
        self.assertEqual(self.step(status="for_revision", is_pm=False)["action"], "edit")

    def test_stage_guidance(self):
        self.assertEqual(self.step()["title"], "Follow up with the customer")
        self.assertEqual(self.step(proposal_status="negotiation_review")["title"], "Negotiation in progress")
        self.assertEqual(self.step("delivery")["title"], "Plan the delivery")
        planned = self.step("delivery", delivery_start_date=TODAY, delivery_target_finish_date=TODAY, delivery_progress=40)
        self.assertEqual(planned["title"], "Delivery in progress")
        self.assertIn("40%", planned["hint"])
        self.assertEqual(self.step("billing")["title"], "Ready for billing")
        self.assertEqual(self.step("closed", "billed")["title"], "Complete")
        self.assertEqual(self.step("closed", "lost")["title"], "Closed-Lost")


class SchemaTests(SimpleTestCase):
    def test_every_spec_is_well_formed_and_unique(self):
        keys = [spec["key"] for spec in proto.FIELD_SPECS]
        self.assertEqual(len(keys), len(set(keys)))
        tabs = {key for key, _label in proto.TABS}
        for spec in proto.FIELD_SPECS:
            with self.subTest(key=spec["key"]):
                self.assertIn(spec["tab"], tabs)
                self.assertTrue(spec["label"] and spec["section"])
                if spec["type"] == "select":
                    self.assertTrue(spec["choices"])

    def test_editable_keys_exist_in_the_inline_endpoint_vocabulary(self):
        from hub.views import SqrInlineFieldUpdateView as View

        server_side = View._ADMIN_ALLOWED | View._PM_ESG_ALLOWED
        self.assertLessEqual(proto.EDIT_FIELD_KEYS, server_side)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PrototypeEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="proto-admin", role=User.Roles.ADMIN)
        cls.pm = User.objects.create_user(username="proto-pm", role=User.Roles.PM_ESG, first_name="Pat", last_name="Manager")
        cls.engineer = User.objects.create_user(username="proto-eng", role=User.Roles.ENGINEER)
        cls.other = User.objects.create_user(username="proto-other", role=User.Roles.ENGINEER)
        cls.requestor = User.objects.create_user(username="proto-req", role=User.Roles.REQUESTOR)
        cls.mine = SqrSubmission.objects.create(
            engineer=cls.engineer, pm_esg_reviewer=cls.pm, customer_name="Mine Co", project_title="Mine",
            sse_manhrs=20, status="submitted",
        )
        cls.lost = SqrSubmission.objects.create(
            engineer=cls.other, pm_esg_reviewer=cls.pm, customer_name="Lost Co", project_title="Lost",
            proposal_status="closed_lost", status="reviewed",
        )
        cls.url = reverse("hub:sqr-prototype-data")

    def records(self, user):
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return {record["pk"]: record for record in response.json()["records"]}

    def test_url_and_cache_headers(self):
        self.assertEqual(self.url, "/sqr/prototype-data/")
        self.client.force_login(self.pm)
        response = self.client.get(self.url)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])

    def test_scope_matches_the_classic_tracker(self):
        self.assertEqual(set(self.records(self.pm)), {self.mine.pk, self.lost.pk})
        self.assertEqual(set(self.records(self.admin)), {self.mine.pk, self.lost.pk})
        self.assertEqual(set(self.records(self.engineer)), {self.mine.pk})

    def test_unauthorised_roles_and_anonymous_users_are_blocked(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.requestor)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("records", response.json())

    def test_pm_gets_options_and_schema_engineer_does_not_get_options(self):
        self.client.force_login(self.pm)
        data = self.client.get(self.url).json()
        self.assertTrue(data["viewer"]["is_pm"])
        self.assertIn("Pat Manager", [option["label"] for option in data["options"]["pm_users"]])
        self.assertEqual(len(data["schema"]["fields"]), len(proto.FIELD_SPECS))
        self.client.force_login(self.engineer)
        data = self.client.get(self.url).json()
        self.assertEqual(data["options"], {"pm_users": [], "sse_users": [], "rq_options": []})
        self.assertFalse(data["viewer"]["is_pm"])

    def test_editable_fields_follow_the_inline_endpoint_rules(self):
        pm_record = self.records(self.pm)[self.mine.pk]
        admin_record = self.records(self.admin)[self.mine.pk]
        engineer_record = self.records(self.engineer)[self.mine.pk]
        self.assertIn("status", pm_record["editable"])
        self.assertNotIn("customer_name", pm_record["editable"])
        self.assertIn("customer_name", admin_record["editable"])
        self.assertEqual(engineer_record["editable"], [])
        self.assertTrue(engineer_record["perms"]["edit_form"])
        self.assertEqual(engineer_record["urls"]["edit"], reverse("hub:sqr-edit", args=[self.mine.pk]))
        self.assertEqual(engineer_record["urls"]["history"], "")
        self.assertEqual(engineer_record["urls"]["delete"], "")
        self.assertNotIn("engineer_email", engineer_record)

    def test_closed_lost_locks_delivery_and_billing_fields(self):
        pm_record = self.records(self.pm)[self.lost.pk]
        self.assertTrue(pm_record["locked"])
        self.assertEqual(pm_record["stage"], "closed")
        for key in ("delivery_start_date", "revenue_status", "assigned_pm", "key_updates_risks"):
            self.assertNotIn(key, pm_record["editable"])
        for key in ("proposal_status", "discount_rate", "status"):
            self.assertIn(key, pm_record["editable"])

    def test_record_payload_contains_computed_values_and_guidance(self):
        record = self.records(self.pm)[self.mine.pk]
        values = record["values"]
        self.assertEqual(values["sse_amount"], "40000.00")
        self.assertEqual(values["pm_manhrs"], "8")
        self.assertEqual(values["total_price"], "64000.00")
        self.assertEqual(record["next_step"]["action"], "review")
        self.assertEqual(record["engineer_email"], self.engineer.email)
        self.assertEqual(set(values), {spec["key"] for spec in proto.FIELD_SPECS})

    def test_single_record_fetch(self):
        self.client.force_login(self.engineer)
        response = self.client.get(self.url, {"pk": self.mine.pk})
        self.assertEqual(response.json()["record"]["pk"], self.mine.pk)
        self.assertEqual(self.client.get(self.url, {"pk": self.lost.pk}).status_code, 404)
        self.assertEqual(self.client.get(self.url, {"pk": "abc"}).status_code, 400)

    def test_query_count_does_not_grow_with_records(self):
        self.client.force_login(self.pm)
        self.client.get(self.url)
        with self.assertNumQueries(self._baseline_queries()):
            self.client.get(self.url)
        for index in range(5):
            SqrSubmission.objects.create(
                engineer=self.engineer, pm_esg_reviewer=self.pm, assigned_pm=self.pm, assigned_sse=self.engineer,
                customer_name=f"Bulk {index}", project_title="Bulk",
            )
        with self.assertNumQueries(self._baseline_queries()):
            self.client.get(self.url)

    def _baseline_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as context:
            self.client.get(self.url)
        return len(context)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class PrototypeTabRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pm = User.objects.create_user(username="tab-pm", role=User.Roles.PM_ESG)
        cls.engineer = User.objects.create_user(username="tab-eng", role=User.Roles.ENGINEER)

    def test_every_sqr_role_gets_the_prototype_tab_and_panel(self):
        for user in (self.pm, self.engineer):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(reverse("hub:sqr"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-sqr-view-button="prototype"')
                self.assertContains(response, "SQR Tracker Prototype")
                self.assertContains(response, 'data-sqr-view-panel="prototype"')
                self.assertContains(response, reverse("hub:sqr-prototype-data"))
