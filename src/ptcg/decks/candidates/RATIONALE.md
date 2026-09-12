# Candidate Decks — Rationale & Measured Results

All card facts below verified against the engine DB (`cg.api.all_card_data()` /
`all_attack()`). Legality/health verified inline with `validate_deck(...) == []` and
`ptcg.decks.mulligan.mulligan_rate(...) <= 0.15`.

> **Attack-lookup convention (do not regress):** `CardData.attacks` holds **attackIds**,
> not indices into `all_attack()` — look up via `{a.attackId: a for a in all_attack()}`
> (the convention used by `ptcg.agents.heuristic`, `ptcg.search.evaluate`,
> `ptcg.decks.analysis`). `all_attack()[i].attackId == i+1`, so indexing the list directly
> with an attackId silently returns the WRONG attack, shifted by one. Proven by
> self-referential cards: Kyurem (144)'s ability names its own **Trifrost**; Bloodmoon
> Ursaluna ex (44)'s ability names its own **Blood Moon** — both only match under
> attackId-keyed lookup.

## Incumbent — `mega-lucario-fighting.csv`

Composition (60): **8 Pokémon / 26 basic Fighting Energy / 26 Trainers**.

| n | id | card | role |
|---|----|------|------|
| 4 | 677 | Riolu (Basic {F}, 80 HP) | Accelerating Stab — 30 for {F} (can't use two turns in a row). A stepping-stone Basic, not an attacker. |
| 4 | 678 | Mega Lucario ex (Stage 1, evolves from Riolu, 340 HP) | Aura Jab — **130 for {F}** + attaches up to 3 Basic {F} from discard to Bench (built-in energy accel); Mega Brave — 270 for {F}{F} (can't use two turns in a row) |
| 26 | 6 | Basic {F} Energy | — |
| 4 | 1224 | Cheren | draw 3 |
| 4 | 1213 | Judge | both players shuffle hand, draw 4 (disruption) |
| 4 | 1121 | Ultra Ball | discard 2 → search any Pokémon |
| 4 | 1102 | Dusk Ball | reveal a Pokémon from bottom 7 |
| 4 | 1182 | Boss's Orders | gust |
| 4 | 1097 | Night Stretcher | recover a Pokémon or Basic Energy from discard |
| 1 | 1082 | Hyper Aroma (ACE SPEC) | search up to 3 Stage-1 Pokémon (fetches Mega Lucario ex) |
| 1 | 1122 | Pokégear 3.0 | reveal a Supporter from top 7 |

**Measured:** `validate_deck == []`, but **mulligan_rate = 0.6005** (only 4 Basic
Pokémon — Riolu is the *only* Basic; Mega Lucario ex is a Stage 1). Prior arena result
0.633 vs sample @30 games is noisy and does not reflect that ~60% of opening hands are
mulliganed away, handing the opponent free cards.

### Reality-vs-brief conflict (named, per Words-of-Wisdom §4)

The task brief's example hypotheses (energy ±2, trainer swaps) **cannot** satisfy the
`mulligan_rate <= 0.15` gate: reaching ≤0.15 requires **≥14 Basic Pokémon** (see table),
and the incumbent has 4. So the brief's small-tweak framing is invalidated by the deck's
actual state. The two dominant levers — **the 26-Energy flood** (far more than the deck's
1–2-energy attack costs plus Aura Jab's discard-recur need) and **the 4-Basic mulligan
hole** — are the same fix: trim Energy, spend the freed slots on on-type Fighting Basics.
Every variant below does this; they differ in *what kind* of Basics fill the slots.

Mulligan table (`mulligan_rate_from_basic_count`): 4→0.601, 8→0.346, 12→0.191,
**14→0.139**, 16→0.099. The 0.15 bar ⇒ 14 Basics minimum.

All added attackers are **non-ex** (preserves prize math — only Mega Lucario ex gives up 2)
and pay their costs with the deck's Basic {F} Energy (Fighting pays both {F} and {C} costs).

---

## v2 — `mega-lucario-v2.csv` ("Fighting-corps")

**Hypothesis:** the incumbent floods 26 Energy while running only 4 Basics (60% mulligan).
Rebalance to **14 Energy** and add a hard-hitting on-type **Fighting attacker corps** that
shares the same energy, so the added Basics double as real early/backup attackers (not dead
mulligan-fodder) while Riolu evolves into Mega Lucario ex.

**Exact diff vs incumbent:**
- OUT: **−12 Basic {F} Energy** (26 → 14)
- IN: **+4 Koraidon** (id 226 — Hammer In, 110 for {F}{F}{C}, 130 HP), **+4 Terrakion**
  (id 607 — Land Crush, 100 for {F}{F}{C}; Retaliate, 50 for {F}{C}, +80 if a Pokémon was
  KO'd last turn — natural revenge attacker, 140 HP), **+4 Sawk** (id 602 — Rising Chop,
  90 for {F} but only works vs Pokémon ex; Elbow Strike, 30 for {F} fallback; 110 HP)
- Trainer suite unchanged.

Result: **validate = [], basics = 16, mulligan = 0.0992.**

**Expected failure mode:** 14 Energy may be too lean if Aura Jab's discard-recur (on Mega
Lucario ex) can't keep tempo — the three-energy costs on Hammer In / Land Crush are hungry;
the power attackers risk being "win-more," duplicating Mega Lucario's job instead of
covering a different matchup axis.

## v3 — `mega-lucario-v3.csv` ("Tech-tilt")

**Hypothesis:** same consistency target as v2, but bet on **cheap single-{F} tech/disruption
attackers** over a second power line — trading raw damage for board control and matchup
coverage. Tests whether a low-cost tech package out-ranks the power corps at equal Basic count.

**Exact diff vs incumbent:**
- OUT: **−12 Basic {F} Energy** (26 → 14)
- IN: **+4 Koraidon** (id 226 — Hammer In, 110 for {F}{F}{C}), **+4 Sawk** (id 602 —
  Rising Chop, 90 for {F} anti-ex tech; does nothing vs non-ex), **+4 Ethan's Sudowoodo**
  (id 378 — Impound: 20 for {F} **and the Defending Pokémon can't retreat next turn**
  (retreat-lock disruption); Try to Imitate: coin flip to copy the opponent's attack; 110 HP)
- Trainer suite unchanged.

Result: **validate = [], basics = 16, mulligan = 0.0992.**

**Expected failure mode:** the tech attacks are conditional (Rising Chop whiffs vs non-ex;
Impound's 20 dmg is chip damage — its value depends on the opponent actually wanting to
retreat), so against non-ex or stationary boards the tech corps under-performs v2's flat
damage and may just be a worse Koraidon deck.

## v4 — `mega-lucario-v4.csv` ("Mulligan-control")

**Hypothesis (isolating experiment):** hold everything as close to the incumbent as possible
and change **only** the mulligan variable — add the *minimum* Basics to clear the bar (14)
and trim Energy the *least* (26 → 16). If v4 already beats the incumbent, the 0.60→0.14
mulligan fix alone is the win and the attacker-composition choices in v2/v3 are secondary.
This is the control the tournament needs to attribute credit correctly.

**Exact diff vs incumbent:**
- OUT: **−10 Basic {F} Energy** (26 → 16)
- IN: **+4 Koraidon** (id 226), **+4 Sawk** (id 602), **+2 Terrakion** (id 607) — 10 Basics,
  cheapest neutral on-type bodies
- Trainer suite unchanged.

Result: **validate = [], basics = 14, mulligan = 0.1386** (comfortably ≤ 0.15).

**Expected failure mode:** 14 Basics sits closest to the bar, so real-game mulligan variance
is highest here; 16 Energy may still be a slight flood. If v4 ranks ~equal to v2/v3 it means
the extra attacker density buys little beyond the mulligan fix.

---

# New archetypes (Task 9) — fresh type families, no prior candidate

The lucario variants (v2–v4) all share one engine on the **Fighting** type, and prior art
covers **water** (`mega-starmie-water`) and **metal** (`mega-mawile-metal`). The three decks
below open **three type families with no prior candidate — Lightning, Darkness, Dragon** —
and add the roster's first **non-aggro control shell**. Archetypes were chosen from
`pool_summary()` (attacker density by type) cross-read with engine effect text
(`all_card_data()` / `all_attack()`), per the settled *effect-text-outweighs-stat-lines*
lesson. All three are **100% Basic-Pokémon attacker lines** (no evolutions), which is the
cheapest route to clear the ≥14-Basic mulligan bar the incumbent failed: each fields **16
Basics → mulligan 0.0992** with room to spare. Shared skeleton: **16 Basics / 15 basic Energy
/ 29 Trainers** (Cheren ×4, Judge ×4, Ultra Ball ×4, Dusk Ball ×4, Boss's Orders ×4, Night
Stretcher ×4, Pokégear 3.0 ×4, **Master Ball ACE SPEC ×1**). Master Ball (id 1125, "search
your deck for a Pokémon → hand") replaces the incumbent's Hyper Aroma ACE SPEC, which fetches
**Stage-1** Pokémon and is dead in an all-Basic list. All card facts below verified against
the engine DB, not a prose CSV read.

## a1 — `aggro-lightning.csv` ("single-prize Lightning tempo corps")

**Hypothesis:** a fresh **Lightning** type can field a wall of interchangeable **120-HP
non-ex Basics** that all attack off the same single Lightning-energy base and hit 110–190,
so there is no evolution tax and no mulligan hole — pure single-prize tempo. The bet is that
uniform 120-HP bodies + a paralysis/energy-accel toolbox out-tempo a slower evolving deck
before it sets up, and that giving up only 1 prize per KO wins the prize race vs ex decks.

**Key cards (all Basic {L}, HP 120):**
- **4× Zapdos (953)** — *Thunder Wave* [{L}] free coin-flip Paralyze (tempo steal) / *Thunderbolt* [{L}{L}{C}] **190**, discard all Energy (nuke finisher).
- **4× Tapu Koko (872)** — *Fast Flight* [{L}] turn-1 "discard hand, draw 5" (consistency engine) / *Thunder Blast* [{L}{L}{C}] **130**.
- **4× Thundurus (514)** — *Charge* [{C}] search+attach a Basic {L} (energy accel, feeds the discard-cost attacks) / *Disaster Volt* [{L}{C}{C}] **110**.
- **4× Zeraora (956)** — *Combat Thunder* [{L}{C}] 20 **+20 per opponent Benched Pokémon** — cheap, scales into wide/aggro boards.
- **15× Basic {L} Energy** (id 4) — Thundurus's Charge recycles the discard-attack cost.

**Measured:** `validate_deck == []`, basics = 16, **mulligan_rate = 0.0992.**

**Expected failure mode:** every big hit discards its own Energy (Thunderbolt/Thunder Blast/
Disaster Volt), so without steady Charge/Night Stretcher recursion the deck can brick on
Energy after two swings; Zeraora is filler when the opponent's bench is empty.

## d1 — `disruption-darkness.csv` ("poison-lock control", non-aggro shell)

**Hypothesis (non-aggro / control):** the pool supports a **passive-damage + resource-denial**
win condition that never needs a big attacker. **Pecharunt**'s *Toxic Subjugation* Ability puts
**5 extra damage counters** on the opponent's Poisoned Pokémon each Checkup, turning *Poison
Chain*'s poison into ~**60 passive damage/turn** (10 poison + 50) plus a can't-retreat lock,
while hand-strip attacks starve the opponent of answers. Win by grinding KOs through poison and
denial, not by racing. Tests whether control is viable at all under the ≤0.15 mulligan gate
(most control shells lean on evolutions/trainers and mulligan badly — this one stays all-Basic).

**Key cards (all Basic {D}):**
- **4× Pecharunt (230, HP80)** — Ability *Toxic Subjugation* (+5 damage counters on Poisoned Active at Checkup) / *Poison Chain* [{D}{C}] 10, **Poison + can't-retreat** — the engine.
- **4× N's Purrloin (291, HP70)** — *Thieving Swipe* [{D}{C}] 30, opponent reveals hand and you **bottom-deck a card of your choice** (hand disruption).
- **4× Team Rocket's Murkrow (463, HP80)** — *Torment* [{D}{C}] 30, **locks off one of the Active's attacks** next turn / *Deceit* [{C}] Supporter tutor (finds Judge/Boss's Orders for more denial).
- **4× Roaring Moon (61, HP140)** — *Vengeance Fletching* [{D}{D}] 70 (+10 per Ancient in discard) — the 140-HP wall/finisher that closes games poison can't.
- **15× Basic {D} Energy** (id 7).

**Measured:** `validate_deck == []`, basics = 16, **mulligan_rate = 0.0992.**

**Expected failure mode:** poison is slow and does nothing to Benched threats; Pecharunt must
stay Active to boost, but it is a 80-HP body that gets KO'd; against decks that heal or switch
freely the lock never closes and the low base damage (10–30) can't race. This is the
non-aggro *control* experiment — it may simply be too slow, which is itself the datapoint.


## g1 — `bigbasic-dragon.csv` ("effect-proof big-Basic beatdown", Fighting/Fire base)

*(Revised after Task-9 review: the original list ran Fighting+Lightning Energy, but Koraidon's
Shred actually costs {R}{F}{C} — the deck's thesis attack was unpayable. Re-verified via
attackId-keyed lookup (`{a.attackId: a for a in all_attack()}`, the repo convention — see the
attack-lookup note at the top of this file) AND cross-checked against `EN_Card_Data.csv`
(`Shred,{R}{F}●,130`). The original rationale line was a transcription error, not an
index-shift: the survey script used the correct convention and printed FIRE. Fixed by
dropping the deck's only Lightning dependency (Raging Bolt) for Druddigon and rebasing
Energy to Fighting+Fire — keeps two colors AND deepens the effect-proof line.)*

**Hypothesis:** **Dragon** Pokémon have no Dragon basic Energy, so a Dragon deck is defined by
its **two-type Energy base** — here **Fighting + Fire**, the pair Koraidon's Shred demands.
The payoff is that the deck's spine is **"Shred"-class, effect-proof** attacks (damage "isn't
affected by any effects on your opponent's Active Pokémon"), so it punches straight through
the prevention/damage-reduction walls in the pool (Metapod Harden, Crustle, Farigiraf, the
many "prevent all damage" flip attacks) which blank a normal attacker. A DB scan of all
Basics with effect-ignoring attack text shows this trio is the payable effect-proof core at
three damage tiers: Koraidon 130 / N's Zekrom 70 / Druddigon 40. Anti-meta big-Basic
beatdown at a low mulligan.

**Key cards (all Basic, HP 120–140; costs verified via attackId-keyed engine lookup +
EN_Card_Data.csv cross-check):**
- **4× Koraidon (62, HP140)** — *Shred* [{R}{F}{C}] **130, effect-proof** / *Primordial Beatdown* [{F}{C}] 30 per Ancient Pokémon in play (Koraidon is Ancient).
- **4× Druddigon (625, HP120)** — *Shred* [{C}] **40, effect-proof** — any single Energy pays it; turn-1 chip attacker. (Its *Ambush* [{R}{W}{C}] needs Water and is unpayable here — accepted dead text.)
- **4× Turtonator (196, HP120)** — *Steaming Stomp* [{F}{C}{C}] **100** / *Fully Singe* [{R}] discard an Energy from an opposing Active ex (tech). Neither is effect-proof (plain damage/effect).
- **4× N's Zekrom (906, HP130)** — *Shred* [{C}{C}{C}] **70, effect-proof** — any Energy pays. (Its *Rampaging Thunder* [{R}{L}{L}{C}] needs Lightning and is unpayable here — accepted dead text.)
- **8× Basic {F}** (id 6) + **7× Basic {R}** (id 2) — Fighting demand: Koraidon Shred + Steaming Stomp + Primordial Beatdown; Fire demand: Koraidon Shred + Fully Singe.

**Payability audit (vs the F+R base):** every attacker has ≥1 payable attack; all three
effect-proof Shreds payable; the two unpayable side attacks (Ambush, Rampaging Thunder) are
named above, not discovered later.

**Measured:** `validate_deck == []`, basics = 16, **mulligan_rate = 0.0992.**

**Expected failure mode:** the two-type base is the risk — Koraidon's Shred needs one of EACH
color plus a third Energy, so a mono-color draw stalls the thesis attack; 15 Energy split 8/7
is thinner per-color than a mono deck, making color-screw the likely loss condition. Also 130
max (vs the lucario line's 270) may simply be too little damage, and Druddigon's 40 is chip,
not pressure.

## Payability repair (unpayable-attack-pool-rule, Task 3)

`anchor-cand-b-mega-starmie.csv`, `anchor-cand-c-palafin.csv`,
`anchor-cand-d-tinkaton.csv` each ran 5-7 off-type filler basics
(Hippopotas/Pinsir/Iron Leaves/Poltchageist/Chi-Yu — none evolved by
anything else in these decks) whose attacks needed energy types the deck
didn't run, tripping the new `attack_payability_problems` rule. Repaired
IN PLACE (mutated-deck identity preserved — no regeneration via
`make_anchor_candidates.py`) by `ptcg.factory.deck_repair.repair_deck`:
basic-for-basic swaps to payable fillers already payable under each deck's
own energy type, count-preserving. All three now pass `validate_deck == []`
and `attack_payability_problems == []`; see `tests/test_anchor_candidates.py`.
