# j2 from 'macros.j2' import header
# {{ header("HTTP-Replay-Formen", "HTTP Replay Shapes") }}

# %% [markdown]
#
# The three HTTP shapes that historically break the replay transport
# (hoelzl/clm#681): an OpenAI-style JSON POST with an auth header, a
# streaming/chunked response with a non-trivial content type, and a
# redirect carrying an auth header. Every request goes to the local stub
# (`tests/e2e/http_replay_stub.py`); the committed cassette replays them
# without any server.

# %%
import os

import requests

BASE = os.environ.get("CLM_TEST_HTTP_BASE", "http://127.0.0.1:47113")

# %% [markdown]
#
# Shape 1: an LLM-style call over an OpenAI-compatible API — JSON body,
# bearer token, JSON response.

# %%
response = requests.post(
    f"{BASE}/v1/chat/completions",
    headers={"Authorization": "Bearer test-token-not-a-secret"},
    json={
        "model": "stub-model",
        "messages": [{"role": "user", "content": "Say hello."}],
    },
    timeout=30,
)
completion = response.json()
print(completion["choices"][0]["message"]["content"])

# %% [markdown]
#
# Shape 2: a streaming, chunked `text/event-stream` response. The body is
# assembled before printing so the output never depends on chunk
# boundaries — the transport may deliver the same bytes in different
# slices on record and replay.

# %%
with requests.get(f"{BASE}/stream", stream=True, timeout=30) as stream_response:
    content_type = stream_response.headers.get("Content-Type", "")
    body = b"".join(stream_response.iter_content(chunk_size=None))
events = [
    line[len("data: ") :] for line in body.decode("utf-8").splitlines() if line.startswith("data: ")
]
print(f"{content_type}: {len(events)} events: {' '.join(events)}")

# %% [markdown]
#
# Shape 3: a redirect followed automatically, with an auth header on the
# initial request. The stub reports whether the header survived the hop.

# %%
final = requests.get(
    f"{BASE}/old-location",
    headers={"Authorization": "Bearer test-token-not-a-secret"},
    timeout=30,
)
payload = final.json()
print(final.status_code, len(final.history), payload["location"], payload["auth_seen"])
