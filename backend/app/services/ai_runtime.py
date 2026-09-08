"""Adapter around Prompture drivers: isolated fallback and per-call accounting."""
import time

from app.services import ai_connections, ai_management


def managed_driver(driver, connection, model, recorder, max_cost, row):
    from prompture.drivers.base import Driver
    from prompture.exceptions import BudgetExceededError

    class ManagedDriver(Driver):
        def __init__(self):
            self.delegate = driver
            self.connection = connection
            self.model = model
            self.conversation = None
            self.tried = {(connection.id, model)}
            self._sync_flags()

        def _sync_flags(self):
            for name in ('supports_messages', 'supports_tool_use', 'supports_streaming',
                         'supports_streaming_tool_use', 'supports_vision', 'supports_json_mode', 'supports_json_schema'):
                setattr(self, name, getattr(self.delegate, name, False))

        def _switch(self, candidate, reason):
            target = ai_connections.get_connection(candidate['connection_id'])
            replacement = ai_connections.build_driver(target, candidate['model'])
            self.delegate, self.connection, self.model = replacement, target, candidate['model']
            self.tried.add((target.id, self.model))
            self._sync_flags()
            recorder.connection_id = target.id
            recorder.model = ai_connections.model_name(target, self.model)
            recorder.run.reason = reason
            recorder.switched = True
            row.connection_id, row.model_name = target.id, recorder.model
            if self.conversation is not None:
                self.conversation._model_name = recorder.model

        def _fallback(self, error):
            if (not recorder.config['fallback_enabled'] or recorder.output_started
                    or isinstance(error, (BudgetExceededError, ValueError, PermissionError))):
                return False
            for candidate in recorder.config['fallbacks']:
                key = candidate['connection_id'], candidate['model']
                if key in self.tried:
                    continue
                self.tried.add(key)
                try:
                    self._switch(candidate, 'Configured fallback after provider failure; no output or tools replayed')
                    return True
                except Exception:
                    # Failed construction is visible without leaking credentials.
                    recorder.attempts.append({**candidate, 'status': 'unavailable', 'cost_source': 'unknown',
                                              'cost': 0, 'total_tokens': 0, 'duration_ms': 0})
            return False

        def degrade(self):
            if recorder.config['budget_policy'] != 'degrade' or not max_cost:
                return
            if recorder.initial.get('cost', 0) < max_cost * 0.8:
                return
            rates = ai_management.model_info(self.connection, self.model)['pricing']
            if not rates:
                return
            for candidate in recorder.config['fallbacks']:
                target = ai_connections.get_connection(candidate['connection_id'])
                cheaper = ai_management.model_info(target, candidate['model'])['pricing']
                if cheaper and sum(cheaper.values()) < sum(rates.values()):
                    self._switch(candidate, 'Lower-cost model selected before the turn at 80% of the conversation allowance')
                    return

        def _options(self, options):
            # Keep SDK JSON/schema options, while enforcing administrator controls.
            return {**options, **ai_management.generation_options(recorder.config, self.connection, self.model)}

        def _meta(self, raw):
            from prompture.infra.tracker import UsageTracker
            import math
            raw = raw if isinstance(raw, dict) else {}
            meta = {}
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cached_prompt_tokens', 'cache_creation_tokens', 'cost'):
                value = raw.get(key, 0)
                meta[key] = value if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else 0
            if not meta['total_tokens']:
                meta['total_tokens'] = meta['prompt_tokens'] + meta['completion_tokens']
            pricing = ai_management.model_info(self.connection, self.model)['pricing']
            reported = raw.get('cost')
            if (raw.get('cost_source') == 'provider_reported' and isinstance(reported, (int, float))
                    and not isinstance(reported, bool) and math.isfinite(reported) and reported >= 0):
                meta['cost_source'] = 'provider_reported'
            elif pricing is not None:
                if not meta['cost']:
                    meta['cost'] = UsageTracker.calculate_cost(ai_connections.model_name(self.connection, self.model), meta['prompt_tokens'], meta['completion_tokens'])
                meta['cost_source'] = 'estimated'
            else:
                meta['cost_source'] = 'estimated' if meta['cost'] > 0 else 'unknown'
            return meta

        def _call(self, method, *args):
            while True:
                recorder.check(max_cost)
                started = time.monotonic()
                try:
                    response = getattr(self.delegate, method)(*args[:-1], self._options(args[-1]))
                except Exception as error:
                    recorder.record({}, (time.monotonic() - started) * 1000, error=True)
                    if self._fallback(error):
                        continue
                    raise
                response = {**response, 'meta': self._meta(response.get('meta', {}))}
                recorder.output_started = True
                recorder.record(response['meta'], (time.monotonic() - started) * 1000)
                return response

        def _stream(self, method, *args):
            while True:
                recorder.check(max_cost)
                started = time.monotonic()
                meta = {}
                try:
                    for event in getattr(self.delegate, method)(*args[:-1], self._options(args[-1])):
                        recorder.output_started = True  # conservative: never replay any delivered event
                        if isinstance(event, dict) and 'meta' in event:
                            meta = self._meta(event['meta'])
                            event = {**event, 'meta': meta}
                        elif getattr(event, 'event_type', '') == 'message_stop':
                            meta = self._meta(event.usage)
                            event.usage.update(meta)
                        yield event
                except Exception as error:
                    recorder.record(meta, (time.monotonic() - started) * 1000, error=True)
                    if self._fallback(error):
                        continue
                    raise
                except GeneratorExit:
                    recorder.record(meta, (time.monotonic() - started) * 1000, error=True)
                    raise
                else:
                    recorder.record(meta, (time.monotonic() - started) * 1000)
                return

        def generate(self, prompt, options):
            return self._call('generate', prompt, options)

        def generate_messages(self, messages, options):
            return self._call('generate_messages', messages, options)

        def generate_messages_with_tools(self, messages, tools, options):
            return self._call('generate_messages_with_tools', messages, tools, options)

        def generate_messages_stream(self, messages, options):
            yield from self._stream('generate_messages_stream', messages, options)

        def generate_messages_with_tools_stream(self, messages, tools, options):
            yield from self._stream('generate_messages_with_tools_stream', messages, tools, options)

    wrapped = ManagedDriver()
    wrapped.degrade()
    return wrapped


EXTRACTION_SCHEMA = {'type': 'object', 'properties': {
    'summary': {'type': 'string'}, 'facts': {'type': 'array', 'items': {'type': 'string'}},
    'warnings': {'type': 'array', 'items': {'type': 'string'}},
}, 'required': ['summary', 'facts', 'warnings'], 'additionalProperties': False}


def ask(conv, message, workflow='chat'):
    if workflow == 'extract':
        result = conv.ask_for_json(message, EXTRACTION_SCHEMA, ai_cleanup=False)
        return result['json_string']
    return conv.ask(message)


def ask_live(conv, message, workflow='chat'):
    if workflow == 'extract':
        from prompture.agents.live_events import TextDelta, TurnComplete
        yield TextDelta(text=ask(conv, message, workflow))
        yield TurnComplete(usage=conv.usage)
    else:
        yield from conv.ask_live(message)
