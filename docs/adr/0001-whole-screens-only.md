# 1. Whole screens only, never partial updates

Status: accepted, 2026-11-25

## Context

The D200 firmware has a "partial update" command (0x000d) that redraws some keys. A stream of partial updates
(webcam frames, animations) made the firmware slow down and then stop answering until it was replugged. This
happened twice on 2026-11-25. The long-running community drivers send whole screens.

## Decision

`deck.device.Deck.show()` always sends the whole screen, as one zip, at most `MAX_FPS` times a second. It sends
only when some key changed. The camera key shows a single still on demand, never a stream.

## Consequences

- Redraws cost a full upload (tens of KB), so animations are limited to about one frame a second.
- Future reviews should not suggest partial updates "for efficiency".
