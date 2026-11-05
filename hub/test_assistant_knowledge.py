from dataclasses import asdict
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from hub.assistant_directory import (
    AssistantDirectoryNotFoundError,
    AssistantDirectoryValidationError,
    find_department_members,
    get_department_overview,
    list_departments,
)
from hub.assistant_knowledge import (
    AssistantCatalogError,
    GuideNotFoundError,
    allowed_destinations,
    approved_department_overviews,
    get_guide,
    normalize_department,
    validate_catalog,
)
from hub.assistant_types import DepartmentSummary


class AssistantKnowledgeTests(TestCase):
    def make_user(self, role):
        return User(role=role, is_active=True)

    def test_destinations_match_existing_navigation_roles(self):
        expected = {
            User.Roles.REQUESTOR: {"dashboard", "notifications", "profile"},
            User.Roles.REQUESTOR_ESS: {"dashboard", "notifications", "profile"},
            User.Roles.PM_ESS: {"dashboard", "notifications", "profile"},
            User.Roles.PM_ESG: {"dashboard", "sqr", "reports", "notifications", "profile"},
            User.Roles.ENGINEER: {"dashboard", "activity_logs", "sqr", "notifications", "profile"},
            User.Roles.ON_HOLD: {"dashboard", "activity_logs", "sqr", "notifications", "profile"},
            User.Roles.ADMIN: {
                "dashboard", "sqr", "management", "reports",
                "notifications", "profile",
            },
        }

        for role, expected_keys in expected.items():
            with self.subTest(role=role):
                cards = allowed_destinations(self.make_user(role), "", None)
                self.assertEqual({card.key for card in cards}, expected_keys)

    def test_destinations_resolve_only_named_routes_and_filter_current_page(self):
        admin = self.make_user(User.Roles.ADMIN)
        cards = {card.key: card for card in allowed_destinations(admin, "", None)}
        route_names = {
            "dashboard": "hub:dashboard",
            "activity_logs": "hub:activity-logs",
            "sqr": "hub:sqr",
            "management": "hub:management",
            "reports": "hub:report",
            "notifications": "hub:notifications",
            "profile": "accounts:update",
        }

        for key, route_name in route_names.items():
            if key == "activity_logs":
                self.assertNotIn(key, cards)
                continue
            self.assertEqual(cards[key].url, reverse(route_name))
        engineer_cards = {
            card.key: card
            for card in allowed_destinations(self.make_user(User.Roles.ENGINEER), "", None)
        }
        self.assertEqual(
            engineer_cards["activity_logs"].url,
            reverse("hub:activity-logs"),
        )
        self.assertEqual(
            {card.key for card in allowed_destinations(admin, "sqr", None)},
            {"sqr"},
        )
        self.assertNotIn(
            "dashboard",
            {
                card.key
                for card in allowed_destinations(admin, "", "dashboard")
            },
        )

    def test_guide_audiences_match_existing_navigation_permissions(self):
        destination_access = {
            User.Roles.REQUESTOR: {"dashboard", "notifications", "profile"},
            User.Roles.REQUESTOR_ESS: {"dashboard", "notifications", "profile"},
            User.Roles.PM_ESS: {"dashboard", "notifications", "profile"},
            User.Roles.PM_ESG: {"dashboard", "sqr", "reports", "notifications", "profile"},
            User.Roles.ENGINEER: {"dashboard", "activity_logs", "sqr", "notifications", "profile"},
            User.Roles.ON_HOLD: {"dashboard", "activity_logs", "sqr", "notifications", "profile"},
            User.Roles.ADMIN: {
                "dashboard", "sqr", "management", "reports", "notifications", "profile",
            },
        }

        for role, allowed_keys in destination_access.items():
            user = self.make_user(role)
            for guide_key in {
                "dashboard", "activity_logs", "sqr", "management",
                "reports", "notifications", "profile",
            }:
                with self.subTest(role=role, guide=guide_key):
                    if guide_key in allowed_keys:
                        self.assertEqual(get_guide(user, guide_key).key, guide_key)
                    else:
                        with self.assertRaises(PermissionDenied):
                            get_guide(user, guide_key)

    def test_requestor_cannot_open_an_admin_guide_directly(self):
        with self.assertRaises(PermissionDenied):
            get_guide(self.make_user(User.Roles.REQUESTOR), "management")

    def test_unknown_guide_has_an_explicit_not_found_error(self):
        with self.assertRaises(GuideNotFoundError):
            get_guide(self.make_user(User.Roles.ADMIN), "unpublished-feature")

    def test_guides_require_an_active_authenticated_user(self):
        with self.assertRaises(PermissionDenied):
            get_guide(AnonymousUser(), "dashboard")
        with self.assertRaises(PermissionDenied):
            get_guide(User(role=User.Roles.ADMIN, is_active=False), "management")

    def test_department_keys_are_trimmed_and_casefolded(self):
        self.assertEqual(normalize_department("  Engineering "), "engineering")

    def test_department_overviews_are_empty_until_facts_are_approved(self):
        self.assertEqual(approved_department_overviews(), ())

    def test_catalog_rejects_noncanonical_department_keys(self):
        invalid_overview = DepartmentSummary(
            key="Engineering",
            name="Engineering",
            summary="Approved summary",
            responsibilities=("Approved responsibility",),
            member_count=0,
        )
        with patch.dict(
            "hub.assistant_knowledge.DEPARTMENT_CATALOG",
            {"Engineering": invalid_overview},
            clear=True,
        ):
            with self.assertRaises(AssistantCatalogError):
                validate_catalog()


class AssistantDirectoryTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(
            username="directory-viewer",
            password="test-password",
            first_name="Directory",
            last_name="Viewer",
            role=User.Roles.ADMIN,
            department="Administration",
        )

    def create_member(
        self,
        username,
        *,
        first_name="",
        last_name="",
        department="",
        role=User.Roles.REQUESTOR,
        is_active=True,
        email="",
    ):
        return User.objects.create_user(
            username=username,
            password="test-password",
            first_name=first_name,
            last_name=last_name,
            department=department,
            role=role,
            is_active=is_active,
            email=email,
        )

    def test_directory_projects_only_active_members_and_safe_fields(self):
        blank_member_count = sum(
            not department.strip()
            for department in User.objects.filter(is_active=True).values_list(
                "department",
                flat=True,
            )
        )
        self.create_member(
            "markup-member",
            first_name="<img src=x>",
            last_name="Example",
            department=" Assistant Test Engineering ",
            role=User.Roles.ENGINEER,
            email="private@example.invalid",
        )
        self.create_member(
            "unnamed-member",
            department="assistant test engineering",
            email="hidden@example.invalid",
        )
        self.create_member(
            "inactive-member",
            first_name="Inactive",
            last_name="Person",
            department="Assistant Test Engineering",
            is_active=False,
        )
        self.create_member(
            "unassigned-member",
            department="   ",
        )

        page = find_department_members(
            self.viewer,
            department_key="ASSISTANT TEST ENGINEERING",
        )

        self.assertEqual(page.total, 2)
        self.assertEqual(
            set(asdict(page.items[0])),
            {"display_name", "department", "application_role"},
        )
        members_by_name = {member.display_name: member for member in page.items}
        self.assertIn("<img src=x> Example", members_by_name)
        self.assertEqual(members_by_name["Unnamed member"].display_name, "Unnamed member")
        self.assertEqual(
            members_by_name["<img src=x> Example"].department,
            "Assistant Test Engineering",
        )
        self.assertEqual(members_by_name["<img src=x> Example"].application_role, "Engineer")
        self.assertNotIn(
            "Inactive Person",
            {member.display_name for member in page.items},
        )

        unspecified = get_department_overview(
            self.viewer,
            "Department not specified",
        )
        self.assertEqual(unspecified.member_count, blank_member_count + 1)
        self.assertEqual(unspecified.key, "department-not-specified")

    def test_department_keys_group_case_and_whitespace_but_not_abbreviations(self):
        self.create_member("qa-one", department=" Assistant QA ")
        self.create_member("qa-two", department="assistant qa")
        self.create_member("qa-team-one", department="Assistant QA-Team")
        self.create_member("qa-team-esg-one", department="Assistant QA-Team-ESG")

        departments, total = list_departments(self.viewer, query="Assistant QA")
        departments_by_key = {department.key: department for department in departments}

        self.assertEqual(total, 3)
        self.assertEqual(departments_by_key["assistant qa"].member_count, 2)
        self.assertEqual(departments_by_key["assistant qa-team"].member_count, 1)
        self.assertEqual(departments_by_key["assistant qa-team-esg"].member_count, 1)

    def test_approved_overview_without_members_is_available(self):
        overview = DepartmentSummary(
            key="finops",
            name="FinOps",
            summary="Approved summary",
            responsibilities=("Approved responsibility",),
            member_count=0,
        )
        with patch.dict(
            "hub.assistant_knowledge.DEPARTMENT_CATALOG",
            {"finops": overview},
            clear=True,
        ):
            departments, total = list_departments(self.viewer, query="FinOps")
            empty_page = find_department_members(
                self.viewer,
                department_key="finops",
            )

        self.assertEqual(total, 1)
        self.assertEqual(departments[0].summary, "Approved summary")
        self.assertEqual(departments[0].member_count, 0)
        self.assertEqual(empty_page.items, ())
        self.assertEqual(empty_page.total, 0)

    def test_unknown_department_is_not_found(self):
        with self.assertRaises(AssistantDirectoryNotFoundError):
            get_department_overview(self.viewer, "unpublished-team")

    def test_member_pages_are_stable_and_limited_to_twenty(self):
        for index in range(21):
            self.create_member(
                f"engineering-{index:02}",
                first_name=f"Member {index:02}",
                department="Assistant Directory QA",
            )

        first_page = find_department_members(
            self.viewer,
            department_key="assistant directory qa",
        )
        second_page = find_department_members(
            self.viewer,
            department_key="assistant directory qa",
            page=2,
        )

        self.assertEqual(len(first_page.items), 20)
        self.assertEqual(first_page.total, 21)
        self.assertEqual(len(second_page.items), 1)
        self.assertEqual(second_page.items[0].display_name, "Member 20")

    def test_member_search_does_not_match_email_or_username(self):
        self.create_member(
            "secret-username",
            first_name="Visible",
            last_name="Name",
            department="Assistant Directory Search",
            email="private@example.invalid",
        )

        self.assertEqual(
            find_department_members(
                self.viewer,
                department_key="assistant directory search",
                query="secret-username",
            ).total,
            0,
        )
        self.assertEqual(
            find_department_members(
                self.viewer,
                department_key="assistant directory search",
                query="private@example.invalid",
            ).total,
            0,
        )

    def test_invalid_queries_and_pages_are_rejected(self):
        with self.assertRaises(AssistantDirectoryValidationError):
            list_departments(self.viewer, query="x" * 101)
        with self.assertRaises(AssistantDirectoryValidationError):
            list_departments(self.viewer, page=0)
        with self.assertRaises(AssistantDirectoryValidationError):
            list_departments(self.viewer, page=True)

    def test_directory_requires_an_active_authenticated_user(self):
        with self.assertRaises(PermissionDenied):
            list_departments(AnonymousUser())
        with self.assertRaises(PermissionDenied):
            list_departments(User(role=User.Roles.ADMIN, is_active=False))
