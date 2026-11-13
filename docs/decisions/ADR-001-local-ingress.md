# Fixed local ingress to an internal runtime

Actual Docker Desktop 4.98.0 / Engine 29.9.2 testing showed empty published-port mappings
for containers attached only to an internal bridge, despite configured HostPort bindings.
The database itself was healthy and listening, but the host TCP connection was refused.

A project-owned fixed TCP ingress service connects a dedicated ingress bridge to the private
internal bridge. Only it publishes 127.0.0.1 ports. Its target map is constant (PostgreSQL,
Auth, API, local mail UI), it receives no credentials, it has no dynamic proxy/fetch API,
and it has neither privileged mode nor a Docker socket. API, worker, database, and Auth
remain on the internal network; the graph worker has no Internet-capable network.

This is a local-development topology choice, not production TLS or an external gateway.
No global Docker settings or unrelated resources are changed.
