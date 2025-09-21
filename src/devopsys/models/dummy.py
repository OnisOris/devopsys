from .base import Model

TEMPLATE = '''# Generated (dummy backend):

Request:
{request}

Response (template):
- This is a deterministic placeholder used in tests.
- Replace backend with 'ollama' for real LLM generation.
'''

class DummyModel(Model):
    async def acomplete(self, prompt: str) -> str:
        return TEMPLATE.format(request=prompt)
