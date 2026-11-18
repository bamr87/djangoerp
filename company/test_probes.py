"""
The deployment probes (hub specs/BACKEND.md UPS-BE-10): GET /healthz for liveness, GET /readyz for readiness.

Both are public and uncached. /healthz touches nothing; /readyz checks the database and, unless Celery runs tasks
eagerly, the Redis broker, and answers 503 "degraded" when a check fails.
"""

from unittest import mock

from django.db import OperationalError, connection
from django.test import TestCase, override_settings
from django.urls import reverse


class HealthzTests(TestCase):
    def test_paths_follow_the_fleet_contract(self):
        self.assertEqual(reverse('company:healthz'), '/healthz')
        self.assertEqual(reverse('company:readyz'), '/readyz')

    def test_healthz_is_ok_without_touching_the_database(self):
        with self.assertNumQueries(0):
            response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertIn('no-store', response['Cache-Control'])


# Pinned, because dev settings read CELERY_TASK_ALWAYS_EAGER from the environment (default True).
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ReadyzTests(TestCase):
    def test_ready_when_the_database_answers(self):
        with self.assertNumQueries(1):  # SELECT 1, nothing else
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'checks': {'db': 'ok', 'broker': 'skipped'}})
        self.assertIn('no-store', response['Cache-Control'])

    def test_degraded_when_the_database_check_fails(self):
        refused = OperationalError('could not connect to server: Connection refused')
        with (
            mock.patch.object(connection, 'cursor', side_effect=refused),
            self.assertLogs('company.views', 'WARNING') as logs,
        ):
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'degraded', 'checks': {'db': 'error', 'broker': 'skipped'}})
        self.assertIn('database check failed', logs.output[0])
        self.assertNotIn('Connection refused', response.content.decode())  # the reason is logged, not served

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL='redis://broker.invalid:6379/0')
    def test_the_broker_is_pinged_when_tasks_go_to_a_worker(self):
        with mock.patch('company.views.redis.Redis.ping', return_value=True) as ping:
            response = self.client.get('/readyz')
        ping.assert_called_once_with()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'checks': {'db': 'ok', 'broker': 'ok'}})

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL='redis://127.0.0.1:1/0')
    def test_degraded_when_the_broker_refuses_the_connection(self):
        with self.assertLogs('company.views', 'WARNING') as logs:
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'degraded', 'checks': {'db': 'ok', 'broker': 'error'}})
        self.assertIn('broker check failed', logs.output[0])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL='amqp://guest@localhost//')
    def test_degraded_when_the_broker_url_is_not_redis(self):
        with self.assertLogs('company.views', 'WARNING'):
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['checks']['broker'], 'error')
