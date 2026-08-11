# Limit generated-code execution to a local demo

The first version is a trusted, single-user local demo and runs generated Python in a subprocess with a project-scoped working directory, a timeout, and an output limit. This avoids container orchestration in the minimum implementation, but the service must not be exposed to untrusted network users; doing so requires a new isolation design before deployment.
