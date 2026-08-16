export const PROTOCOL_VERSION = 1 as const;
export const PI_IMPLEMENTATION_VERSION = '0.84.2';

export type JsonObject = Record<string, unknown>;

export type ProtocolEnvelope = {
  version: typeof PROTOCOL_VERSION;
  run_id: string;
  correlation_id: string | null;
  type: string;
  payload: JsonObject;
};

export class ProtocolError extends Error {}

export function parseEnvelope(line: string): ProtocolEnvelope {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new ProtocolError('malformed JSON frame');
  }
  if (!isObject(value)) throw new ProtocolError('protocol frame must be an object');
  if (value.version !== PROTOCOL_VERSION) throw new ProtocolError('unsupported protocol version');
  if (typeof value.run_id !== 'string') throw new ProtocolError('run_id must be a string');
  if (value.correlation_id !== null && typeof value.correlation_id !== 'string') {
    throw new ProtocolError('correlation_id must be a string or null');
  }
  if (typeof value.type !== 'string' || !value.type) throw new ProtocolError('message type must be a non-empty string');
  if (!isObject(value.payload)) throw new ProtocolError('payload must be an object');
  return value as ProtocolEnvelope;
}

export function envelope(
  type: string,
  runId: string,
  payload: JsonObject = {},
  correlationId: string | null = null,
): ProtocolEnvelope {
  return {
    version: PROTOCOL_VERSION,
    run_id: runId,
    correlation_id: correlationId,
    type,
    payload,
  };
}

export function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
