# llm_client.py
import os
from typing import List, Dict
from huggingface_hub import InferenceClient

HF_TOKEN = os.getenv("HF_TOKEN")
HF_MODEL = os.getenv("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")

_client = InferenceClient(
    provider="together",               # buen costo/latencia
    token=HF_TOKEN
)

def chat_completion(messages: List[Dict], temperature: float = 0.4, max_tokens: int = 512) -> str:
    """
    Llama a la API de Chat Completions (OpenAI-like) en HF.
    messages: [{"role":"system"/"user"/"assistant", "content":"..."}]
    """
    resp = _client.chat_completions.create(
        model=HF_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()
