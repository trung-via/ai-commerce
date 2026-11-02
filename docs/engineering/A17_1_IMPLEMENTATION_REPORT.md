# A17.1 C07/C10/E06 — pre-dispatch source and MT-01 registration

**STATUS: NOT IMPLEMENTED / NOT PRODUCT VERIFIED / NOT MERGE APPROVED.**

- Brain product handoff: https://github.com/trung-via/ai-commerce/issues/255
- Parent baseline pinned at branch creation: `main@e6a554594ad0a511cfa1a19e1e0b38c505eede3a`.
- This PR reserves `creative_scaling` offline source/contract/test ownership, adds two intentionally invalid JSON schemas, two sentinel test modules, and registered `mt01-a17-1-tests` running 12 pytest workers.
- All A17.1 API placeholders raise `NotImplementedError` and do not create variants, read accounts, write data, call models, generate media or publish. Test PASS only proves the stubs fail closed.
- On a later *new main* after pre-registration merge, a real authorized EF02 executor must replace the stubs and sentinel tests with substantive source and positive/negative tests for immutable C07 variant lineage, opt-in A11 advisory consumption C10 and truthful E06 next-brief proposals.
- Required consumers: A03/A05 composition and rendering reference, A04 immutable media records, A11 advisory export, A15 status/cost; preserve all upstream APIs. Test targeted A17.1 plus affected A11/A05/A04/A15/A02 fixtures in executor and hosted CI. MT-01 owner/consumer closure and mandatory Ubuntu+Windows exact-head evidence.
- A17.2 (D05/L07) and A17.3 (D07/D08) are not in this registration. No live provider rights or direct-post permission inferred.

Do not dispatch EF02 from this PR; never publish Brain PASS until independent protected registration review and actual CI evidence at exact PR head/base.
