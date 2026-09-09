---
name: Panelist browser simulation
description: A readable workbench for inspecting expert grading and verifiable delivery.
colors:
  primary: "#b92f20"
  paper: "#f3eee3"
  ink: "#272822"
  muted: "#646258"
  line: "#d7d0c2"
  success: "#366449"
typography:
  display:
    fontFamily: "Fraunces, Georgia, serif"
    fontSize: "clamp(34px, 4.2vw, 60px)"
    fontWeight: 500
    lineHeight: 1.07
    letterSpacing: "-0.035em"
  body:
    fontFamily: "Arial, Helvetica, sans-serif"
    fontSize: "16px"
    lineHeight: 1.55
  data:
    fontFamily: '"IBM Plex Mono", ui-monospace, monospace'
    fontSize: "11px"
rounded:
  control: "4px"
spacing:
  compact: "8px"
  regular: "16px"
  section: "28px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    padding: "10px 14px"
---

# Design System: Panelist

## Overview

This document records the browser simulation implemented in `web/src/style.css`. Its warm paper, red accent, Fraunces headings, and IBM Plex Mono data typography continue the existing HTML identity. Controls stay beside observable results, and a dark delivery panel marks the final artifact.

## Colors

Primary red marks the run action, selected views, focus, and rejected or paused states. Ink carries reading text and the delivery panel. Muted text remains readable on paper; green marks approved work and completed phases. Status is always stated in text as well as color.

## Typography

Use display typography for the introduction and section headings. Body text and controls use the sans-serif stack. Tables, counts, events, and checksums use tabular monospaced figures. Keep dense labels readable and reserve the largest type for the introduction.

## Layout

The desktop container has a 1440px maximum width and 48px side padding. A 270px control column sits beside flexible results. Below 1000px the rail narrows and the metrics use two columns; below 700px the workspace stacks vertically. Tables and long export summaries scroll inside their own containers instead of widening the page.

## Elevation & Depth

Surfaces are flat. Rules, warm tonal fills, and the dark delivery panel separate sections. The selected inspection button uses a two-pixel inset underline, without raised card shadows.

## Shapes

Inputs, buttons, and filled panels use gently rounded control corners. Phase numbers are circular. Tables use horizontal rules and compact rows.

## Components

Buttons have a visible three-pixel accent focus outline and an explicit disabled state. The primary run action changes to pause while active. Task, expert, and event selectors use native buttons with `aria-pressed`; they retain text labels on mobile. Empty states explain the action that produces data. The delivery panel keeps downloads disabled until the final summary exists and allows long checksums to wrap.

## Do's and Don'ts

- Do preserve the local simulation disclosure and textual status labels.
- Do use the muted token for small supporting text on paper.
- Do keep controls keyboard-accessible and preserve the skip link.
- Don't let tables or checksums cause page-level horizontal overflow.
- Don't communicate approval, pause, or completion solely through color.
