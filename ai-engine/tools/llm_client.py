import requests
import os

OLLAMA_URL = "http://host.docker.internal:11434"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")


def call_llm(prompt: str, model: str | None = None):
    url = f"{OLLAMA_URL}/api/generate"
    selected_model = model or OLLAMA_MODEL

    response = requests.post(
        url,
        json={
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
        },
        timeout=60,
    )

    if response.status_code != 200:
        raise Exception(f"LLM request failed: {response.text}")

    data = response.json()

    return data.get("response", "")
