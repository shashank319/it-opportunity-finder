#!/usr/bin/env python3
"""
gmail_setup.py — run this ONCE on your own laptop to authorize read-only access
to the dedicated Gmail inbox. It prints a token blob you paste into the GitHub
secret GMAIL_TOKEN_JSON. No password is ever stored.

PREREQUISITES (see README for the click-by-click version):
  1. In Google Cloud Console, create a project, enable the "Gmail API", and
     create an OAuth client of type "Desktop app". Download its JSON.
  2. Save that file next to this script as  client_secret.json  (git-ignored).
  3. pip install google-auth-oauthlib google-api-python-client
  4. python scripts/gmail_setup.py

It opens a browser, you log in as the DEDICATED inbox and approve read-only
access, and it prints the token JSON. Copy the whole blob into the secret.
"""

import os
import sys

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET = os.path.join(HERE, "client_secret.json")


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit("Run:  pip install google-auth-oauthlib google-api-python-client")

    if not os.path.exists(CLIENT_SECRET):
        sys.exit(f"Missing {CLIENT_SECRET}. Download your OAuth 'Desktop app' "
                 f"client JSON and save it there (see README).")

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    # Opens a browser; falls back to a console prompt over SSH.
    creds = flow.run_local_server(port=0)

    token_json = creds.to_json()  # includes refresh_token + client id/secret

    print("\n" + "=" * 70)
    print("SUCCESS. Copy EVERYTHING between the lines into the GitHub secret")
    print("named  GMAIL_TOKEN_JSON  (Settings -> Secrets and variables -> Actions):")
    print("=" * 70)
    print(token_json)
    print("=" * 70)
    print("Tip: keep this secret. Delete any local token file when done.")


if __name__ == "__main__":
    main()
