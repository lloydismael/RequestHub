from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from .forms import RequestAdminForm
from .models import Account, Request


class RequestPeopleValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="people-validation-admin", role=User.Roles.ADMIN)
        cls.requestor = User.objects.create_user(username="people-validation-requestor", role=User.Roles.REQUESTOR)
        cls.engineer = User.objects.create_user(username="people-validation-engineer", role=User.Roles.ENGINEER)
        cls.other_engineer = User.objects.create_user(username="people-validation-other", role=User.Roles.ENGINEER)
        cls.account = Account.objects.create(name="People validation account")

    def make_request(self, owner):
        return Request.objects.create(
            requestor=owner,
            account=self.account,
            account_manager="Account Manager",
            product_category="Azure",
            engagement_type=Request.Engagement.SUPPORT,
            engineer=self.engineer,
        )

    def make_form(self, request, owner, assignee):
        return RequestAdminForm(
            data={
                "account_name": self.account.name,
                "request_date": timezone.localdate().isoformat(),
                "requestor": str(owner.pk),
                "priority": request.priority,
                "status": request.status,
                "engineer": str(assignee.pk),
                "backup_engineer": "",
                "due_date": "",
                "end_date": "",
                "description": "Updated request",
            },
            instance=request,
        )

    def test_can_reassign_requestor_to_admin(self):
        request = self.make_request(self.requestor)
        form = self.make_form(request, self.admin, self.engineer)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        request.refresh_from_db()
        self.assertEqual(request.requestor_id, self.admin.pk)

    def test_can_reassign_engineer_while_retaining_admin_requestor(self):
        request = self.make_request(self.admin)
        form = self.make_form(request, self.admin, self.other_engineer)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        request.refresh_from_db()
        self.assertEqual(request.requestor_id, self.admin.pk)
        self.assertEqual(request.engineer_id, self.other_engineer.pk)

    def test_all_request_creator_roles_pass_requestor_model_validation(self):
        for role in User.REQUEST_CREATOR_ROLES:
            with self.subTest(role=role):
                owner = User.objects.create_user(username=f"people-validation-owner-{role}", role=role)
                request = self.make_request(owner)
                request.full_clean()

    def test_engineer_cannot_be_selected_as_requestor(self):
        request = self.make_request(self.requestor)
        form = self.make_form(request, self.engineer, self.other_engineer)
        self.assertFalse(form.is_valid())
        self.assertIn("requestor", form.errors)
        request.refresh_from_db()
        self.assertEqual(request.requestor_id, self.requestor.pk)
        self.assertEqual(request.engineer_id, self.engineer.pk)
