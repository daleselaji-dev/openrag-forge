# Recipes

A Recipe is a versioned directed acyclic graph. Each edge connects a typed output port to a compatible input port. The compiler rejects unknown nodes, incompatible ports, missing required inputs, ordinary cycles and unsafe side-effect nodes.

Corrective retrieval is represented by a bounded node with an explicit `max_retries`; it is never an unconstrained loop. Published recipes are immutable and identified by a SHA-256 hash.

