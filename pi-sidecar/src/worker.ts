import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';

import { PiRunEngine, type RunEngine, type StartRunPayload } from './pi-engine.js';
import {
  envelope,
  isObject,
  parseEnvelope,
  PI_IMPLEMENTATION_VERSION,
  ProtocolError,
  type JsonObject,
  type ProtocolEnvelope,
} from './protocol.js';

type ActiveRun = {
  runId: string;
  controller: AbortController;
  engine: RunEngine;
  pendingValidation: Map<string, { resolve: (value: JsonObject) => void; reject: (error: Error) => void }>;
  terminalSent: boolean;
};

export class ProtocolWorker {
  private active: ActiveRun | null = null;
  private shuttingDown = false;

  constructor(
    private readonly send: (frame: ProtocolEnvelope) => void,
    private readonly createEngine: () => RunEngine = () => new PiRunEngine(),
  ) {}

  ready(): void {
    this.send(envelope('ready', '', { implementation_version: PI_IMPLEMENTATION_VERSION }));
  }

  get shouldExit(): boolean {
    return this.shuttingDown && this.active === null;
  }

  async receiveLine(line: string): Promise<void> {
    let frame: ProtocolEnvelope;
    try {
      frame = parseEnvelope(line);
    } catch (error) {
      this.protocolError('', error);
      return;
    }
    try {
      await this.receive(frame);
    } catch (error) {
      this.protocolError(frame.run_id, error);
    }
  }

  async receive(frame: ProtocolEnvelope): Promise<void> {
    switch (frame.type) {
      case 'health':
        this.send(envelope('health_result', frame.run_id, {
          ready: !this.shuttingDown,
          busy: this.active !== null,
          implementation_version: PI_IMPLEMENTATION_VERSION,
        }, frame.correlation_id));
        return;
      case 'start_run':
        await this.startRun(frame);
        return;
      case 'validator_result':
        this.resolveValidation(frame);
        return;
      case 'abort_run':
        await this.abortRun(frame.run_id);
        return;
      case 'shutdown':
        this.shuttingDown = true;
        if (this.active) await this.abortRun(this.active.runId);
        this.send(envelope('shutdown_ack', '', {}));
        return;
      default:
        throw new ProtocolError(`unexpected frame type: ${frame.type}`);
    }
  }

  private async startRun(frame: ProtocolEnvelope): Promise<void> {
    if (this.shuttingDown) throw new ProtocolError('worker is shutting down');
    if (this.active) {
      this.send(envelope('failed', frame.run_id, { reason: 'Pi worker is busy' }));
      return;
    }
    const payload = validateStartPayload(frame.payload);
    const active: ActiveRun = {
      runId: frame.run_id,
      controller: new AbortController(),
      engine: this.createEngine(),
      pendingValidation: new Map(),
      terminalSent: false,
    };
    this.active = active;
    this.send(envelope('run_started', frame.run_id, { implementation_version: PI_IMPLEMENTATION_VERSION }));
    void active.engine.run(payload, {
      event: (eventPayload) => this.send(envelope('event', active.runId, eventPayload)),
      validate: () => this.requestValidation(active),
    }, active.controller.signal).then(
      (result) => this.finish(active, 'complete', result as unknown as JsonObject),
      (error) => {
        const aborted = active.controller.signal.aborted;
        this.finish(active, aborted ? 'aborted' : 'failed', aborted ? {} : {
          reason: safeReason(error, payload.provider.api_key),
        });
      },
    );
  }

  private requestValidation(active: ActiveRun): Promise<JsonObject> {
    if (this.active !== active || active.controller.signal.aborted) {
      return Promise.reject(new Error('Agent Run aborted'));
    }
    const correlationId = randomUUID();
    this.send(envelope('validator_request', active.runId, {}, correlationId));
    return new Promise((resolve, reject) => {
      active.pendingValidation.set(correlationId, { resolve, reject });
    });
  }

  private resolveValidation(frame: ProtocolEnvelope): void {
    const active = this.active;
    if (!active || active.runId !== frame.run_id) throw new ProtocolError('validator result is for an inactive Run');
    if (!frame.correlation_id) throw new ProtocolError('validator result is missing a correlation ID');
    const pending = active.pendingValidation.get(frame.correlation_id);
    if (!pending) throw new ProtocolError('validator result correlation does not match');
    active.pendingValidation.delete(frame.correlation_id);
    pending.resolve(frame.payload);
  }

  private async abortRun(runId: string): Promise<void> {
    const active = this.active;
    if (!active || active.runId !== runId) throw new ProtocolError('abort is for an inactive Run');
    active.controller.abort();
    for (const pending of active.pendingValidation.values()) pending.reject(new Error('Agent Run aborted'));
    active.pendingValidation.clear();
    await active.engine.abort();
  }

  private finish(active: ActiveRun, type: 'complete' | 'failed' | 'aborted', payload: JsonObject): void {
    if (this.active !== active || active.terminalSent) {
      this.protocolError(active.runId, new ProtocolError('duplicate terminal frame'));
      return;
    }
    active.terminalSent = true;
    for (const pending of active.pendingValidation.values()) pending.reject(new Error('Agent Run finished'));
    active.pendingValidation.clear();
    this.active = null;
    this.send(envelope(type, active.runId, payload));
  }

  private protocolError(runId: string, error: unknown): void {
    this.send(envelope('protocol_error', runId, { reason: safeReason(error) }));
  }
}

function validateStartPayload(payload: JsonObject): StartRunPayload {
  const provider = payload.provider;
  if (
    typeof payload.prompt !== 'string' ||
    typeof payload.project_dir !== 'string' ||
    typeof payload.skill_root !== 'string' ||
    typeof payload.blocked_root !== 'string' ||
    typeof payload.system_prompt !== 'string' ||
    !isObject(provider) ||
    typeof provider.api_key !== 'string' ||
    typeof provider.model_id !== 'string' ||
    (provider.base_url !== null && typeof provider.base_url !== 'string')
  ) {
    throw new ProtocolError('start_run payload is invalid');
  }
  return payload as unknown as StartRunPayload;
}

function safeReason(error: unknown, apiKey?: string): string {
  let value = error instanceof Error ? error.message : String(error);
  if (apiKey) value = value.replaceAll(apiKey, '[REDACTED]');
  value = value.replace(/\b(bearer)\s+[A-Za-z0-9._~+/=-]+/gi, '$1 [REDACTED]');
  value = value.replace(/\b(?:sk|pk|ghp|github_pat|xox[baprs])-[A-Za-z0-9._~+/=-]{8,}/g, '[REDACTED]');
  return value.trim().split(/\r?\n/, 1)[0].slice(0, 500) || 'Pi worker error';
}

function sendStdout(frame: ProtocolEnvelope): void {
  process.stdout.write(`${JSON.stringify(frame)}\n`);
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  process.env.PI_OFFLINE = '1';
  process.env.PI_TELEMETRY = '0';
  const worker = new ProtocolWorker(sendStdout);
  worker.ready();
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  lines.on('line', (line) => void worker.receiveLine(line).then(() => {
    if (worker.shouldExit) process.exit(0);
  }));
  lines.on('close', () => process.exit(0));
}
