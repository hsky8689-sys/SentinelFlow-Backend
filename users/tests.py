import secrets
from datetime import date

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from users.models import User


class UsersTests(TestCase):
    def setUp(self):
        # django_ratelimit's counters live in Redis, not the DB - TestCase's
        # transaction rollback doesn't touch them, so leftover counts from a
        # previous test (or a previous run) would otherwise leak in here and
        # trip a limit early. Clearing at the start of setUp (not tearDown)
        # means every test gets a clean slate regardless of run order.
        cache.clear()
        self.users = []
        for _ in range(3):
            suffix = secrets.token_hex(4)
            user = User.objects.create_user(
                username=f'testuser_{suffix}',
                email=f'testuser_{suffix}@example.com',
                password='CorrectHorseBatteryStaple123',
                birthday=date(2000, 1, 1),
            )
            self.users.append(user)

    def tearDown(self):
        pass

    def test_login_rate_limit_blocks_after_five_n_per_username(self):
        """
        login_page rate-limits POST by 'post:username' at 5/m. The first 5
        attempts (wrong password) should each behave like a normal failed
        login (401 JSON error response); the 6th within the same minute
        should be blocked by django_ratelimit before the view even runs.
        """
        target_user = self.users[0]
        login_url = reverse('user_login')

        for attempt in range(5):
            response = self.client.post(login_url, {
                'username': target_user.username,
                'password': 'wrong-password',
            })
            self.assertEqual(
                response.status_code, 401,
                f"attempt {attempt + 1} should be a normal failed-login response, got {response.status_code}"
            )

        blocked_response = self.client.post(login_url, {
            'username': target_user.username,
            'password': 'wrong-password',
        })
        self.assertEqual(
            blocked_response.status_code, 403,
            "6th attempt within a minute should be rate-limited (post:username, 5/m)"
        )

    def test_login_rate_limit_blocks_after_ten_attempts_per_ip(self):
        """
        login_page also rate-limits POST by 'ip' at 10/m, independent of
        username. A different (nonexistent) username on every attempt keeps
        the stricter 'post:username' limit (5/m) from tripping first, so this
        isolates the per-IP limit specifically.
        """
        login_url = reverse('user_login')

        for attempt in range(10):
            response = self.client.post(login_url, {
                'username': f'nonexistent_{attempt}_{secrets.token_hex(4)}',
                'password': 'wrong-password',
            })
            self.assertEqual(
                response.status_code, 401,
                f"attempt {attempt + 1} should be a normal failed-login response, got {response.status_code}"
            )

        blocked_response = self.client.post(login_url, {
            'username': f'nonexistent_10_{secrets.token_hex(4)}',
            'password': 'wrong-password',
        })
        self.assertEqual(
            blocked_response.status_code, 403,
            "11th attempt within a minute from the same IP should be rate-limited (ip, 10/m)"
        )

    def test_login_page_get_rate_limit_blocks_after_twenty_requests(self):
        """
        login_page rate-limits GET by 'user_or_ip' at 20/m (an anonymous
        client falls back to its IP for this key).
        """
        login_url = reverse('user_login')

        for attempt in range(20):
            response = self.client.get(login_url)
            self.assertEqual(
                response.status_code, 200,
                f"GET attempt {attempt + 1} should render the login page normally"
            )

        blocked_response = self.client.get(login_url)
        self.assertEqual(
            blocked_response.status_code, 403,
            "21st GET within a minute should be rate-limited (user_or_ip, 20/m)"
        )
