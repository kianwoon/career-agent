# vastai update template wipes unspecified fields + needs hash id

- **Date**: 2026-08-19T03:18:04+0800
- **Type**: lesson

## What happened

Updated vast.ai template 567382 onstart. Numeric id -> 400; real fix: hash_id from 'search templates id eq <id> --raw'. Then 'vastai update template <hash> --ssh --onstart-cmd X' succeeded but the CLI PUT nulls every field not passed (name/image/tag/env wiped). Each update also rotates the hash_id, so a follow-up with the old hash silently fails.

## Root cause / fix

Before 'vastai update template': (1) get current hash_id via search templates --raw, (2) pass ALL fields in one call (--name --image --image_tag --env --desc --disk_space --ssh --onstart-cmd), (3) re-fetch hash_id after every update, (4) always verify with search templates after. Test updates with a throwaway value first.
