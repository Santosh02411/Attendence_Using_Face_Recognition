"""Tests for logging_config.py and app.py's request-id/structured-logging
hooks (see README "Structured Logging")."""
import json
import logging

import logging_config


class TestJsonFormatter:
    def test_formats_record_as_valid_json_with_expected_fields(self):
        formatter = logging_config.JsonFormatter()
        record = logging.LogRecord(
            name='attendance_app', level=logging.INFO, pathname=__file__, lineno=1,
            msg='hello %s', args=('world',), exc_info=None,
        )
        line = formatter.format(record)
        payload = json.loads(line)  # raises if not valid JSON
        assert payload['message'] == 'hello world'
        assert payload['level'] == 'INFO'
        assert payload['logger'] == 'attendance_app'
        assert 'timestamp' in payload

    def test_includes_request_id_when_present_on_record(self):
        formatter = logging_config.JsonFormatter()
        record = logging.LogRecord(
            name='attendance_app', level=logging.INFO, pathname=__file__, lineno=1,
            msg='hi', args=(), exc_info=None,
        )
        record.request_id = 'abc123'
        payload = json.loads(formatter.format(record))
        assert payload['request_id'] == 'abc123'

    def test_omits_request_id_when_absent(self):
        formatter = logging_config.JsonFormatter()
        record = logging.LogRecord(
            name='attendance_app', level=logging.INFO, pathname=__file__, lineno=1,
            msg='hi', args=(), exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert 'request_id' not in payload

    def test_includes_exception_traceback_when_present(self):
        formatter = logging_config.JsonFormatter()
        try:
            raise ValueError('boom')
        except ValueError:
            import sys
            record = logging.LogRecord(
                name='attendance_app', level=logging.ERROR, pathname=__file__, lineno=1,
                msg='failed', args=(), exc_info=sys.exc_info(),
            )
        payload = json.loads(formatter.format(record))
        assert 'exception' in payload
        assert 'ValueError' in payload['exception']
        assert 'boom' in payload['exception']

    def test_surfaces_extra_fields_passed_via_logger_extra(self):
        formatter = logging_config.JsonFormatter()
        record = logging.LogRecord(
            name='attendance_app', level=logging.INFO, pathname=__file__, lineno=1,
            msg='request handled', args=(), exc_info=None,
        )
        record.http_status = 200
        record.duration_ms = 12.5
        payload = json.loads(formatter.format(record))
        assert payload['http_status'] == 200
        assert payload['duration_ms'] == 12.5

    def test_never_produces_invalid_json_for_non_serializable_extra(self):
        formatter = logging_config.JsonFormatter()
        record = logging.LogRecord(
            name='attendance_app', level=logging.INFO, pathname=__file__, lineno=1,
            msg='weird extra', args=(), exc_info=None,
        )
        record.weird = object()  # not JSON-serializable
        line = formatter.format(record)
        json.loads(line)  # must not raise


class TestRequestIdInApp:
    def test_response_includes_request_id_header(self, client):
        resp = client.get('/')
        assert 'X-Request-ID' in resp.headers
        assert len(resp.headers['X-Request-ID']) > 0

    def test_each_request_gets_a_distinct_request_id(self, client):
        resp1 = client.get('/')
        resp2 = client.get('/')
        assert resp1.headers['X-Request-ID'] != resp2.headers['X-Request-ID']

    def test_honors_inbound_request_id_header(self, client):
        resp = client.get('/', headers={'X-Request-ID': 'my-custom-trace-id'})
        assert resp.headers['X-Request-ID'] == 'my-custom-trace-id'

    def test_oversized_inbound_request_id_is_replaced(self, client):
        huge = 'x' * 500
        resp = client.get('/', headers={'X-Request-ID': huge})
        assert resp.headers['X-Request-ID'] != huge
        assert len(resp.headers['X-Request-ID']) < 200


class TestGetRequestId:
    def test_returns_none_outside_request_context(self):
        assert logging_config.get_request_id() is None

    def test_returns_the_current_request_id_inside_a_request(self, client, monkeypatch):
        # app.py logs a structured access-log line with the request_id via
        # the RequestIdFilter — assert the plumbing works end-to-end by
        # capturing a log record emitted during a real request.
        captured = []

        class _CaptureHandler(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _CaptureHandler()
        handler.addFilter(logging_config.RequestIdFilter())
        logger = logging.getLogger('attendance_app')
        logger.addHandler(handler)
        try:
            resp = client.get('/')
        finally:
            logger.removeHandler(handler)

        access_log_records = [r for r in captured if getattr(r, 'event', None) == 'http_request']
        assert access_log_records, 'expected at least one http_request access-log record'
        assert access_log_records[-1].request_id == resp.headers['X-Request-ID']
