# BQ-03a R6: synthetic preparation and future BQ-03b protocol

Source: `6ed23280b954e276a06bfc1bef2810927889f9b0`. Human authorized this
bounded source cut with “ok tiếp tục”. [ADR B002](../architecture/ADR_B002_CREATIVE_INTELLIGENCE_QUALITY.md)
and [adopted Issue #373](https://github.com/trung-via/ai-commerce/issues/373)
define the Q2/Q4/R6 consumer and authority boundaries. This is a counterbalanced
plan and synthetic simulation. Human study, recruitment, participant sessions,
randomized exposure, observation, consent and authentic observer verification
are NOT_RUN / NOT_ESTABLISHED. No enrolled participant, result, rights grant,
provider finding or study date is asserted here.

## Use the source-only preparation

Import the three modules directly; no package initializer or closed B0 schema
changes. `build_plan([sha_a, sha_b], seed=seed_sha256, reviewer_slots=4)` accepts
unique lowercase SHA256 references, never bytes/paths/URLs. The seed argument is
64 hexadecimal characters, not a small integer. It is private replay material;
the function neither generates entropy nor attests its quality. A fixture seed
is deterministic, not proof of unpredictable allocation. The private record's
digest binds every field; pin that digest outside the record. Identical inputs
and seed have identical canonical UTF-8 bytes. Sort-normalized asset/rubric
inputs make order immaterial. Canonical A/B is lexical SHA order, not a model,
provider or expected winner. Pair selection and arm allocation use domain
separated HMAC-SHA256; a separate domain commits the seed. At most 12 references,
16 even synthetic slots, 66 pairs and 1,056 assignments are possible. Each slot
gets each selected pair once; each pair has equal A/B and B/A arms across
distinct slots. These limits are engineering bounds, not a sampling justification.

Keep `to_json()` PRIVATE. It contains the seed, task/slot/pair mapping and
canonical asset references. `public_tasks()` is an allowlist projection with
opaque task IDs, LEFT/RIGHT placeholders and fixed rubric tokens only. It
excludes hashes, paths, pair/slot IDs, model/provider identity, product and source
references, seed and mapping. A linked native BQ02b review requires its exact
digest, matching expected asset SHA and unchanged T3 NOT_RUN. Supply that same
native review and digest for replay/public export/report operations. This is
source linkage only: asset_seen stays NOT_ESTABLISHED, rights UNKNOWN. Detached
JSON and absent/mismatched source pins fail. No link means UNVERIFIED.

`synthetic_judgments(plan, entries, plan_digest=...)` accepts only fixture rows
with exact assigned `synthetic_reviewer_slot`, `task_id`, choice LEFT/RIGHT/TIE/
ABSTAIN and `scores` (possibly empty). Scores are finite 0..5 for eligible
planned dimensions. There are no free-text, participant, email, name, consent,
signer or attestation fields. Caller HUMAN_OBSERVED cannot enter reports.
ABSTAIN has no scores. Missing rows remain missing. No external entry intake or
independent identity ledger exists. `synthetic_report` and report replay require
the exact native plan and matching native synthetic set plus both digest pins.
Report output is PRIVATE analysis containing canonical asset references.

Eligibility is codeable from declared intent, never an inspected fact:

| Intended content | Eligible planned rubric | Unestablished evidence |
|---|---|---|
| product_ref=null, no product | overall preference; other applicable modes | product consistency unavailable |
| animation or intentional fantasy | overall preference, visual continuity | literal physical plausibility unavailable |
| stylized/live action | visual continuity, physical plausibility | correctness and naturalness unmeasured |
| voice-only | overall preference; audio preference with VOICE/MUSIC | visual dimensions unavailable |
| silent | overall preference; applicable visual dimensions | audio NOT_APPLICABLE |
| NO_LOGO | other applicable dimensions | logo legibility unavailable |
| unspecified visual/audio intent | overall preference | no uninspected signal promoted to fact |

Product/logo dimensions require explicit planned references/modes. These are
rubric applicability declarations, not product truth or rights verification.
Future naturalness questions need their own reviewed rubric and authentic
observers; no existing score means native-human naturalness.

## BQ-03b: consent before any real intake

This is a proposed future design requiring a separately authorized WorkItem,
authenticated consent, privacy/retention plan, sampling plan and independent
observer attestation. BQ-03a does not implement a live study behind a flag. Keep
a future Human rater sidecar separate from these SYNTHETIC_FIXTURE_ONLY records
and from closed CreativeDirective.v1, BrainDecision.v1 and ProductJourneyReceipt.v1.

1. Pre-register the question, target population, inclusion/exclusion rules,
   rights eligibility, dimensions, primary/secondary endpoints, treatment of
   ties/abstentions/missingness, allocation algorithm, analysis units, minimum
   usable data, stopping rule and all HOLD criteria. Bind exact asset bytes,
   protocol/rubric versions and source approvals by digest. No outcome-driven
   seed/pair/participant/metric selection. Publish deviations to the authorized
   reviewer before interpretation. No cherry-pick of attractive assets or raters.
2. Complete separate rights review before exposure, including any voice,
   likeness, product, logo or sensitive material. Preference does not establish
   lawful use. Obtain informed consent before scheduling/exposure or recording
   responses: purpose, voluntary participation, withdrawal, intended data uses,
   access, retention/deletion, compensation if any and contact through an
   authorized organizer. No recruitment or consent collection is authorized now.
3. An independent future observer verifier must validate consent through a
   separately authenticated channel and bind protocol/asset/rubric/session
   digests, actual allocation/exposure and response provenance. Define key
   custody, revocation, freshness, replay resistance and exact-head receipt
   verification separately. A caller label, signature-shaped string or boolean
   is not observer authentication. Unknown identity/consent/exposure -> HOLD;
   do not manufacture an observed receipt from a synthetic row.
4. Store only study-scoped pseudonyms in the proposed analysis sidecar. Any
   necessary consent/contact linkage belongs in a separately permissioned store
   with named custodian, restricted access and approved deletion schedule. Do
   not collect raw names, emails, birthdates, biometrics or bank details in
   engineering fixtures/analysis. Do not place PII in logs, public tasks or B0
   wires. Withdrawal handling must be specified before collection.
5. Justify sampling with the target question and precision/power calculation,
   accounting for clustered raters/assets, exclusions and attrition. Any example
   allocation such as **hypothetical** 24 raters over **hypothetical** 12 pairs
   is a planning illustration, not enrollment, a recommended minimum, adequate
   power or a result. Pre-register diversity goals relevant to the audience
   (e.g. language familiarity, accessibility needs, context), minimize sensitive
   attributes and avoid treating demographic categories as quality truth.
6. Independently randomize actual exposure within pre-registered constraints;
   hide asset/model/provider labels and distinguish LEFT/RIGHT from canonical
   asset names. Prefer independent between-rater swapped arms to avoid memory.
   If within-session ABBA is necessary, pre-register order, washout, repeat
   handling, fatigue limits and crossover/memory analysis. ABBA cannot guarantee
   independence or erase learning. Never label BQ-03a's planned assignments as
   actual randomized exposure.
7. Record minimal authenticated exposure/completion evidence separately, ensure
   sessions are independent where claimed, prevent duplicate participation with
   approved privacy controls, and preserve abstentions, dropouts and reasons
   within pre-registered privacy limits. Check order, position, carryover,
   rater-context and selection confounds; equal planned arms do not ensure equal
   usable responses or balanced rater characteristics. Blindness breaches,
   missing attestation, asset drift or unsafe exposure -> HOLD.
8. Analyze canonical A/B preferences after unblinding in authorized analysis
   only. Report eligible/received/usable/abstain/missing counts per pair and arm.
   Treat ties as a separate agreement category; exclude ties/abstentions/missing
   from binary intervals with explicit denominators. Report available pair
   agreement/disagreement. Conventional Fleiss kappa needs equal numbers of
   usable ratings per item; insufficient data, unequal counts or a zero chance
   denominator -> UNDEFINED, not NaN or a convenient zero. Investigate rater
   dependence and prevalence effects; kappa alone is not calibration.
9. BQ-03a's Wilson calculation uses z=1.959963984540054 and rounds to six decimal
   places solely for deterministic replay. It supplies no independence/cluster
   adjustment. Small n is explicitly descriptive without a confidence claim;
   the implementation's n<30 annotation is not a quality or study threshold.
   A future real study needs a justified analysis (e.g. rater/asset-aware
   intervals), sensitivity checks, preregistered multiplicity handling and an
   independent review before saying any threshold is calibrated.
10. Label arm differences DESCRIPTIVE_POSITION_ASSOCIATION_NOT_CAUSAL. Choosing
    A consistently can change LEFT rates without a position effect; choosing
    LEFT consistently can cancel canonical asset preference. Neither proves
    model bias. Report uncertainty and counterexamples, including intentional
    fantasy, alongside disagreements. No score establishes factual/commercial
    truth, rights, native Human naturalness, CTR, virality or ROI. Send a
    bounded suggestion to Brain; no automatic production pass/veto, release,
    publish or spend authority follows.

Current observation count is zero. Production quality remains UNKNOWN. Real
Human calibration and all provider/live effects remain NOT_RUN.
