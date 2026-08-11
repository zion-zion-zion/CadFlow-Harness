# Use a bounded Deep Agents generation loop

Model generation uses LangChain Deep Agents because the demo is intended to exercise autonomous reference reading, source editing, execution, and repair rather than one-shot text generation. The backend seeds a valid Model Source scaffold, exposes only project-scoped file operations plus dedicated reference-reading and model-execution tools, and permits at most three executions within a five-minute Agent Run. A user may stop the run at any time; cancellation preserves source and diagnostics but never promotes an unvalidated scene to a result.
