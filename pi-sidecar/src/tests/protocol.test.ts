import test from 'node:test';
import assert from 'node:assert/strict';

import { testing } from '../pi-engine.js';
import { ProtocolWorker } from '../worker.js';
import type { EngineCallbacks, EngineResult, RunEngine, StartRunPayload } from '../pi-engine.js';
import type { JsonObject, ProtocolEnvelope } from '../protocol.js';

const startPayload: StartRunPayload = {
  prompt: 'make a part',
  project_dir: '/tmp/project',
  skill_root: '/tmp/skills',
  blocked_root: '/tmp/examples',
  system_prompt: 'system',
  provider: { api_key: 'secret', model_id: 'model', base_url: null },
};

test('Pi bash masks the blocked repository reference', () => {
  const command = testing.sandboxedCommand(
    'cat /repo/examples/model.py',
    '/tmp/project',
    '/repo/examples',
  );
  assert.match(command, /'--tmpfs' '\/repo\/examples'/);
});

class FakeEngine implements RunEngine {
  async run(_payload: StartRunPayload, callbacks: EngineCallbacks, _signal: AbortSignal): Promise<EngineResult> {
    callbacks.event({ event_type: 'tool_call', tool_name: 'write_todos', arguments: {} });
    await callbacks.validate();
    return {
      final_text: 'done',
      token_usage: { input_tokens: 1, cached_input_tokens: 0, output_tokens: 2, total_tokens: 3 },
    };
  }

  async abort(): Promise<void> {}
}

class BlockingEngine implements RunEngine {
  async run(_payload: StartRunPayload, _callbacks: EngineCallbacks, signal: AbortSignal): Promise<EngineResult> {
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    });
  }

  async abort(): Promise<void> {}
}

class CredentialLeakingEngine implements RunEngine {
  async run(payload: StartRunPayload): Promise<EngineResult> {
    throw new Error(`provider rejected ${payload.provider.api_key}`);
  }

  async abort(): Promise<void> {}
}

function frame(type: string, runId: string, payload: JsonObject = {}, correlationId: string | null = null): string {
  return JSON.stringify({ version: 1, run_id: runId, correlation_id: correlationId, type, payload });
}

async function waitFor<T>(fn: () => T | undefined): Promise<T> {
  const deadline = Date.now() + 500;
  while (Date.now() < deadline) {
    const value = fn();
    if (value !== undefined) return value;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error('timed out waiting for worker frame');
}

test('worker emits readiness, correlates validator requests, and completes once', async () => {
  const output: ProtocolEnvelope[] = [];
  const worker = new ProtocolWorker((value) => output.push(value), () => new FakeEngine());
  worker.ready();
  assert.equal(output[0]?.type, 'ready');

  await worker.receiveLine(frame('start_run', 'run-1', startPayload as unknown as JsonObject));
  const request = await waitFor(() => output.find((item) => item.type === 'validator_request'));
  assert.equal(request.run_id, 'run-1');
  assert.ok(request.correlation_id);

  await worker.receiveLine(frame('validator_result', 'run-1', { status: 'succeeded' }, request.correlation_id));
  const complete = await waitFor(() => output.find((item) => item.type === 'complete'));
  assert.equal(complete.payload.final_text, 'done');
  assert.equal(output.filter((item) => ['complete', 'failed', 'aborted'].includes(item.type)).length, 1);
});

test('worker rejects malformed, unsupported, and unexpected frames predictably', async () => {
  const output: ProtocolEnvelope[] = [];
  const worker = new ProtocolWorker((value) => output.push(value));
  await worker.receiveLine('{');
  await worker.receiveLine(JSON.stringify({ version: 99, run_id: '', correlation_id: null, type: 'health', payload: {} }));
  await worker.receiveLine(frame('unknown', '', {}));
  assert.deepEqual(output.map((item) => item.type), ['protocol_error', 'protocol_error', 'protocol_error']);
});

test('worker rejects a second start while the first run is active', async () => {
  const output: ProtocolEnvelope[] = [];
  const worker = new ProtocolWorker((value) => output.push(value), () => new FakeEngine());
  await worker.receiveLine(frame('start_run', 'run-1', startPayload as unknown as JsonObject));
  await worker.receiveLine(frame('start_run', 'run-2', startPayload as unknown as JsonObject));
  assert.equal(output.find((item) => item.run_id === 'run-2')?.type, 'failed');
});

test('worker rejects mismatched validation correlations and still accepts the matching result', async () => {
  const output: ProtocolEnvelope[] = [];
  const worker = new ProtocolWorker((value) => output.push(value), () => new FakeEngine());
  await worker.receiveLine(frame('start_run', 'run-1', startPayload as unknown as JsonObject));
  const request = await waitFor(() => output.find((item) => item.type === 'validator_request'));

  await worker.receiveLine(frame('validator_result', 'run-1', {}, 'wrong-correlation'));
  assert.match(String(output.at(-1)?.payload.reason), /correlation does not match/);

  await worker.receiveLine(frame('validator_result', 'run-1', {}, request.correlation_id));
  await waitFor(() => output.find((item) => item.type === 'complete'));
  assert.equal(output.filter((item) => item.type === 'complete').length, 1);
});

test('worker aborts an active run and emits one aborted terminal frame', async () => {
  const output: ProtocolEnvelope[] = [];
  const worker = new ProtocolWorker((value) => output.push(value), () => new BlockingEngine());
  await worker.receiveLine(frame('start_run', 'run-1', startPayload as unknown as JsonObject));
  await worker.receiveLine(frame('abort_run', 'run-1'));
  await waitFor(() => output.find((item) => item.type === 'aborted'));

  assert.equal(output.filter((item) => ['complete', 'failed', 'aborted'].includes(item.type)).length, 1);
});

test('worker reports health and acknowledges graceful shutdown while idle', async () => {
  const output: ProtocolEnvelope[] = [];
  const worker = new ProtocolWorker((value) => output.push(value));
  await worker.receiveLine(frame('health', '', {}, 'health-1'));
  await worker.receiveLine(frame('shutdown', ''));

  assert.deepEqual(output.map((item) => item.type), ['health_result', 'shutdown_ack']);
  assert.equal(output[0]?.correlation_id, 'health-1');
  assert.equal(worker.shouldExit, true);
});

test('worker removes the configured API key from bounded failure diagnostics', async () => {
  const output: ProtocolEnvelope[] = [];
  const worker = new ProtocolWorker((value) => output.push(value), () => new CredentialLeakingEngine());
  await worker.receiveLine(frame('start_run', 'run-1', startPayload as unknown as JsonObject));
  const failed = await waitFor(() => output.find((item) => item.type === 'failed'));

  assert.equal(failed.payload.reason, 'provider rejected [REDACTED]');
  assert.equal(JSON.stringify(output).includes('secret'), false);
});
