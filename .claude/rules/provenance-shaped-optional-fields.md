# Test all provenance shapes for Optional dataclass fields, not just the main path

When a dataclass field is Optional because DIFFERENT creation paths populate different fields (e.g., `Candidate.local_wr: float | None`), every consumer of that field must be tested against all known provenance shapes. A test that only exercises one creation path will pass in CI against that path's Optional value, then crash in production when a second creation path produces a different Optional value.

**Datapoint (factory-submit-crash-fix, 2026-07-22):** The matrix worker's candidate promotion path set `local_wr=None` (no prior local eval), while the legacy deck-matrix and hand-authored paths set it to a float. The submit-gate's `_merit_segment` filter consumed `local_wr` via a `.quantize_segments(local_wr)` call. The test suite only exercised the hand-authored path (where `local_wr` was always float), so the None case never was tested and crashed in production every ~15 minutes for 36 hours (~96 silent failures). Caught not by any fixture, but by unattended-factory observability: missing terminal log lines revealing partial-progress crashes. The fix was a None-guard; the lesson is the test gap.

## Rule

For any Optional field that is None for a SPECIFIC creation path (not for rare edge cases, but as a design choice of that path):

1. **Identify all creation paths** for the dataclass in a code comment or PR description — e.g., "local_wr is None for matrix-promoted, float for hand-authored/deck-matrix."

2. **Test each path independently** via parametrized fixtures:
   ```python
   @pytest.mark.parametrize("provenance", [
       "hand_authored",    # local_wr=float(...)
       "deck_matrix",      # local_wr=float(...)
       "matrix_promoted",  # local_wr=None
   ])
   def test_candidate_field_all_provenances(provenance):
       if provenance == "hand_authored":
           candidate = Candidate(..., local_wr=0.45)
       elif provenance == "matrix_promoted":
           candidate = Candidate(..., local_wr=None)
       # ... test all consumers
   ```

3. **Every consumer of the Optional field must handle all shapes.** If a consumer only handles one shape (e.g., calls `.quantize(local_wr)` directly), add a parametrized test that exercises that consumer against all provenances, not just the primary one.

4. **When a new creation path is added mid-project, grep all consumers of that Optional field** and verify each one is still safe. Do not assume prior test coverage transferred to the new path.

## Generalization

This is a specialization of the global CLAUDE.md "virgin-directory untested-state-shape" lesson: code that assumes "the state will always have X" (where X is actually conditional on provenance) is invisible to tests that only exercise one provenance path. The untested path bites first in production, not in CI. Fixture-based testing is insufficient when state-shape varies by creation path — the fixtures must parametrize over the known shapes.

