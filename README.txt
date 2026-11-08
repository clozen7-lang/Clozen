CLOZEN - Phase 1 + profile and call UI starter

Install:
  python -m venv .venv
  Windows: .venv\\Scripts\\activate
  macOS/Linux: source .venv/bin/activate
  pip install -r requirements.txt
  python app.py

Open http://127.0.0.1:5000

Profile: open Profile > Edit profile to set bio, profile picture and cover photo.
On another user's profile, use Message, Voice call or Video call.
Calls use WebRTC peer-to-peer media and Socket.IO signalling. Both users must be
online, use a browser with microphone/camera permission, and be on localhost or
HTTPS. For use across the public internet, configure TURN servers and deploy
behind HTTPS; the included public STUN server alone is not sufficient for every
network. Browser-to-browser calling is a starter implementation, not a
production-grade calling service.

Real-time chat is delivered over Socket.IO. SQLite stores message history.
Set CLOZEN_SECRET to a long random secret before deployment. Debug mode is for
local development only. Add CSRF protection, rate limits, media scanning,
authorization hardening, HTTPS and TURN before public deployment.


Navigation includes separate Chat and Notifications sections. Chat shows conversations; notification center stores likes, comments, shares, follows, incoming messages and call alerts. Suggested people are shown separately within Notifications.
