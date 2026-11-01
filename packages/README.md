# skillcall monorepo packages

The repository is transitioning along explicit product boundaries:

| Package | Status | Responsibility |
|---|---|---|
| [`capability-spec`](capability-spec/) | independent v0.4 package | capability and implementation contracts |
| [`skill-eval`](skill-eval/) | independent v0.1 package | reproducible evaluation evidence |
| [`skill-router`](skill-router/) | migration marker | production routing remains at the repository root |

Dependencies remain one-way: Eval and Router may consume Spec; Router may
consume Eval reports; Spec never depends on either project, and Eval does not
depend on Router.
