# Scenario Gallery

The workbench ships three scenario presets:

1. **Customer Support** — official guidance plus historical cases; useful for citation, source authority and customer-safe boundaries.
2. **Internal Policy** — versioned SOP/HR/IT documents; useful for metadata filters, effective dates and reranking.
3. **Controlled Customer Agent** — missing-field questions, knowledge search, structured ticket draft and human approval; it cannot send messages, write external CRM, promise refunds or decide liability.

Presets are intentionally data-source neutral. A scenario describes its required documents and default Recipe, but it runs against the currently selected knowledge base. This makes the same Trace and Eval contracts reusable with a company's own files.

The custom document parsing preset is `custom_ingest`: route → chunk → metadata → embedding/index. Select an Embedding model in the upload panel before uploading a file. The uploaded file remains the source version; changing the route or model creates a reprocessing/indexing action rather than silently replacing the source.

