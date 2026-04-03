# Versioning and Release Notes

This repository now keeps versioning visible inside the repo instead of leaving it only in git tags.

## Current baseline

- visible git tag: `1.0.0`
- active development branch consumption in the demo repo: yes
- formal changelog file: yes, see [`../CHANGELOG.md`](../CHANGELOG.md)

## Policy

- Use semantic versioning for documented releases.
- Track unreleased work in `CHANGELOG.md`.
- Cut a tag only when the shared library docs, Python tests, and Groovy tests are green together.

## Consumption guidance

- During active development, consuming the library by branch is acceptable for the thesis workspace.
- For reproducible demos or thesis evidence snapshots, prefer a tag or release cut from a green state.
