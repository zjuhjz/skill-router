# skillcall compatibility rules

This snapshot is published as **capability-spec v0.4**. The version of this
package, the version of a capability, and the `skillcall` value in an
implementation manifest are separate version domains.

## Capability versions

A capability is addressed as `<id>@<major>`, for example
`video.transcript@1`.

- Removing a field, making an optional field required, narrowing accepted
  values, or changing a field's meaning requires a new capability major.
- Adding an optional input or output field is compatible within the same
  capability major.
- A consumer must ignore output fields it does not understand.
- An implementation may provide more than one major explicitly. A router must
  never silently substitute one major for another.
- `deprecated` capabilities remain readable; new routing should prefer the
  latest non-deprecated major when the caller omits a version.

## Implementation manifests

`skillcall: "0.1"` identifies the current manifest and command invocation
format. It remains `0.1` in capability-spec v0.4 because the wire format has
not made a breaking change.

- New optional manifest fields are compatible. Readers must ignore unknown
  fields.
- Removing or renaming a field, changing its meaning, or adding a required
  field requires a new `skillcall` format version.
- An implementation's own `version` identifies its code release. It does not
  change the capability contract or manifest format version.

## Schema evolution

The JSON Schemas intentionally allow unknown object properties. Conformance
therefore protects the stable required core while permitting additive
extensions. Extensions must not override the semantics of standard fields.
