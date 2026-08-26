"""Tests for the self-hosted image CAPTCHA."""
import config as cfg


class TestCaptchaImage:
    def test_captcha_image_endpoint_returns_png(self, client):
        resp = client.get('/captcha-image')
        assert resp.status_code == 200
        assert resp.content_type == 'image/png'
        assert resp.data[:8] == b'\x89PNG\r\n\x1a\n'  # PNG file signature

    def test_captcha_answer_is_stored_in_session(self, client):
        client.get('/captcha-image')
        with client.session_transaction() as sess:
            assert 'captcha_answer' in sess
            assert len(sess['captcha_answer']) == cfg.CAPTCHA_LENGTH


class TestCaptchaEnforcement:
    def test_correct_captcha_is_accepted(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'CAPTCHA_ENABLED', True)
        client.get('/captcha-image')
        with client.session_transaction() as sess:
            answer = sess['captcha_answer']

        resp = client.post('/login', data={
            'username': 'admin', 'password': cfg.DEFAULT_ADMIN_PASSWORD, 'captcha': answer,
        })
        assert b'Incorrect CAPTCHA' not in resp.data

    def test_wrong_captcha_is_rejected(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'CAPTCHA_ENABLED', True)
        client.get('/captcha-image')

        resp = client.post('/login', data={
            'username': 'admin', 'password': cfg.DEFAULT_ADMIN_PASSWORD, 'captcha': 'WRONGCODE',
        })
        assert b'Incorrect CAPTCHA' in resp.data

    def test_captcha_answer_is_single_use(self, client, isolated_paths, monkeypatch):
        """Solving the CAPTCHA once must not let it be reused for a second
        request."""
        monkeypatch.setattr(cfg, 'CAPTCHA_ENABLED', True)
        client.get('/captcha-image')
        with client.session_transaction() as sess:
            answer = sess['captcha_answer']

        client.post('/login', data={
            'username': 'admin', 'password': 'wrong-password-first-try', 'captcha': answer,
        })
        # Second attempt reusing the same (now-consumed) answer must fail
        # on CAPTCHA grounds, even though the code string was correct once.
        resp = client.post('/login', data={
            'username': 'admin', 'password': cfg.DEFAULT_ADMIN_PASSWORD, 'captcha': answer,
        })
        assert b'Incorrect CAPTCHA' in resp.data

    def test_disabled_captcha_is_not_enforced(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'CAPTCHA_ENABLED', False)
        resp = client.post('/login', data={
            'username': 'admin', 'password': cfg.DEFAULT_ADMIN_PASSWORD,
        })
        assert b'Incorrect CAPTCHA' not in resp.data

    def test_case_insensitive_match(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'CAPTCHA_ENABLED', True)
        client.get('/captcha-image')
        with client.session_transaction() as sess:
            answer = sess['captcha_answer']

        resp = client.post('/login', data={
            'username': 'admin', 'password': cfg.DEFAULT_ADMIN_PASSWORD, 'captcha': answer.lower(),
        })
        assert b'Incorrect CAPTCHA' not in resp.data
