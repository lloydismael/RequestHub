import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connections
from django.test import RequestFactory, TransactionTestCase
from django.utils import timezone

from accounts.models import User
from hub.assistant_runtime import (
    AssistantGlobalCapacityError,
    AssistantRequestConflictError,
    AssistantRateLimitError,
    AssistantRequestInProgressError,
    acquire_request,
    append_exchange_if_current,
    load_cached_request_response,
    load_conversation,
    prune_expired_runtime,
    release_request,
    reset_conversation,
)
from hub.models import AssistantUsageLimit


class AssistantRuntimeTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = self.create_user("runtime-owner")

    def create_user(self, username):
        return User.objects.create_user(
            username=username,
            password="test-password",
            first_name="Runtime",
            last_name="User",
            role=User.Roles.ADMIN,
        )

    def make_request(self, user):
        session = SessionStore()
        session["_auth_user_id"] = str(user.pk)
        session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
        session["_auth_user_hash"] = user.get_session_auth_hash()
        session.create()

        request = RequestFactory().get("/")
        request.session = session
        request.user = user
        return request

    def test_twenty_first_attempt_is_rejected_until_window_boundary(self):
        for _ in range(20):
            lease = acquire_request(self.user, uuid4(), self.now)
            release_request(lease)

        with self.assertRaises(AssistantRateLimitError):
            acquire_request(self.user, uuid4(), self.now + timedelta(minutes=9, seconds=59))

        lease = acquire_request(self.user, uuid4(), self.now + timedelta(minutes=10))
        release_request(lease)

    def test_one_user_cannot_have_overlapping_requests(self):
        lease = acquire_request(self.user, uuid4(), self.now)
        with self.assertRaises(AssistantRequestInProgressError):
            acquire_request(self.user, uuid4(), self.now + timedelta(seconds=1))
        release_request(lease)

    def test_third_global_request_is_rejected(self):
        second_user = self.create_user("runtime-second")
        third_user = self.create_user("runtime-third")
        first = acquire_request(self.user, uuid4(), self.now)
        second = acquire_request(second_user, uuid4(), self.now)

        with self.assertRaises(AssistantGlobalCapacityError):
            acquire_request(third_user, uuid4(), self.now)

        self.assertEqual(
            AssistantUsageLimit.objects.filter(
                scope_key__startswith="global:",
                lease_expires_at__gt=self.now,
            ).count(),
            2,
        )
        release_request(first)
        release_request(second)

    def test_expired_worker_lease_is_reclaimed_and_old_release_is_owner_safe(self):
        old_lease = acquire_request(self.user, uuid4(), self.now)
        replacement = acquire_request(
            self.user,
            uuid4(),
            self.now + timedelta(seconds=61),
        )

        release_request(old_lease)
        with self.assertRaises(AssistantRequestInProgressError):
            acquire_request(
                self.user,
                uuid4(),
                self.now + timedelta(seconds=62),
            )

        release_request(replacement)
        recovered = acquire_request(
            self.user,
            uuid4(),
            self.now + timedelta(seconds=63),
        )
        release_request(recovered)

    def test_two_independent_database_connections_share_user_lock(self):
        barrier = threading.Barrier(2)

        def acquire_from_worker():
            connections.close_all()
            barrier.wait(timeout=10)
            try:
                return ("acquired", acquire_request(self.user, uuid4(), self.now))
            except AssistantRequestInProgressError:
                return ("busy", None)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: acquire_from_worker(), range(2)))

        self.assertCountEqual([status for status, _ in results], ["acquired", "busy"])
        for status, lease in results:
            if status == "acquired":
                release_request(lease)

    def test_conversations_are_session_owned_and_capped_by_exchange_count(self):
        request = self.make_request(self.user)
        other_request = self.make_request(self.create_user("runtime-other-session"))

        state = load_conversation(request)
        other_state = load_conversation(other_request)
        self.assertNotEqual(state.id, other_state.id)

        for index in range(15):
            self.assertTrue(
                append_exchange_if_current(
                    request,
                    state,
                    user_text=f"question {index}",
                    assistant_text=f"answer {index}",
                )
            )
            state = load_conversation(request)

        self.assertEqual(len(state.exchanges), 12)
        self.assertEqual(state.exchanges[0]["user"], "question 3")
        self.assertLessEqual(
            len(json.dumps(state.exchanges, separators=(",", ":")).encode("utf-8")),
            64 * 1024,
        )

    def test_history_discards_old_whole_exchanges_to_remain_under_byte_cap(self):
        request = self.make_request(self.user)
        state = load_conversation(request)

        for index in range(12):
            self.assertTrue(
                append_exchange_if_current(
                    request,
                    state,
                    user_text=f"question-{index}",
                    assistant_text="a" * 8000,
                )
            )
            state = load_conversation(request)

        self.assertLessEqual(len(state.exchanges), 8)
        self.assertLessEqual(
            len(json.dumps(state.exchanges, separators=(",", ":")).encode("utf-8")),
            64 * 1024,
        )
        self.assertTrue(all(set(exchange) == {"user", "assistant"} for exchange in state.exchanges))

    def test_reset_invalidates_old_generation_and_old_exchange(self):
        request = self.make_request(self.user)
        old_state = load_conversation(request)
        new_state = reset_conversation(request)

        self.assertNotEqual(old_state.id, new_state.id)
        self.assertFalse(
            append_exchange_if_current(
                request,
                old_state,
                user_text="old",
                assistant_text="obsolete",
            )
        )

    def test_role_change_during_inference_invalidates_response(self):
        request = self.make_request(self.user)
        old_state = load_conversation(request)
        User.objects.filter(pk=self.user.pk).update(role=User.Roles.ENGINEER)

        self.assertFalse(
            append_exchange_if_current(
                request,
                old_state,
                user_text="pending",
                assistant_text="obsolete",
            )
        )
        refreshed_state = load_conversation(request)
        self.assertNotEqual(old_state.id, refreshed_state.id)
        self.assertEqual(refreshed_state.exchanges, ())

    def test_logout_during_inference_does_not_restore_session_state(self):
        request = self.make_request(self.user)
        old_state = load_conversation(request)
        request.session.flush()

        self.assertFalse(
            append_exchange_if_current(
                request,
                old_state,
                user_text="pending",
                assistant_text="obsolete",
            )
        )

    def test_completed_request_cache_is_bound_to_session_generation_and_payload(self):
        request = self.make_request(self.user)
        state = load_conversation(request)
        request_id = uuid4()
        request_fingerprint = "a" * 64
        response = {"text": "Verified answer", "sources": []}

        self.assertTrue(
            append_exchange_if_current(
                request,
                state,
                user_text="What is SQR?",
                assistant_text="Verified answer",
                request_id=request_id,
                request_fingerprint=request_fingerprint,
                response_cache=response,
            )
        )
        self.assertEqual(
            load_cached_request_response(
                request,
                state,
                request_id,
                request_fingerprint,
                self.now,
            ),
            response,
        )
        with self.assertRaises(AssistantRequestConflictError):
            load_cached_request_response(
                request,
                state,
                request_id,
                "b" * 64,
                self.now,
            )

        reset_state = reset_conversation(request)
        self.assertIsNone(
            load_cached_request_response(
                request,
                reset_state,
                request_id,
                request_fingerprint,
                self.now,
            )
        )

    def test_expired_runtime_rows_are_pruned_but_active_session_and_global_slots_remain(self):
        request = self.make_request(self.user)
        load_conversation(request)
        lease = acquire_request(self.user, uuid4(), self.now)
        release_request(lease)

        expired_session = self.make_request(self.create_user("runtime-expired-session"))
        expired_session.session.set_expiry(60)
        expired_session.session.save()
        load_conversation(expired_session)
        Session.objects.filter(
            session_key=expired_session.session.session_key,
        ).update(expire_date=self.now - timedelta(seconds=1))

        AssistantUsageLimit.objects.get_or_create(scope_key="user:expired-window")
        AssistantUsageLimit.objects.filter(scope_key="user:expired-window").update(
            window_started_at=self.now - timedelta(days=2),
        )

        pruned = prune_expired_runtime(self.now + timedelta(minutes=2))

        self.assertGreaterEqual(pruned, 1)
        self.assertTrue(
            AssistantUsageLimit.objects.filter(scope_key__startswith="session:").exists()
        )
        self.assertEqual(
            AssistantUsageLimit.objects.filter(scope_key__startswith="global:").count(),
            2,
        )
