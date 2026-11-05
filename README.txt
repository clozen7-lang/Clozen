CLOZEN - Phase 1 starter

1. Install Python 3.11 or newer.
2. Open this folder in VS Code or PyCharm.
3. Open Terminal in the project folder.
4. Run:
   python -m venv .venv
   Windows: .venv\Scripts\activate
   macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   python app.py
5. Open http://127.0.0.1:5000

Included: registration/login, profile, text/photo posts, feed, likes, comments,
discover/search users, follow, and basic one-to-one text messaging.

Not included yet: production deployment, real-time chat, audio/video calls,
stories, short videos, jobs, group messaging, password reset, email verification,
CSRF protection, and robust media scanning. Do not deploy this starter publicly
until those production security tasks are completed. Set CLOZEN_SECRET to a long
random secret before deployment. Debug mode is for local development only.
