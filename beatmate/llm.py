"""Optional Responses API planner; never used by Mock mode."""
import json
import urllib.request
import urllib.error
from .model import BeatSpec, EditPlan, KEYS, fields
from .planner import checked_text


def schema(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


SPEC_SCHEMA = schema(dict(style=dict(type='string', enum=['boom_bap', 'trap']),
    bpm=dict(type='integer', minimum=40, maximum=220), bars=dict(type='integer', minimum=1, maximum=32),
    key=dict(type='string', enum=list(KEYS)), mode=dict(type='string', enum=['minor', 'major']),
    swing=dict(type='number', minimum=0, maximum=.45), seed=dict(type='integer', minimum=0, maximum=2**31-1)))
EDIT_SCHEMA = schema(dict(operation=dict(type='string', enum=['velocity', 'transpose', 'density', 'mute']), value=dict(type='integer')))


class PlannerError(Exception):
    def __init__(self, message, *, code='invalid_response'):
        super().__init__(message)
        self.code = code


def strict_json(text):
    """Reject non-JSON constants and duplicate keys instead of repairing output."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('Non-JSON constant')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


class OpenAIPlanner:
    provider = 'openai'

    def parse_creative(self, text, reference=None):
        from .creative import llm_creative
        return llm_creative(self, text, reference)

    def __init__(self, api_key, model, transport=None):
        if not api_key or not model:
            raise ValueError('OPENAI_API_KEY and BEATMATE_MODEL are required for openai mode')
        self.api_key, self.model = api_key, model
        self.transport = transport or self._request

    def _request(self, payload):
        request = urllib.request.Request('https://api.openai.com/v1/responses',
            data=json.dumps(payload).encode(), headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise PlannerError('LLM request failed; no project changes committed') from error

    def _payload(self, text, instructions, output_schema):
        return dict(model=self.model, store=False, max_output_tokens=2000,
                    instructions=instructions, input=text,
                    text=dict(format=dict(type='json_schema', name='beatmate_plan', strict=True, schema=output_schema)))

    def _complete(self, text, instructions, output_schema):
        checked_text(text)
        payload = self._payload(text, instructions, output_schema)
        try:
            response = self.transport(payload)
            if response.get('status') != 'completed' or response.get('error') or response.get('incomplete_details'):
                details = response.get('incomplete_details')
                reason = details.get('reason') if isinstance(details, dict) else None
                if reason in ('max_output_tokens', 'content_filter'):
                    raise PlannerError(f'LLM response incomplete ({reason})')
                raise PlannerError('LLM response incomplete')
            output = response.get('output', [])
            if any(item.get('type') not in ('message', 'reasoning') for item in output):
                raise PlannerError('Unexpected LLM output item; tools are not allowed')
            messages = [item for item in output if item.get('type') == 'message']
            if len(messages) != 1 or messages[0].get('status', 'completed') != 'completed':
                raise PlannerError('LLM returned no single completed message')
            content = messages[0]['content']
            if any(c.get('type') == 'refusal' for c in content):
                raise PlannerError('LLM refused this request')
            if any(c.get('type') != 'output_text' for c in content):
                raise PlannerError('Unexpected LLM message content')
            texts = [c['text'] for c in content if c.get('type') == 'output_text']
            if len(texts) != 1:
                raise PlannerError('LLM returned no single structured plan')
            return strict_json(texts[0])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise PlannerError('Malformed LLM response') from None

    def parse_intent(self, text):
        result = self._complete(text,
            'Translate the user beat request into BeatSpec. 4/4 only. Use defaults when unspecified: '
            'boom_bap,90 BPM,8 bars,C minor,swing .12,seed 0; trap defaults 140 BPM,swing 0. '
            'No audio generation, tools, code or file operations.', SPEC_SCHEMA)
        try:
            fields(result, SPEC_SCHEMA['properties'], SPEC_SCHEMA['required'])
            spec = BeatSpec.parse(result)
        except (TypeError, ValueError):
            raise PlannerError('Invalid LLM BeatSpec') from None
        return dict(spec=spec.to_dict(), planner=f'{self.provider}:{self.model}', assumptions=['未指定参数由模型补全；生成前可通过 /parse 检查最终 BeatSpec。'])

    def plan_edit(self, text, track_id, start_bar, end_bar):
        # Caller-selected scope is never entrusted to the LLM.
        EditPlan(track_id, start_bar, end_bar, 'mute', 0)
        result = self._complete(text,
            f'Plan one edit for track {track_id}, bars {start_bar}..{end_bar}. '
            'velocity is an additive delta -126..126; transpose is semitones -24..24, melodic tracks only; '
            'density is 8/16/32 hits per bar for hihat only; mute value=0. Output operation/value only.', EDIT_SCHEMA)
        try:
            fields(result, ['operation', 'value'], ['operation', 'value'])
            return EditPlan(track_id, start_bar, end_bar, **result).to_dict()
        except (TypeError, ValueError):
            raise PlannerError('Invalid LLM edit plan') from None
