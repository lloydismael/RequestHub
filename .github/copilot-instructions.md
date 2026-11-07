# Workspace Copilot Instructions

- Keep responses concise and avoid restating code or terminal output already visible.
- For large logs, search results, JSON, diffs, or file contents, prefer summarizing only relevant parts and preserving all errors.
- When Headroom MCP tools are available, use them for large content compression/retrieval before sending bulky context to the model.
- Preserve exact error messages, stack traces, file paths, and line numbers when compressing context.
- Include every substantive repository change in the release changelog, including UI, backend, fixes, configuration, tests, documentation and maintenance. Update relevant notes/evidence with the change; do not use `[skip changelog]` to bypass this requirement. Verify before/after evidence and Notifications visibility before calling changelog work complete.
