# npm plugin entry resolution + Node ESM plugin imports

- **Date**: 2026-08-30T19:24:27+0800
- **Type**: lesson

## What happened

Npm.resolveEntryPoint on Node passed the package DIRECTORY to import.meta.resolve which returned the dir URL verbatim; later import() failed with 'Directory import is not supported' (skillful in desktop sidecar). Also: Node ESM type-stripping requires explicit .ts extensions on relative plugin imports.

## Root cause / fix

Entry targets must come from the installed package.json (exports['.'] import/default, then main, then index.js) resolved relative to that package.json — never the bare name (Bun global cache shadowing) and never the directory. Fix landed in packages/core/src/npm.ts resolveEntryPoint; acceptance: bun test packages/core test/npm.test.ts resolveEntryPoint cases.
