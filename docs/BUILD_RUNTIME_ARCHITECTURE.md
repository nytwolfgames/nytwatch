# Build Runtime Architecture

## Overview

This document captures the design decisions for extending the NytwatchAgent UE5 plugin to support packaged game builds, in addition to the existing Play In Editor (PIE) workflow.

---

## Problem Statement

The current plugin is editor-only in ways that prevent it from running in packaged builds:

1. **Module type** — `"Type": "Editor"` in the `.uplugin`; the module is stripped at cook time
2. **Subsystem** — `UEditorSubsystem` does not exist in packaged builds
3. **Session lifecycle** — driven by `FEditorDelegates::BeginPIE` / `EndPIE`, which do not fire in builds
4. **Class-to-system mapping** — uses `FSourceCodeNavigation` to match tracked objects to armed systems by source file path; source files do not ship with a build

---

## Connection Model: Plugin-in-Build (Push, Auto-Detect)

The plugin is baked into the `.exe`. When the game boots, it automatically connects outward to the nytwatch server — the same WebSocket handshake and `session_open` message used today. No process scanning or debugger-attach model is needed.

**Why this works:**
- Mirrors the PIE model exactly: the process announces itself, the server receives it
- Multiple builds running simultaneously (e.g. a dedicated server `.exe` and client `.exe`) each open their own session
- Instance selection is a server/dashboard concern — the UI shows all active sessions and the developer picks which to view

**Config delivery in builds:**
The plugin needs to know the server address. Options in priority order:
1. Command-line argument: `-NytwatchServer=ws://localhost:8420`
2. Config file dropped by the server into the build's `Saved/Nytwatch/` folder before launch
3. Compiled-in default (localhost, for local dev machines)

---

## Solution: Option A + B

### Option A — Split into Runtime + Editor Modules

Add a second module (`NytwatchAgentRuntime`, type `Runtime`) alongside the existing `NytwatchAgent` (type `Editor`).

| Module | Type | Owns |
|--------|------|------|
| `NytwatchAgentRuntime` | Runtime | WebSocket, property tracking, session lifecycle via `UGameInstanceSubsystem` |
| `NytwatchAgent` | Editor | PIE delegates (`FEditorDelegates::BeginPIE/EndPIE`), `FSourceCodeNavigation`, Slate notifications |

The editor module becomes a thin shell that hooks into PIE and delegates to the runtime module. Both PIE and packaged builds use the same runtime core; editor-only features are stripped at cook time.

**Session lifecycle in builds:**
`UGameInstanceSubsystem` fires on game boot and shutdown — the runtime equivalent of `BeginPIE` / `EndPIE`. Crash detection retains the lock file + watchdog pattern.

### Option B — Bake Class Mappings at Cook Time

The armed system config uses source file paths to assign tracked classes to systems (e.g. `/Source/Combat/*.h → "Combat"`). Source files do not exist in a build, so this resolution must happen before cooking.

A cook-time step walks all tracked classes and generates a precomputed data asset:

```
ClassName → SystemName
```

The runtime module reads this baked table instead of calling `FSourceCodeNavigation` at runtime.

**Behavior per context:**

| Context | Class-to-system resolution | Hot-reload |
|---------|---------------------------|------------|
| PIE (editor) | File path matching via `FSourceCodeNavigation` | Yes — config reloads every 1s |
| Packaged build | Baked data asset (frozen at cook time) | No — mappings are fixed |

The baked asset is regenerated each cook, so it stays in sync with the source-path config as long as the project is re-cooked after armed system changes.

---

## What Does Not Change

- The WebSocket protocol (`session_open`, `event_batch`, `session_close`) — identical between PIE and builds
- The server — already supports multiple concurrent sessions via `session_id`; no changes needed
- The armed system config format — still JSON with source file paths; the cook step translates these to the baked asset
- The lock file + watchdog crash detection pattern

---

## Open Questions

- **Cook step integration** — where does the bake step live? A custom `UDeveloperSettings` editor utility, a commandlet, or a build plugin hook?
- **Config delivery** — which of the three delivery mechanisms becomes the default? Command-line arg is the most explicit for CI/QA environments; a dropped config file is friendliest for local iteration.
- **Dashboard instance selection UX** — when multiple sessions are live (build + PIE, or server + clients), what does the session picker look like?
