# Evidence Security / Correctness Test Matrix

## Document ingestion

1. same file SHA ingests idempotently;
2. same SHA with incompatible scope is rejected;
3. page count stored;
4. page numbers remain 1-based and stable;
5. extracted page hashes deterministic;
6. zero-text PDF fails without OCR hallucination;
7. superseding document atomically marks old current document superseded;
8. concurrent current-document changes must preserve a single current revision per logical series.

## Retrieval scope

9. Material A query cannot return Material B-only document;
10. ProductRevision note cannot leak into unrelated ProductRevision;
11. current query excludes superseded;
12. history query may include superseded explicitly;
13. withdrawn excluded by default;
14. unknown query returns insufficient evidence, not model-memory answer.

## Citation

15. citation anchor must belong to retrieval allowlist;
16. citation page must match stored page;
17. citation document revision must match stored current document;
18. invented anchor ID rejected;
19. supported technical claim without evidence anchor rejected or falls back;
20. excerpt and file hashes preserved.

## Comparison

21. compare two Materials retrieves evidence independently;
22. differing Pin 5 produces `different`;
23. missing field produces `unknown`;
24. unknown must not become `same`;
25. current Rev B overrides superseded Rev A.

## Relation lifecycle

26. candidate -> validated;
27. candidate -> rejected;
28. validated -> revoked;
29. rejected cannot become validated in place;
30. revoked does not appear in default validated list;
31. rejection uses generic reviewed_by, not validated_by;
32. revocation audited with reason.

## Alternate lifecycle

33. candidate -> approved;
34. candidate -> rejected;
35. approved -> revoked;
36. revoked not active approved;
37. approved but inactive Material remains historical approved and `currently_usable=false`;
38. cross-product approval never leaks.

## Traceable evidence gates

39. strong relation validation without two-sided traceable current evidence fails;
40. relation with valid two-sided evidence can validate;
41. alternate approval without primary/alternate evidence fails;
42. product-specific usage condition without ProductRevision evidence fails when policy requires it.

## Agent / LLM safety

43. Agent evidence tools read-only;
44. LLM cannot call validate/approve/revoke;
45. model-generated unknown citation ID rejected;
46. evidence answer cannot auto-update relation status;
47. alternate evidence cannot affect BuildReadiness arithmetic;
48. alternate evidence cannot affect BuildPlan reservation quantities.

## Baseline

49. Phase 2.3 baseline restore reproduces relation/alternate counts;
50. Phase 2.4 baseline restore reproduces evidence counts and one page-level citation smoke.
