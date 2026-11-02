# Overview category views

Approved design: All plus four categories (purpose, structure, Issue/PR status, management). All contains one compact paragraph per category. A selected category shows full report text; Issue/PR shows snapshot records with status, Markdown body, timestamps and source links. Explicit same-repository references are labelled textual references, never formal GitHub linkage. Missing data remains unknown. No API/schema changes.

Implementation: add pure categorization/summary/reference helpers and regression tests; add the category switcher and Issue/PR detail component; preserve report fragments and evidence; add responsive styling. Run frontend tests and production build, then browser-check All/category switching and narrow layout. Commit localized frontend change to main. Deployment must preserve the existing unmerged backend PR #9 and record both versions.
