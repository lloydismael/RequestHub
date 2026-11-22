from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from hub.models import Notification, Request


class Command(BaseCommand):
    help = "Send notifications for requests that exceeded their SLA due date."

    def handle(self, *args, **options):
        today = timezone.now().date()
        overdue_requests = Request.objects.filter(status=Request.Status.ONGOING, due_date__lt=today)
        if not overdue_requests.exists():
            self.stdout.write(self.style.SUCCESS("No overdue requests found."))
            return

        admins = User.objects.filter(role=User.Roles.ADMIN)
        created_notifications = 0

        for request in overdue_requests:
            message = f"Request {request.reference_code} exceeded its SLA target date."
            recipients = list(admins)
            if request.engineer:
                recipients.append(request.engineer)
            for recipient in recipients:
                _, created = Notification.objects.get_or_create(
                    recipient=recipient,
                    related_request=request,
                    message=message,
                )
                if created:
                    created_notifications += 1

        self.stdout.write(self.style.SUCCESS(f"Processed {overdue_requests.count()} requests, created {created_notifications} notifications."))
