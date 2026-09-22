"""Small runtime selector; only the selected provider reads its configuration."""
import os
from .planner import MockPlanner


def create_planner(provider=None, *, environ=None):
    env = os.environ if environ is None else environ
    selected = provider if provider is not None else env.get('BEATMATE_PLANNER', 'mock')
    if selected == 'mock':
        return MockPlanner()
    if selected == 'deepseek':
        from .deepseek import DeepSeekPlanner
        return DeepSeekPlanner(environ=env)
    if selected == 'openai':
        from .llm import OpenAIPlanner
        return OpenAIPlanner(env.get('OPENAI_API_KEY'), env.get('BEATMATE_MODEL'))
    raise ValueError('BEATMATE_PLANNER must be mock, deepseek or openai')
