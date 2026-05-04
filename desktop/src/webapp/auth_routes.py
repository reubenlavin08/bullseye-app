"""Auth routes: /login, /logout, OAuth callback inbox.

These render the small login screen + the post-OAuth "you can close
this tab" page. The actual OAuth dance happens in
`deal_finder.auth.google_oauth` which spawns its own callback server
on localhost:53682 (separate from this Flask app's random port).

Why two HTTP servers: the OAuth callback URL must be a stable port
registered in Supabase's redirect_to allowlist. Our main Flask app
binds to a random port for security/multiple-launch tolerance. So
the OAuth server is a separate, narrow-purpose listener that runs
only during the login flow, then shuts down.
"""
from __future__ import annotations

# from flask import Blueprint, render_template, redirect
# bp = Blueprint('auth', __name__)

# @bp.route('/login')
# def login():
#     return render_template('login.html')

# @bp.route('/logout', methods=['POST'])
# def logout():
#     token_store.clear()
#     return redirect('/login')
