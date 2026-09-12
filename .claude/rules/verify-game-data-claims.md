---
paths:
  - "src/**"
  - "tests/**"
  - "docs/**"
---

# Verify game-data claims against the engine, not prose

Any review finding, spec claim, or dispatch prompt that asserts a fact about card data — energy costs, attack text, damage values, HP, weaknesses/resistances, retreat cost, or any other card/attack property — MUST be verified by an executable check against the engine's own data (e.g. `all_card_data`, `all_attack`) or by instrumented gameplay output. Do not accept or propagate a claim derived from reading a CSV or card list in prose.

**Datapoint (2026-07-08, Slice 1 Pass-2):** a per-task reviewer asserted that Cramorant costs Fighting energy for one of its attacks. This was wrong — the actual cost is Colorless. The claim was refuted only when the opus fixer ran 20 instrumented games and printed the real attack data. A prose read of the card CSV produced a confident, wrong finding.

**Practical check before accepting any such claim:**
```python
from <engine_module> import all_card_data, all_attack  # adjust import path to actual location
card = all_card_data["<card_name>"]
print(card, all_attack[card.attack_id])
```
or run a short instrumented match and print the actual `Attack` object used, then compare against the claim.

This generalizes the "subagent self-reports are unreliable" family (global CLAUDE.md → Subagent Discipline) to game-data-specific claims: a reviewer or implementer stating "card X costs Y energy" is a numeric/factual claim and needs the same executable-check discipline as an arithmetic verification claim.

## The `attackId` convention — a specific, recurring source of fabricated card-fact claims (2026-07-09, Slice 3)

`CardData.attacks` holds **attackIds**, not indices into `all_attack()`. `all_attack()[i].attackId == i+1` — the list is 0-based, the IDs are 1-based. Indexing the `all_attack()` list directly with an attackId silently returns the WRONG attack, shifted by one, with no error. Always look up via `{a.attackId: a for a in all_attack()}`, never `all_attack()[attackId]`. Documented at `src/ptcg/decks/candidates/RATIONALE.md`.

**Datapoint:** two separate card-fact errors traced to this exact convention miss occurred in one Slice-3 session — a fabricated Mankey effect and a wrong Koraidon Shred energy cost — both from ad-hoc verification scripts that ignored the 1-based/0-based offset. Both were opus deck-builders stating effects/costs from pattern memory rather than running the executable check this rule already requires.

**Practical corollary — verify at BUILD time, not just review time.** The existing executable-check requirement above was being satisfied only at review/Pass-2, after the wrong claim had already been used to justify a deck-construction decision. Every load-bearing effect/cost claim used during deck construction (not just during review) needs the executable DB check — inline in the deck-building script or session, before the claim is used to make a build decision — not deferred to a later review pass.
