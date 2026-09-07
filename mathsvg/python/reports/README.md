# MathSVG report package

This package supplies the required `mathsvg/python/reports` namespace. Report
generation remains colocated with the canonical producers, including
`mathsvg.python.benchmarks.summarize`, the analysis pipelines and the bounded
oracle/reproducibility tools. This directory is a source-level compatibility
boundary, not a second reporting implementation.

Generated CSV, JSON and Markdown evidence belongs under `mathsvg/results`, not
inside this package.
