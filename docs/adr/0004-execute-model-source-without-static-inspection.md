# Execute Model Source without static inspection

The local demo executes Agent-generated Model Source without AST, import, or dangerous-call inspection. Allowed dependencies remain an Agent instruction rather than an enforced security boundary; project-scoped working directories, time limits, output limits, and cancellation improve operational control but do not isolate Python code. This deliberate shortcut is acceptable only under the trusted single-user boundary in ADR-0001 and must be replaced before any untrusted network exposure.
