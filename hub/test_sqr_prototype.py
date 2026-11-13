from datetime import date, timedelta
import json
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from urllib.parse import parse_qs, urlencode, urlparse

from django.conf import settings
from django.http import QueryDict
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
            ({"delivery_health": "completed"}, ("billing", "")),
            ({"delivery_health": "cancelled"}, ("closed", "canceled")),
            ({"delivery_completion_signed_date": TODAY}, ("billing", "")),
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
        flags = self.flags("delivery", delivery_target_finish_date=TODAY - timedelta(days=2),
                           delivery_health="off_track", schedule_performance="at_risk",
                           budget_effort_performance="over_budget", quality_performance="met")
        self.assertEqual(flags["schedule_performance"]["tone"], "warning")
        self.assertEqual(flags["budget_effort_performance"]["tone"], "danger")
        self.assertNotIn("overdue", flags)
        self.assertNotIn("health", flags)
        self.assertNotIn("quality_performance", flags)
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
        planned = self.step("delivery", delivery_start_date=TODAY, delivery_progress=40)
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

    def _active(self, **values):
        defaults = {"engineer": self.engineer, "pm_esg_reviewer": self.pm,
                    "customer_name": "Filter fixture", "project_title": "Filter project", "status": "reviewed",
                    "proposal_status": "closed_won", "overall_status": "in_progress"}
        defaults.update(values)
        return SqrSubmission.objects.create(**defaults)

    def _multi_fixture(self):
        second = User.objects.create_user(username="filter-second-pm", role="pm_esg")
        third = User.objects.create_user(username="filter-third-pm", role="pm_esg")
        rows = SqrSubmission.objects.bulk_create([
            SqrSubmission(engineer=self.engineer, pm_esg_reviewer=self.pm,
                          assigned_pm=second if index % 3 == 0 else None,
                          year=2026, sequence_number=1000 + index, reference_code=f"FILTER-{index}",
                          customer_name="Filter fixture", project_title="Filter project", customer_company="ESS Delivery",
                          status="reviewed", proposal_status="closed_won", schedule_performance="at_risk",
                          overall_status="in_progress" if index % 2 == 0 else "on_hold")
            for index in range(55)
        ])
        self._active(engineer=self.other, assigned_pm=third, schedule_performance="at_risk")
        self._active(overall_status="planning", schedule_performance="at_risk")
        self._active(schedule_performance="on_track")
        params = QueryDict(f"overall_status=in_progress&overall_status=on_hold&pm={self.pm.pk}&pm={second.pk}&pm={self.pm.pk}&schedule_performance=at_risk&assigned=all", mutable=True)
        return {row.pk for row in rows}, params

    def test_two_statuses_and_two_effective_pms_use_or_within_and_across(self):
        from hub.services.sqr_queries import annotate_tracker, filter_tracker, multi_values
        from hub.views import _sqr_queryset_for_user

        expected, params = self._multi_fixture()
        self.assertEqual(multi_values(params, "overall_status", allowed=SqrSubmission.OverallStatus.values), ("in_progress", "on_hold"))
        scoped = annotate_tracker(_sqr_queryset_for_user(self.admin), TODAY)
        self.assertEqual(set(filter_tracker(scoped, params, self.admin, prototype=True).values_list("pk", flat=True)), expected)
        self.client.force_login(self.admin)
        params["page"] = "2"
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pagination"]["total"], 55)
        self.assertEqual(len(response.json()["records"]), 5)
        self.assertLessEqual({row["pk"] for row in response.json()["records"]}, expected)

    def test_common_filters_match_classic_prototype_and_ppt_full_scope(self):
        expected, params = self._multi_fixture()
        self.client.force_login(self.admin)
        found = set()
        for page in (1, 2):
            params["page"] = str(page)
            classic = self.client.get(reverse("hub:sqr"), params)
            self.assertEqual(classic.status_code, 200)
            self.assertEqual(classic.context["proposal_counts"]["total"], 55)
            found.update(row.pk for row in classic.context["proposal_submissions"])
        self.assertEqual(found, expected)
        params["page"] = "1"
        prototype = self.client.get(self.url, params).json()
        self.assertEqual(prototype["pipeline"]["all"]["count"], 55)
        ppt = self.client.get(reverse("hub:sqr-performance-data"), params)
        self.assertEqual(ppt.status_code, 200)
        self.assertEqual({row["pk"] for row in ppt.json()["projects"]}, expected)

    def test_invalid_or_out_of_scope_option_does_not_broaden(self):
        outside = User.objects.create_user(username="filter-outside", role="pm_esg")
        self.client.force_login(self.admin)
        for query in ("pm=not_a_number", f"pm={outside.pk}", "overall_status=in_progress&overall_status=invalid"):
            params = QueryDict(query + "&assigned=all")
            for url in (self.url, reverse("hub:sqr-performance-data")):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()["ok"])
            classic = self.client.get(reverse("hub:sqr"), params)
            self.assertEqual(classic.status_code, 200)
            self.assertTrue(classic.context["sqr_filter_error"])
            self.assertEqual(classic.context["page_obj"].paginator.count, 0)

    def test_my_assigned_stays_reviewer_based_and_clear_all_keeps_authorization(self):
        other_pm = User.objects.create_user(username="assignment-other-pm", role="pm_esg")
        reviewed_by_me = self._active(assigned_pm=other_pm)
        assigned_to_me = self._active(engineer=self.other, pm_esg_reviewer=other_pm, assigned_pm=self.pm)
        self.client.force_login(self.pm)
        for url, key in ((self.url, "records"), (reverse("hub:sqr-performance-data"), "projects")):
            response = self.client.get(url, {"overall_status": "in_progress"})
            self.assertEqual({row["pk"] for row in response.json()[key]}, {reviewed_by_me.pk})
            response = self.client.get(url, {"overall_status": "in_progress", "assigned": "all"})
            self.assertEqual({row["pk"] for row in response.json()[key]}, {reviewed_by_me.pk, assigned_to_me.pk})
        self.client.force_login(self.engineer)
        response = self.client.get(self.url, {"overall_status": "in_progress", "assigned": "all"})
        self.assertEqual({row["pk"] for row in response.json()["records"]}, {reviewed_by_me.pk})

    def test_not_recorded_group_and_ppt_missing_assessment_filters(self):
        missing = self._active(schedule_performance="", budget_effort_performance="over_budget", quality_performance="met", customer_company="ESS Delivery")
        risk = self._active(schedule_performance="at_risk", budget_effort_performance="within_budget", quality_performance="met", customer_company="ESS Delivery")
        self._active(schedule_performance="on_track", customer_company="Different group")
        self.client.force_login(self.admin)
        params = QueryDict("overall_status=in_progress&schedule_performance=not_recorded&schedule_performance=at_risk&group=ES&assigned=all")
        response = self.client.get(self.url, params)
        self.assertEqual({row["pk"] for row in response.json()["records"]}, {missing.pk, risk.pk})
        response = self.client.get(reverse("hub:sqr-performance-data"), {"overall_status": "in_progress", "rating": "danger", "missing_assessment": "1", "assigned": "all"})
        self.assertEqual({row["pk"] for row in response.json()["projects"]}, {missing.pk})

    def test_legacy_schedule_alias_and_unassigned_pm_keep_distinct_meanings(self):
        delayed = self._active(schedule_performance="delayed", delivery_health="off_track")
        green = self._active(schedule_performance="on_track", delivery_health="off_track", assigned_pm=self.pm)
        terminal = self._active(delivery_health="completed")
        self.client.force_login(self.admin)
        base = {"overall_status": "in_progress", "assigned": "all"}
        response = self.client.get(self.url, {**base, "delivery_health": "off_track"})
        self.assertEqual({row["pk"] for row in response.json()["records"]}, {delayed.pk})
        response = self.client.get(self.url, {**base, "delivery_health": "off_track", "schedule_performance": "on_track"})
        self.assertEqual({row["pk"] for row in response.json()["records"]}, {green.pk})
        response = self.client.get(self.url, {**base, "delivery_health": "completed"})
        self.assertEqual({row["pk"] for row in response.json()["records"]}, {terminal.pk})
        response = self.client.get(self.url, {**base, "pm": "unassigned"})
        self.assertEqual({row["pk"] for row in response.json()["records"]}, {delayed.pk, terminal.pk})
        for row in response.json()["records"]:
            self.assertEqual(row["values"]["assigned_pm"], "")
            self.assertTrue(row["assigned_pm_fallback"])

    def test_focus_bypasses_user_filters_and_page_but_never_authorization(self):
        expected, params = self._multi_fixture()
        focus = min(expected)
        params["overall_status"] = "planning"
        params["page"] = "99"
        params["focus"] = str(focus)
        self.client.force_login(self.admin)
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["pk"] for row in response.json()["records"]], [focus])
        hidden = self._active(engineer=self.other)
        params["focus"] = str(hidden.pk)
        self.client.force_login(self.engineer)
        response = self.client.get(self.url, params)
        self.assertEqual(response.json()["records"], [])
        for bad in ("²", "999999999999999999999999999999"):
            params["focus"] = bad
            response = self.client.get(self.url, params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["records"], [])

    def test_sql_stage_matches_python_terminal_and_performance_attention(self):
        from hub.services.sqr_queries import annotate_tracker

        for fields, stage, attention in (
            ({"delivery_health": "completed"}, "billing", False),
            ({"delivery_health": "cancelled"}, "closed", False),
            ({"delivery_completion_signed_date": TODAY}, "billing", False),
            ({"schedule_performance": "delayed"}, "delivery", True),
            ({"budget_effort_performance": "at_risk"}, "delivery", True),
            ({"quality_performance": "unmet", "status": "on_hold"}, "delivery", False),
            ({"delivery_health": "off_track", "delivery_target_finish_date": TODAY - timedelta(days=1),
              "schedule_performance": "on_track", "budget_effort_performance": "within_budget", "quality_performance": "met"}, "delivery", False),
        ):
            with self.subTest(fields=fields):
                row = self._active(**fields)
                annotated = annotate_tracker(SqrSubmission.objects.filter(pk=row.pk), TODAY).values("_pipeline", "_attention").get()
                self.assertEqual(annotated["_pipeline"], stage)
                self.assertEqual(proto.derive_stage(row)[0], stage)
                self.assertEqual(annotated["_attention"], attention)
                flags = proto.derive_attention(row, stage, TODAY)
                self.assertEqual(any(flag["tone"] in {"warning", "danger"} for flag in flags), attention)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
@skipUnless(shutil.which('node'), 'Node.js is required for tracker controller tests')
class TrackerLayoutScriptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='layout-admin', role=User.Roles.ADMIN)
        cls.pm = User.objects.create_user(username='layout-pm', role=User.Roles.PM_ESG)
        cls.other_pm = User.objects.create_user(username='layout-other-pm', role=User.Roles.PM_ESG)
        cls.engineer = User.objects.create_user(username='layout-engineer', role=User.Roles.ENGINEER)
        SqrSubmission.objects.bulk_create([
            SqrSubmission(reference_code=f'LAYOUT-{i}', year=2026, sequence_number=2000+i,
                          engineer=cls.engineer, pm_esg_reviewer=cls.pm,
                          assigned_pm=cls.other_pm if i % 2 else None,
                          customer_name=f'Fictional layout account {i}', project_title='Layout fixture',
                          project_details='Deployment Only', status='reviewed', proposal_status='closed_won',
                          overall_status='on_hold' if i % 2 else 'in_progress',
                          schedule_performance='on_track', budget_effort_performance='within_budget')
            for i in range(51)
        ])
        cls.params = [('assigned', 'all'), ('overall_status', 'in_progress'), ('overall_status', 'on_hold'),
                      ('pm', str(cls.pm.pk)), ('pm', str(cls.other_pm.pk)), ('page', '2')]
        cls.url = reverse('hub:sqr-prototype-data')

    def data(self, user=None):
        self.client.force_login(user or self.admin)
        response = self.client.get(self.url + '?' + urlencode(self.params))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def run_controller(self, scenario, data=None, **extra):
        payload = {'scenario': scenario, 'data': data or self.data(),
                   'query': '?view=prototype&' + urlencode(self.params), **extra}
        runner = Path(settings.BASE_DIR) / 'scripts_check' / 'test_sqr_prototype_layout.js'
        result = subprocess.run([shutil.which('node'), str(runner)], input=json.dumps(payload),
                                text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_list_table_switch_retains_filters_page_and_in_scope_selection(self):
        result = self.run_controller('switch', savedLayout='table',
                                     query='?view=prototype&layout=list&' + urlencode(self.params))
        self.assertEqual(result['initial_layout'], 'list')
        self.assertEqual(result['table_pks'], result['list_pks'])
        self.assertEqual(result['list_after'], result['list_pks'])
        self.assertEqual(result['selected_after'], result['selected_before'])
        self.assertTrue(result['count_preserved'])
        query = parse_qs(result['query'].lstrip('?'))
        self.assertEqual(query['pm'], [str(self.pm.pk), str(self.other_pm.pk)])
        self.assertEqual(query['overall_status'], ['in_progress', 'on_hold'])
        self.assertEqual(query['page'], ['2'])
        self.assertEqual(result['saved_layout'], 'list')
        self.assertEqual(result['writes'], [])

    def test_table_open_uses_existing_editor_and_save_path(self):
        before = self.data()
        record = before['records'][0]
        inline = record['urls']['inline']
        self.assertEqual(self.client.post(inline, data=json.dumps({'field': 'quality_performance', 'value': 'met'}),
                                         content_type='application/json').status_code, 200)
        after = self.data()
        result = self.run_controller('save', data=before, after=after, value='met')
        self.assertEqual(result['writes'], [{'url': inline, 'body': {'field': 'quality_performance', 'value': 'met'}}])
        self.assertEqual(result['after_list_pks'], result['after_table_pks'])
        self.assertEqual(result['selected_after_save'], record['pk'])
        performance = self.client.get(reverse('hub:sqr-performance-data') + '?assigned=all').json()
        project = next(row for row in performance['projects'] if row['pk'] == record['pk'])
        self.assertEqual(project['indicators']['quality']['value'], 'met')

    def test_invalid_layout_preference_defaults_to_list(self):
        for query, saved, expected in [('?view=prototype&layout=broken&', 'table', 'list'),
                                       ('?view=prototype&', '{corrupt', 'list'),
                                       ('?view=prototype&', 'table', 'table')]:
            with self.subTest(query=query, saved=saved):
                result = self.run_controller('invalid', query=query + urlencode(self.params), savedLayout=saved)
                self.assertEqual(result['initial_layout'], expected)

    def test_saved_filter_change_refreshes_counts_and_removes_nonmatching_row(self):
        before = self.data()
        record = before['records'][0]
        inline = record['urls']['inline']
        self.assertEqual(self.client.post(inline, data=json.dumps({'field': 'overall_status', 'value': 'completed'}),
                                         content_type='application/json').status_code, 200)
        after = self.data()
        self.assertEqual(after['pagination']['page'], 1)
        result = self.run_controller('membership', data=before, after=after, value='completed')
        self.assertIsNone(result['selected_after_save'])
        self.assertNotIn(record['pk'], result['after_table_pks'])
        self.assertEqual(result['total_after'], 50)
        self.assertEqual(result['page_after'], 1)
        self.assertIn('no longer matches', result['toast'])
        requests = [parse_qs(urlparse(url).query)
                    for url in result['requests'] if 'prototype-data' in url]
        self.assertEqual(requests[-1]['overall_status'], ['in_progress', 'on_hold'])
        self.assertEqual(requests[-1]['pm'], [str(self.pm.pk), str(self.other_pm.pk)])
        self.assertNotIn('pk', requests[-1])

    def test_table_open_preserves_readonly_and_locked_records(self):
        readonly = self.data(self.engineer)
        self.assertEqual(self.run_controller('readonly', data=readonly)['writes'], [])
        self.client.force_login(self.admin)
        pk = readonly['records'][0]['pk']
        SqrSubmission.objects.filter(pk=pk).update(proposal_status='closed_lost')
        locked = self.data()
        self.assertTrue(locked['records'][0]['locked'])
        self.assertEqual(self.run_controller('readonly', data=locked)['writes'], [])


class SqrTrackerVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='visibility-admin', role=User.Roles.ADMIN)
        cls.pm = User.objects.create_user(username='visibility-pm', role=User.Roles.PM_ESG)
        cls.engineer = User.objects.create_user(username='visibility-engineer', role=User.Roles.ENGINEER)
        cls.other = User.objects.create_user(username='visibility-other', role=User.Roles.ENGINEER)
        cls.record = SqrSubmission.objects.create(engineer=cls.engineer, pm_esg_reviewer=cls.pm,
                                                  customer_name='Fictional visibility account', project_title='Visibility fixture')

    def test_only_active_admin_can_persist_classic_visibility(self):
        from hub.models import SqrTrackerSettings
        from hub.services.sqr_settings import classic_tracker_visible
        url = reverse('hub:sqr-tracker-visibility')
        SqrTrackerSettings.objects.all().delete()
        self.assertTrue(classic_tracker_visible())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('hub:sqr')).status_code, 200)
        self.assertFalse(SqrTrackerSettings.objects.exists(), 'GET must not seed the singleton')
        self.assertEqual(self.client.get(url).status_code, 405)
        for value in (False, True, False):
            response = self.client.post(url, {'show_classic_sqr_tracker': value}, content_type='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'ok': True, 'show_classic_sqr_tracker': value, 'default_view': 'prototype'})
            self.assertEqual(classic_tracker_visible(), value)
        for payload in ({'show_classic_sqr_tracker': 'false'}, {'show_classic_sqr_tracker': None},
                        {'show_classic_sqr_tracker': False, 'unknown': True}, {}, []):
            self.assertEqual(self.client.post(url, payload, content_type='application/json').status_code, 400)
            self.assertFalse(classic_tracker_visible())
        for user in (self.pm, self.engineer):
            self.client.force_login(user)
            self.assertEqual(self.client.post(url, {'show_classic_sqr_tracker': True}, content_type='application/json').status_code, 403)
        from django.test import RequestFactory, Client
        from hub.views import SqrTrackerVisibilityView
        self.admin.is_active = False
        request = RequestFactory().post(url, {'show_classic_sqr_tracker': True}, content_type='application/json')
        request.user = self.admin
        self.assertEqual(SqrTrackerVisibilityView.as_view()(request).status_code, 403)
        self.admin.is_active = True
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        self.assertEqual(csrf.post(url, {'show_classic_sqr_tracker': True}, content_type='application/json').status_code, 403)
        self.assertFalse(classic_tracker_visible())

    def test_hidden_classic_url_and_stale_preference_fall_back_with_safe_focus(self):
        from hub.models import SqrTrackerSettings
        SqrTrackerSettings.objects.update_or_create(pk=1, defaults={'show_classic_sqr_tracker': False})
        self.client.force_login(self.pm)
        response = self.client.get(reverse('hub:sqr'), {'view': 'tracker', 'focus': self.record.pk, 'q': 'excluded by filter'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['sqr_active_view'], 'prototype')
        self.assertNotContains(response, 'data-sqr-view-button="tracker"')
        self.assertNotContains(response, 'data-sqr-view-panel="tracker"')
        self.assertContains(response, 'data-sqr-view-panel="prototype"')
        self.assertContains(response, 'id="sqr-proto-focus"')
        self.assertEqual(response.context['request'].GET['q'], 'excluded by filter')
        focused = self.client.get(reverse('hub:sqr-prototype-data'), {'focus': self.record.pk, 'q': 'excluded by filter', 'page': 40}).json()
        self.assertEqual([row['pk'] for row in focused['records']], [self.record.pk])
        self.client.force_login(self.other)
        missing = self.client.get(reverse('hub:sqr-prototype-data'), {'focus': self.record.pk}).json()
        self.assertEqual(missing['records'], [])

    def test_main_sqr_navigation_is_independent_of_last_ppt_visit(self):
        self.client.force_login(self.pm)
        response = self.client.get(reverse('hub:sqr'), {'view': 'performance'})
        self.assertContains(response, 'href="/sqr/?view=prototype" data-sqr-nav-view="prototype"')
        self.client.force_login(self.engineer)
        response = self.client.get(reverse('hub:sqr'), {'view': 'performance'})
        self.assertEqual(response.context['sqr_active_view'], 'prototype')
        self.assertNotContains(response, 'data-sqr-nav-view="performance"')
        self.assertNotContains(response, 'id="sqr-performance-dashboard"')

    def test_visibility_singleton_is_guarded_in_database(self):
        from django.db import IntegrityError, transaction
        from hub.models import SqrTrackerSettings
        with self.assertRaises(IntegrityError), transaction.atomic():
            SqrTrackerSettings.objects.create(pk=2, show_classic_sqr_tracker=False)


class PrototypeTabRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pm = User.objects.create_user(username="tab-pm", role=User.Roles.PM_ESG)
        cls.engineer = User.objects.create_user(username="tab-eng", role=User.Roles.ENGINEER)

    def test_pm_can_navigate_to_tracker_and_reports_from_either_page(self):
        self.client.force_login(self.pm)
        for tab in ("table", "reports"):
            with self.subTest(tab=tab):
                response = self.client.get(reverse("hub:sqr"), {"tab": tab})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "To SQR Tracker", count=1)
                self.assertContains(response, "To Reports", count=1)
                for destination, label, icon, style in (
                    ("table", "To SQR Tracker", "bi-table", "btn-primary lg-btn--accent"),
                    ("reports", "To Reports", "bi-bar-chart-line-fill", "btn-outline-primary lg-btn--ghost"),
                ):
                    href = "/sqr/?view=prototype" if destination == "table" else "/sqr/?tab=reports"
                    tracker_link = " data-sqr-tracker-link" if destination == "table" else ""
                    current = ' aria-current="page"' if tab == destination else ""
                    self.assertContains(
                        response,
                        f'<a href="{href}" class="btn btn-sm sqr-toggle-btn lg-btn {style}"{tracker_link}{current}>'
                        f'<i class="bi {icon} me-1" aria-hidden="true"></i>{label}</a>',
                        html=True,
                    )

    def test_engineer_does_not_get_pm_navigation_buttons(self):
        self.client.force_login(self.engineer)
        response = self.client.get(reverse("hub:sqr"))
        self.assertNotContains(response, "To SQR Tracker")
        self.assertNotContains(response, "To Reports")

    def test_every_sqr_role_gets_the_prototype_tab_and_panel(self):
        for user in (self.pm, self.engineer):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(reverse("hub:sqr"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-sqr-view-button="prototype"')
                self.assertContains(response, 'aria-label="SQR Tracker layout"')
                self.assertContains(response, 'data-sqr-view-panel="prototype"')
                self.assertContains(response, reverse("hub:sqr-prototype-data"))
