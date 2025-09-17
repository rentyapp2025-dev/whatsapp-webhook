import os
from typing import List, Dict
from huggingface_hub import InferenceClient

HF_TOKEN = os.getenv("HF_TOKEN")
HF_MODEL = os.getenv("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")

_client = InferenceClient(model=HF_MODEL, token=HF_TOKEN)

def chat_completion(messages: List[Dict], temperature: float = 0.4, max_tokens: int = 512) -> str:
    """
    Llama al endpoint de Hugging Face con un estilo Chat.
    messages: [{"role":"system"/"user"/"assistant", "content":"..."}]
    """
    prompt = ""
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            prompt += f"[SYSTEM]\n{content}\n\n"
        elif role == "user":
            prompt += f"[USER]\n{content}\n\n"
        elif role == "assistant":
            prompt += f"[ASSISTANT]\n{content}\n\n"

    resp = _client.text_generation(
        prompt,
        temperature=temperature,
        max_new_tokens=max_tokens,
        stream=False,
        stop_sequences=["[USER]", "[SYSTEM]"]
    )

    return resp.strip()
