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

// Lightweight, self-hosted device fingerprint -- NOT a commercial-grade
// fingerprinting library. Hashes a handful of stable browser/screen/
// timezone signals into a short hex string, purely as a heuristic signal
// for suspicious-device detection (see app.py's _check_suspicious_device()).
// Best-effort: if anything here throws (a locked-down browser, etc.) this
// just returns null, and the server-side check is skipped for that login.
function getDeviceFingerprint() {
    try {
        const parts = [
            navigator.userAgent || '',
            navigator.language || '',
            String(screen.width) + 'x' + String(screen.height),
            String(screen.colorDepth || ''),
            Intl.DateTimeFormat().resolvedOptions().timeZone || '',
            String(navigator.hardwareConcurrency || ''),
        ].join('||');
        // Simple djb2 string hash -- fine for a coarse device signal, not
        // meant to be cryptographically unique or collision-proof.
        let hash = 5381;
        for (let i = 0; i < parts.length; i++) {
            hash = ((hash << 5) + hash + parts.charCodeAt(i)) | 0;
        }
        return 'fp_' + (hash >>> 0).toString(16);
    } catch (e) {
        return null;
    }
}
