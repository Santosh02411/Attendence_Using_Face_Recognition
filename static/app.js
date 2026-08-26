// App.js - reserved for future use
// Camera initialization is handled by individual pages

// Shared CSRF helper: reads the token from the <meta name="csrf-token"> tag
// (rendered per-page via Flask-WTF's csrf_token()) and returns headers to
// attach to any same-origin fetch() POST/PUT/DELETE call.
function csrfHeaders(extra) {
    const token = document.querySelector('meta[name="csrf-token"]');
    const headers = Object.assign({}, extra || {});
    if (token) {
        headers['X-CSRFToken'] = token.getAttribute('content');
    }
    return headers;
}
