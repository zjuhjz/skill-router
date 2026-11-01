# capability-spec v0.4 schemas

This directory is the machine-readable, vendor-neutral contract layer of
`skillcall`. It contains no routing, execution, or evaluation policy.

- `capability.schema.json` validates a capability version's metadata and
  declarative output rules.
- `implementation.schema.json` validates `skill.capability.yaml` manifests.
- `invocation.schema.json` validates command requests and responses.
- `error.schema.json` validates the standard runtime error object.

The schemas use JSON Schema draft 2020-12. Unknown fields are accepted so a
v0.1 implementation manifest and capability contracts can gain optional fields
without breaking existing consumers.

See [`VERSIONING.md`](VERSIONING.md) for capability-major, manifest-format,
implementation-version, and additive-extension compatibility rules.

After installing this package, validate the complete snapshot with:

```bash
skillcall-spec validate
```

The check validates the four schemas, capability metadata, input/output
schemas and examples, implementation manifests, and cross-references to known
capabilities, platforms, and error codes.
