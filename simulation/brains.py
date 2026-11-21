"""
Agent brains: what decides an agent's policy at each review.

Every review has the same shape. The engine builds an observation (what the
agent can see), a baseline proposal from a deterministic, seeded policy, and a
field spec (the knobs the agent may set, with hard bounds). A brain returns
values for those fields plus a rationale:

- ``HeuristicBrain`` accepts the proposal (offline, reproducible, free);
- ``ClaudeBrain`` asks a small Claude model (Claude Haiku 5.5 by default) for a
  structured-output decision, then clamps every value into its bounds. Any
  API error, refusal, truncation or malformed answer falls back to the
  proposal and says why, so a missing key or an outage never stops the
  economy — and nothing the model says reaches the ledger except through
  bounded numbers the engine executes with the ERP's own services.
"""
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

# $ per million input / output tokens (Claude API list prices; prompts under 100K tokens).
PRICES_PER_MTOK = {
    'claude-haiku-5-5': (Decimal('0.10'), Decimal('0.50')),
    'claude-sonnet-5-5': (Decimal('2.00'), Decimal('10.00')),
    'claude-opus-5-5': (Decimal('4.00'), Decimal('20.00')),
    'claude-fable-5-2': (Decimal('8.00'), Decimal('40.00')),
}

ROLE_BRIEFS = {
    'market': 'You personify aggregate market forces: demand sentiment and the price level competitors set. '
              'You are not on the company\'s side; move the market the way a plausible economy would.',
    'customer': 'You buy the company\'s products for your own business. You reward reliable delivery and fair '
                'prices and you pay invoices around their terms, later when your own cash is tight.',
    'supplier': 'You sell raw materials to the company. You want margin and volume, and you remember whether '
                'the company pays you on time.',
    'sales_manager': 'You set list prices to grow gross margin without losing customers to competitors, '
                     'rationing scarce capacity with price when the order book outruns production.',
    'buyer': 'You set the safety-stock policy that the MRP engine buys and builds to, trading stock-outs '
             'against cash tied up in inventory.',
    'planner': 'You choose how production jobs are sequenced on the lines.',
    'maintenance': 'You decide how worn a line may get before preventive maintenance, trading planned '
                   'service cost against breakdowns, repair cost and lost capacity.',
    'plant_manager': 'You decide shifts per production line and, when authorised, buying new lines, '
                     'balancing labor and depreciation cost against capacity and lost sales.',
    'controller': 'You keep the books honest: you set the overhead absorption rate per line (dollars per '
                  'machine hour), the cash buffer that triggers revolver borrowing, and you write the period '
                  'commentary explaining what drove the results.',
}


# --- Field specs ----------------------------------------------------------------------------------

def number(lo, hi, description):
    return {'type': 'number', 'min': lo, 'max': hi, 'description': description}


def number_map(keys, lo, hi, description):
    return {'type': 'map', 'keys': list(keys), 'min': lo, 'max': hi, 'description': description}


def choice(choices, description):
    return {'type': 'enum', 'choices': list(choices), 'description': description}


def text(description, max_length=600):
    return {'type': 'text', 'max_length': max_length, 'description': description}


def json_schema(fields):
    """The structured-output schema for a field spec (bounds go in descriptions; clamping enforces them)."""
    properties = {}
    for name, spec in fields.items():
        if spec['type'] == 'number':
            properties[name] = {'type': 'number',
                                'description': f'{spec["description"]} Between {spec["min"]} and {spec["max"]}.'}
        elif spec['type'] == 'map':
            properties[name] = {
                'type': 'object',
                'description': f'{spec["description"]} Each value between {spec["min"]} and {spec["max"]}.',
                'properties': {key: {'type': 'number'} for key in spec['keys']},
                'required': list(spec['keys']),
                'additionalProperties': False,
            }
        elif spec['type'] == 'enum':
            properties[name] = {'type': 'string', 'enum': spec['choices'], 'description': spec['description']}
        else:
            properties[name] = {'type': 'string', 'description': spec['description']}
    properties['rationale'] = {
        'type': 'string', 'description': 'One or two sentences citing the observed numbers behind the decision.',
    }
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def coerce(fields, raw, fallback):
    """
    Validate a brain's answer against the field spec. Returns (values, notes):
    numbers are clamped into bounds, unknown choices and missing fields fall
    back to the proposal, and every correction is noted.
    """
    values, notes = {}, []
    if not isinstance(raw, dict):
        return dict(fallback), ['answer was not an object; kept the baseline proposal']
    for name, spec in fields.items():
        given = raw.get(name)
        if spec['type'] == 'number':
            if isinstance(given, bool) or not isinstance(given, (int, float)):
                values[name] = fallback[name]
                notes.append(f'{name} missing or not a number; kept {fallback[name]}')
                continue
            clamped = _clamp(float(given), spec['min'], spec['max'])
            if clamped != given:
                notes.append(f'{name} {given} clamped to {clamped}')
            values[name] = clamped
        elif spec['type'] == 'map':
            given = given if isinstance(given, dict) else {}
            out = {}
            for key in spec['keys']:
                item = given.get(key)
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    out[key] = fallback[name].get(key, 0)
                    notes.append(f'{name}.{key} missing; kept {out[key]}')
                    continue
                out[key] = _clamp(float(item), spec['min'], spec['max'])
                if out[key] != item:
                    notes.append(f'{name}.{key} {item} clamped to {out[key]}')
            values[name] = out
        elif spec['type'] == 'enum':
            if given in spec['choices']:
                values[name] = given
            else:
                values[name] = fallback[name]
                notes.append(f'{name} {given!r} is not one of {spec["choices"]}; kept {fallback[name]!r}')
        else:
            values[name] = (given if isinstance(given, str) and given.strip() else fallback.get(name, ''))[
                :spec.get('max_length', 600)]
    return values, notes


@dataclass
class Decision:
    values: dict
    rationale: str
    driver: str = 'heuristic'
    model: str = ''
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal('0')
    error: str = ''
    notes: list = field(default_factory=list)


class HeuristicBrain:
    """The deterministic policy: accept the engine's seeded proposal as is."""

    driver = 'heuristic'

    def review(self, *, agent, observation, proposal, fields, rationale):
        return Decision(values=dict(proposal), rationale=rationale, driver='heuristic')


HEURISTIC = HeuristicBrain()

# Models found unusable in this process (missing credentials, unknown model): skip the round trip.
_UNAVAILABLE = {}


def call_cost(model, input_tokens, output_tokens):
    price_in, price_out = PRICES_PER_MTOK.get(model, PRICES_PER_MTOK['claude-haiku-5-5'])
    return (Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out) / Decimal(1_000_000)


def default_client():
    import anthropic

    return anthropic.Anthropic(timeout=settings.SIMULATION_LLM_TIMEOUT, max_retries=1)


class ClaudeBrain:
    """
    A small Claude model as the agent's policy-maker, via the Messages API with
    structured outputs (`output_config.format`) at low effort. `client_factory`
    is injectable so tests run without a network or a key.
    """

    driver = 'llm'

    def __init__(self, model, client_factory=None):
        self.model = model
        self.client_factory = client_factory or default_client

    def _fallback(self, proposal, rationale, error):
        logger.info('ClaudeBrain fallback (%s): %s', self.model, error)
        return Decision(values=dict(proposal), rationale=rationale, driver='fallback', model=self.model,
                        error=error)

    def system_prompt(self, agent):
        return (
            f'You are {agent.name}, the {agent.get_role_display().lower()} agent of a manufacturing company '
            'simulated inside DjangoERP, an open-source double-entry ERP. '
            f'{ROLE_BRIEFS.get(agent.role, "")} '
            'At each review you receive a JSON observation of the business and a baseline proposal from a '
            'deterministic policy model. Decide the policy for the next period: keep the baseline or change it '
            'when the observation gives you a reason. Stay inside the bounds stated in each field description; '
            'values outside them are clamped. Everything you decide is executed by the ERP and posted to '
            'balanced books, so be economically sensible.'
        )

    def review(self, *, agent, observation, proposal, fields, rationale):
        import anthropic

        if self.model in _UNAVAILABLE:
            return self._fallback(proposal, rationale, _UNAVAILABLE[self.model])
        content = json.dumps({'observation': observation, 'baseline_proposal': proposal}, sort_keys=True,
                             default=str)
        try:
            client = self.client_factory()
            response = client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=self.system_prompt(agent),
                messages=[{'role': 'user', 'content': content}],
                output_config={'effort': 'low', 'format': {'type': 'json_schema', 'schema': json_schema(fields)}},
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            _UNAVAILABLE[self.model] = f'Claude unavailable: {exc.__class__.__name__}'
            return self._fallback(proposal, rationale, _UNAVAILABLE[self.model])
        except anthropic.AnthropicError as exc:
            # Rate limits, timeouts and 5xx: this review falls back, the next one retries.
            return self._fallback(proposal, rationale, f'Claude call failed: {exc.__class__.__name__}: {exc}'[:500])
        except TypeError as exc:
            # The SDK raises a bare TypeError at call time when no credential resolves (no ANTHROPIC_API_KEY,
            # auth token or `ant auth login` profile). Any other TypeError is a bug and propagates.
            if 'authentication' not in str(exc):
                raise
            _UNAVAILABLE[self.model] = 'Claude unavailable: no API credentials configured'
            return self._fallback(proposal, rationale, _UNAVAILABLE[self.model])

        usage = getattr(response, 'usage', None)
        input_tokens = getattr(usage, 'input_tokens', 0) or 0
        output_tokens = getattr(usage, 'output_tokens', 0) or 0
        cost = call_cost(self.model, input_tokens, output_tokens)

        def spent(decision):
            decision.input_tokens, decision.output_tokens, decision.cost = input_tokens, output_tokens, cost
            return decision

        if response.stop_reason in ('refusal', 'max_tokens'):
            return spent(self._fallback(proposal, rationale, f'Claude stopped with {response.stop_reason}'))
        text_block = next((b.text for b in response.content if getattr(b, 'type', '') == 'text'), None)
        try:
            raw = json.loads(text_block or '')
        except ValueError:
            return spent(self._fallback(proposal, rationale, 'Claude answer was not valid JSON'))
        values, notes = coerce(fields, raw, proposal)
        why = raw.get('rationale') if isinstance(raw, dict) and isinstance(raw.get('rationale'), str) else ''
        return spent(Decision(values=values, rationale=(why or rationale)[:1000], driver='llm', model=self.model,
                              notes=notes))
