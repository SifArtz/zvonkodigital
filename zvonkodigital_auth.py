"""OAuth login helper for account.zvonkodigital.com.

This script performs the PKCE OAuth flow used by https://account.zvonkodigital.com/
by mimicking the browser steps:
1. Generate a code verifier and challenge.
2. Fetch the login page via the authorization endpoint to obtain the CSRF token.
3. Submit the username/password form with the CSRF token.
4. Follow redirects to capture the authorization code from the redirect URI.
5. Exchange the code for access/refresh tokens.

Example:
    python zvonkodigital_auth.py --username USER --password PASS

The script prints the token payload as JSON on success.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
from typing import Dict, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

CLIENT_ID = "75mwixlHmTIbzvREyUQt3Sk29lwpQfIw9bU948wJ"
AUTH_BASE = "https://auth.zvonkodigital.ru"
REDIRECT_URI = "https://account.zvonkodigital.com/account/oauth-login"
AUTH_PATH = "/o/authorize/"
TOKEN_PATH = "/o/token/"

# Character set copied from the production JS bundle to mirror browser behavior.
CODE_VERIFIER_CHARSET = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"


def generate_code_verifier(length: int = 64) -> str:
    """Generate a random PKCE code verifier."""

    return "".join(secrets.choice(CODE_VERIFIER_CHARSET) for _ in range(length))


def create_code_challenge(code_verifier: str) -> str:
    """Create an S256 PKCE code challenge from the verifier."""

    digest = hashlib.sha256(code_verifier.encode()).digest()
    encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return encoded


def build_authorize_url(code_challenge: str) -> str:
    """Construct the OAuth authorize URL with PKCE parameters."""

    params = urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
        }
    )
    return f"{AUTH_BASE}{AUTH_PATH}?{params}"


def extract_csrf_token(html: str) -> Optional[str]:
    """Parse the login page HTML to extract the CSRF token."""

    soup = BeautifulSoup(html, "html.parser")
    token_field = soup.find("input", {"name": "csrfmiddlewaretoken"})
    return token_field["value"] if token_field else None


def perform_login(session: requests.Session, auth_url: str, username: str, password: str) -> requests.Response:
    """Submit the login form and return the final response after redirects."""

    login_page = session.get(auth_url)
    csrf_token = extract_csrf_token(login_page.text)
    if not csrf_token:
        raise RuntimeError("CSRF token not found on login page")

    soup = BeautifulSoup(login_page.text, "html.parser")
    form = soup.find("form")
    if not form or not form.get("action"):
        raise RuntimeError("Unable to locate login form")

    action_url = urljoin(AUTH_BASE, form["action"])
    next_value = form.find("input", {"name": "next"})
    payload = {
        "csrfmiddlewaretoken": csrf_token,
        "username": username,
        "password": password,
        "next": next_value["value"] if next_value else "",
    }

    headers = {"Referer": auth_url}
    response = session.post(action_url, data=payload, headers=headers, allow_redirects=True)
    return response


def extract_authorization_code(final_response: requests.Response) -> str:
    """Extract the OAuth authorization code from the final redirect URL."""

    parsed = urlparse(final_response.url)
    query = parse_qs(parsed.query)
    code = query.get("code", [None])[0]
    if not code:
        raise RuntimeError("Authorization code not found; check credentials")
    return code


def exchange_code_for_tokens(session: requests.Session, code: str, code_verifier: str) -> Dict:
    """Exchange an authorization code for OAuth tokens."""

    data = {
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    token_url = urljoin(AUTH_BASE, TOKEN_PATH)
    response = session.post(token_url, data=data)
    response.raise_for_status()
    return response.json()


def authenticate(username: str, password: str) -> Dict:
    """Perform the full OAuth login flow and return token JSON."""

    session = requests.Session()
    code_verifier = generate_code_verifier()
    code_challenge = create_code_challenge(code_verifier)
    auth_url = build_authorize_url(code_challenge)

    final_response = perform_login(session, auth_url, username, password)
    authorization_code = extract_authorization_code(final_response)
    tokens = exchange_code_for_tokens(session, authorization_code, code_verifier)
    return tokens


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Authenticate against account.zvonkodigital.com")
    parser.add_argument("--username", required=True, help="Account username")
    parser.add_argument("--password", required=True, help="Account password")
    args = parser.parse_args(argv)

    try:
        tokens = authenticate(args.username, args.password)
    except Exception as exc:  # noqa: BLE001 - provide clear error to user
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(tokens, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
