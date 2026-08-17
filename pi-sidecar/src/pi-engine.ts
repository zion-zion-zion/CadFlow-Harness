import { constants as fsConstants } from 'node:fs';
import { access, lstat, mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, relative, resolve } from 'node:path';

import {
  createAgentSession,
  createBashToolDefinition,
  createEditToolDefinition,
  createExtensionRuntime,
  createLocalBashOperations,
  createReadToolDefinition,
  createWriteToolDefinition,
  loadSkillsFromDir,
  ModelRuntime,
  type ResourceLoader,
  SessionManager,
  SettingsManager,
  type ToolDefinition,
} from '@earendil-works/pi-coding-agent';
import { InMemoryCredentialStore } from '@earendil-works/pi-ai';
import { Type } from 'typebox';

import type { JsonObject } from './protocol.js';

const SENSITIVE_ENV_NAME = /api[_-]?(?:key|token)|access[_-]?token|secret|password|credential|authorization|endpoint|base[_-]?url|openai|anthropic|gemini|langchain|langsmith|cohere|mistral|groq|azure/i;

export type StartRunPayload = {
  prompt: string;
  project_dir: string;
  skill_root: string;
  blocked_root: string;
  system_prompt: string;
  provider: {
    api_key: string;
    model_id: string;
    base_url: string | null;
  };
};

export type EngineCallbacks = {
  event: (payload: JsonObject) => void;
  validate: () => Promise<JsonObject>;
};

export type EngineResult = {
  final_text: string | null;
  token_usage: {
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
};

export interface RunEngine {
  run(payload: StartRunPayload, callbacks: EngineCallbacks, signal: AbortSignal): Promise<EngineResult>;
  abort(): Promise<void>;
}

type Todo = { content: string; status: 'pending' | 'in_progress' | 'completed' };

export class PiRunEngine implements RunEngine {
  private abortSession: (() => Promise<void>) | null = null;

  async abort(): Promise<void> {
    await this.abortSession?.();
  }

  async run(payload: StartRunPayload, callbacks: EngineCallbacks, signal: AbortSignal): Promise<EngineResult> {
    const projectDir = resolve(payload.project_dir);
    const skillRoot = resolve(payload.skill_root);
    const blockedRoot = resolve(payload.blocked_root);
    await assertDirectory(projectDir, 'Project');
    await assertDirectory(skillRoot, 'Skill');
    await assertDirectory(blockedRoot, 'blocked reference');

    const resourceLoader = explicitResourceLoader(skillRoot, payload.system_prompt);
    const modelRuntime = await ModelRuntime.create({
      credentials: new InMemoryCredentialStore(),
      modelsPath: null,
      allowModelNetwork: false,
      refreshOnCreate: false,
    });
    const providerId = 'cadflow-openai';
    modelRuntime.registerProvider(providerId, {
      name: 'CadFlow OpenAI-compatible provider',
      baseUrl: payload.provider.base_url ?? 'https://api.openai.com/v1',
      apiKey: payload.provider.api_key,
      api: 'openai-completions',
      models: [{
        id: payload.provider.model_id,
        name: payload.provider.model_id,
        reasoning: false,
        input: ['text'],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128_000,
        maxTokens: 16_384,
      }],
    });
    const model = modelRuntime.getModel(providerId, payload.provider.model_id);
    if (!model) throw new Error('configured Pi model could not be constructed');

    const customTools = await createRestrictedTools(projectDir, skillRoot, blockedRoot, callbacks);
    const settingsManager = SettingsManager.inMemory({
      compaction: { enabled: false },
      retry: { enabled: true, maxRetries: 2 },
      enableInstallTelemetry: false,
      enableAnalytics: false,
      packages: [],
      extensions: [],
      skills: [],
      prompts: [],
    }, { projectTrusted: false });
    const { session } = await createAgentSession({
      cwd: projectDir,
      agentDir: projectDir,
      modelRuntime,
      model,
      thinkingLevel: 'off',
      noTools: 'builtin',
      customTools,
      resourceLoader,
      sessionManager: SessionManager.inMemory(projectDir),
      settingsManager,
    });
    this.abortSession = () => session.abort();
    const unsubscribe = session.subscribe((event) => {
      if (event.type === 'turn_start') {
        callbacks.event({ event_type: 'model_request' });
      } else if (event.type === 'message_end' && event.message.role === 'assistant') {
        callbacks.event({ event_type: 'model_response', message: event.message as unknown as JsonObject });
      } else if (event.type === 'tool_execution_start') {
        callbacks.event({
          event_type: 'tool_call',
          tool_call_id: event.toolCallId,
          tool_name: event.toolName,
          arguments: event.args,
        });
      } else if (event.type === 'tool_execution_end') {
        callbacks.event({
          event_type: event.isError ? 'tool_error' : 'tool_result',
          tool_call_id: event.toolCallId,
          tool_name: event.toolName,
          result: event.result,
        });
      } else if (event.type === 'auto_retry_start') {
        callbacks.event({ event_type: 'provider_retry', attempt: event.attempt, error: event.errorMessage });
      }
    });

    const abortListener = () => void session.abort();
    signal.addEventListener('abort', abortListener, { once: true });
    try {
      if (signal.aborted) throw new Error('Agent Run aborted');
      await session.prompt(payload.prompt, { expandPromptTemplates: false });
      if (signal.aborted) throw new Error('Agent Run aborted');
      const stats = session.getSessionStats();
      return {
        final_text: session.getLastAssistantText() ?? null,
        token_usage: {
          input_tokens: stats.tokens.input + stats.tokens.cacheRead,
          cached_input_tokens: stats.tokens.cacheRead,
          output_tokens: stats.tokens.output,
          total_tokens: stats.tokens.input + stats.tokens.cacheRead + stats.tokens.output,
        },
      };
    } finally {
      signal.removeEventListener('abort', abortListener);
      unsubscribe();
      session.dispose();
      this.abortSession = null;
    }
  }
}

function explicitResourceLoader(skillRoot: string, systemPrompt: string): ResourceLoader {
  const loadedSkills = loadSkillsFromDir({ dir: skillRoot, source: 'cadflow-repository' });
  return {
    getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
    getSkills: () => loadedSkills,
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => systemPrompt,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}

async function createRestrictedTools(
  projectDir: string,
  skillRoot: string,
  blockedRoot: string,
  callbacks: EngineCallbacks,
): Promise<ToolDefinition<any, any, any>[]> {
  const projectReal = await realpath(projectDir);
  const skillReal = await realpath(skillRoot);
  const blockedReal = await realpath(blockedRoot);
  const readableRoots = [projectReal, skillReal];
  let todos: Todo[] = [];

  const assertReadable = async (path: string): Promise<string> => {
    const resolved = await realpath(path);
    if (!readableRoots.some((root) => isWithin(root, resolved))) {
      throw new Error('read path is outside the Project and configured Skill reference');
    }
    return resolved;
  };
  const assertWritable = async (path: string): Promise<string> => {
    const absolute = resolve(path);
    if (!isWithin(projectReal, absolute)) throw new Error('write path is outside the Project');
    let ancestor = absolute;
    while (true) {
      try {
        const info = await lstat(ancestor);
        if (info.isSymbolicLink()) throw new Error('write path must not traverse a symbolic link');
        const resolvedAncestor = await realpath(ancestor);
        if (!isWithin(projectReal, resolvedAncestor)) throw new Error('write path is outside the Project');
        break;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
        const parent = dirname(ancestor);
        if (parent === ancestor) throw new Error('write path has no safe Project ancestor');
        ancestor = parent;
      }
    }
    return absolute;
  };

  const readDefinition = createReadToolDefinition(projectDir, {
    operations: {
      access: async (path) => { await access(await assertReadable(path), fsConstants.R_OK); },
      readFile: async (path) => readFile(await assertReadable(path)),
    },
  });
  const writeDefinition = createWriteToolDefinition(projectDir, {
    operations: {
      mkdir: async (path) => { await mkdir(await assertWritable(path), { recursive: true }); },
      writeFile: async (path, content) => { await writeFile(await assertWritable(path), content, 'utf8'); },
    },
  });
  const editDefinition = createEditToolDefinition(projectDir, {
    operations: {
      access: async (path) => { await access(await assertWritable(path), fsConstants.R_OK | fsConstants.W_OK); },
      readFile: async (path) => readFile(await assertWritable(path)),
      writeFile: async (path, content) => { await writeFile(await assertWritable(path), content, 'utf8'); },
    },
  });

  const localBash = createLocalBashOperations();
  const bashDefinition = createBashToolDefinition(projectDir, {
    exposeSessionEnvironment: false,
    operations: localBash,
    spawnHook: ({ command }) => ({
      command: sandboxedCommand(command, projectReal, blockedReal),
      cwd: projectReal,
      env: sanitizeEnvironment(process.env),
    }),
  });

  const todoDefinition: ToolDefinition<any, any, any> = {
    name: 'write_todos',
    label: 'write_todos',
    description: 'Replace the run-local todo list with an ordered list of content and status items.',
    parameters: Type.Object({
      todos: Type.Array(Type.Object({
        content: Type.String(),
        status: Type.Union([
          Type.Literal('pending'),
          Type.Literal('in_progress'),
          Type.Literal('completed'),
        ]),
      })),
    }),
    execute: async (_id, parameters: { todos: Todo[] }) => {
      todos = [...(parameters.todos as Todo[])];
      return { content: [{ type: 'text', text: JSON.stringify({ todos }) }], details: { todos } };
    },
  };
  const validateDefinition: ToolDefinition<any, any, any> = {
    name: 'validate_model',
    label: 'validate_model',
    description: 'Run the authoritative Python CAD validator for the current Project Model Source.',
    parameters: Type.Object({}, { additionalProperties: false }),
    execute: async () => {
      const result = await callbacks.validate();
      return { content: [{ type: 'text', text: JSON.stringify(result) }], details: result };
    },
  };

  return [
    readDefinition,
    writeDefinition,
    editDefinition,
    bashDefinition,
    todoDefinition,
    validateDefinition,
  ] as ToolDefinition<any, any, any>[];
}

function isWithin(root: string, candidate: string): boolean {
  const path = relative(root, candidate);
  return path === '' || (!path.startsWith('..') && !isAbsolute(path));
}

function sanitizeEnvironment(environment: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  return Object.fromEntries(Object.entries(environment).filter(([name]) => !SENSITIVE_ENV_NAME.test(name)));
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function sandboxedCommand(command: string, projectDir: string, blockedRoot: string): string {
  const args = [
    '--die-with-parent',
    '--unshare-net',
    '--ro-bind', '/', '/',
    '--tmpfs', blockedRoot,
    '--bind', projectDir, projectDir,
    '--chdir', projectDir,
    '/bin/bash', '-lc', command,
  ];
  return `exec /usr/bin/bwrap ${args.map(shellQuote).join(' ')}`;
}

async function assertDirectory(path: string, label: string): Promise<void> {
  await access(path, fsConstants.R_OK);
  const info = await lstat(path);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error(`${label} root must be a real directory`);
}

export const testing = { isWithin, sanitizeEnvironment, sandboxedCommand };
