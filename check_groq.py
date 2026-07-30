"""
check_groq.py
-------------
Standalone test for your Groq API key -- isolates auth problems from
the rest of the app.  Set GROQ_API_KEY env var and run:

    python check_groq.py

A 200 response with a short reply means the key is good.
Free-tier limits: ~30 req/min, ~1000 req/day.
"""

import os
import requests

API_KEY = os.environ.get("GROQ_API_KEY", "")

if not API_KEY:
    print("ERROR: Set GROQ_API_KEY env var first.")
    print("  export GROQ_API_KEY=gsk_...")
    exit(1)

resp = requests.post(
    "https://api.groq.com/openai/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": "Reply with just the word OK."}],
        "max_tokens": 5,
    },
    timeout=30,
)

print("Status:", resp.status_code)
print("Body:", resp.text[:1000])
