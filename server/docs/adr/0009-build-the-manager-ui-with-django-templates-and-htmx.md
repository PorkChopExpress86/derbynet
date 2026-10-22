# ADR-09. Build the manager UI with Django templates, htmx, Alpine.js, Tailwind, and SortableJS

## Status

Accepted. The owner accepted it on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

The manager pages are forms and lists. The alternative was a single-page app with a JavaScript build. It was rejected because it adds Node to packaging, and server-rendered pages cover these forms and lists.

## Decision

Render manager pages on the server with Django templates. Use htmx for partial updates, Alpine.js for local state, and SortableJS for reordering. Build Tailwind CSS with the standalone CLI at build time.

## Consequences

Node is not needed at runtime. Every drag-and-drop interaction gets a tap alternative. [#39](https://github.com/PorkChopExpress86/derbynet/issues/39) owns the design system.

## Evidence

Today's pages use jQuery 1.9.1 (`website/js/jquery.js`), jQuery UI, and touch-punch for drag and drop. The coordinator page is fixed at 975 px (`website/css/coordinator.css`). Only `website/print.php` and `website/photo-thumbs.php` set a viewport meta tag.
