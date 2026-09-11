"""Tests for multi-language UI support (see get_locale()/set_language()
in app.py, config.py's LANGUAGES/BABEL_DEFAULT_LOCALE, and
translations/ for the actual .po/.mo catalogs).
"""


class TestLocaleResolution:
    def test_defaults_to_english(self, client, isolated_paths):
        resp = client.get('/')
        assert 'lang="en"'.encode() in resp.data
        assert b'Face Attendance' in resp.data

    def test_accept_language_header_picks_a_supported_language(self, client, isolated_paths):
        resp = client.get('/', headers={'Accept-Language': 'es'})
        assert 'lang="es"'.encode() in resp.data
        assert 'Asistencia Facial'.encode() in resp.data

    def test_accept_language_header_falls_back_to_default_when_unsupported(self, client, isolated_paths):
        resp = client.get('/', headers={'Accept-Language': 'fr'})
        assert 'lang="en"'.encode() in resp.data

    def test_set_language_persists_for_the_session(self, client, isolated_paths):
        client.get('/set-language/hi')
        resp = client.get('/')
        assert 'lang="hi"'.encode() in resp.data
        # Persists across a second request in the same session, without
        # needing to pass Accept-Language again.
        resp2 = client.get('/student/login')
        assert 'lang="hi"'.encode() in resp2.data

    def test_set_language_rejects_unsupported_code(self, client, isolated_paths):
        with client.session_transaction() as sess:
            sess['locale'] = None
        client.get('/set-language/xx')  # not in LANGUAGES
        resp = client.get('/')
        # Falls through to Accept-Language/default rather than storing
        # an unsupported code — the request here sends no
        # Accept-Language, so it lands on the default.
        assert 'lang="xx"'.encode() not in resp.data

    def test_set_language_redirects_back_to_referrer_on_same_host(self, client, isolated_paths):
        resp = client.get('/set-language/es', headers={'Referer': 'http://localhost/student/login'},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/student/login')

    def test_set_language_ignores_offsite_referrer(self, client, isolated_paths):
        resp = client.get('/set-language/es', headers={'Referer': 'http://evil.example.com/phish'},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert 'evil.example.com' not in resp.headers['Location']


class TestTranslatedContent:
    def test_login_page_renders_in_spanish(self, client, isolated_paths):
        client.get('/set-language/es')
        resp = client.get('/login')
        assert 'Acceso de administrador'.encode() in resp.data or 'Iniciar sesión'.encode() in resp.data

    def test_student_registration_form_renders_in_hindi(self, client, isolated_paths):
        client.get('/set-language/hi')
        resp = client.get('/student/register')
        assert 'छात्र चेहरा पंजीकरण'.encode() in resp.data

    def test_language_switcher_lists_all_configured_languages(self, client, isolated_paths):
        import config as cfg
        resp = client.get('/')
        for name in cfg.LANGUAGES.values():
            assert name.encode() in resp.data
