# MathSVG configurations

All checked-in profiles and catalogues are deterministic development defaults.
Before a publishable freeze, all five profile TOMLs plus `baselines.toml` and
`ablation/catalog.toml` must be reviewed and explicitly changed to the
top-level declaration `status = "frozen"`. The freeze then records their byte
hashes before holdout execution. Changing any of them after that point creates
a new experiment identity.

The values are search and safety budgets, not claims that the corresponding
acceptance gate has already passed. `mathsvg profile --profile NAME` is the
machine-readable effective runtime contract. Freeze tooling records both its
canonical hash and the TOML file hash; a mismatch in operational fields is a
hard methodology failure rather than an implicit override.

The Fast profile reserves 24,000,000 deterministic optimizer work units. This
is enough for the built-in whole-block native entropy catalogue (eight codec
counts plus winner emission) and independent decode verification on a full
1 MiB Fast block; it is not an elapsed-time allowance.

Fast keeps the canonical `G1` entropy parser and does not run an add-only chain
policy. Balanced, Max, Structured and Repository retain `C8L` (`C8Lazy`): an
eight-candidate bounded chain with one-byte lazy matching, 512 KiB of parser
scratch, at most 25 additional work units per input byte per walk, and two
deterministically charged policy walks. The historical first-admission
experiment retained `C4L`; the follow-on depth oracle found that `C4L` to
`C8L` saved 87,952 bytes
(0.6549908423 percentage points of the G1 baseline), above the 0.5% threshold,
whereas `C8L` to `C16L` saved 50,863 bytes (0.3787838731 points), below it.

Balanced reserves 80,000,000 deterministic optimizer work units. The larger
cap admits its current `C8L` preflight and charged walks while remaining a
hard reproducible work bound; it is not an elapsed-time allowance. The other
profiles retain their profile-specific work budgets.

Balanced disables interval-function enumeration and coordinate discovery by
default. A paired 10-repetition development ablation found exactly zero archive
change on all ten sampled files while disabling both searches reduced aggregate
compression wall time by 29.609% (95% t interval -29.966% to -29.252%). Exact
whole-block functions remain enabled, so constant, linear and periodic source
blocks still receive native procedural representations. Fast, Max, Structured
and Repository retain interval/coordinate search pending their own profile-
specific evidence.

`ablation/catalog.toml` contains only runnable counterfactuals. Each row maps
to a real CLI `--disable` value and removes that provider from candidate
search. Residual, graph and symbolic disable rows are omitted while those
engines are already off in built-in profiles, because such rows would be
no-op evidence.
