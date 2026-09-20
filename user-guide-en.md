# User Guide — Document Anonymizer

*Guide last updated: September 20, 2026*

This guide explains how to use the document anonymization application, from uploading a file to downloading the anonymized version.

## Screenshots to capture

The application handles several formats (PDF, Word .docx, CSV, PNG/JPEG images) — capture at least one example for each format your users actually work with.

| # | Screen | What to show |
| --- | --- | --- |
| 1 | Login | Login page (SSO/credentials), before entering anything |
| 2 | Upload | Home screen with the upload button or drop zone, before sending any file (PDF, DOCX, CSV or image) |
| 3 | Detected data | Preview with detected zones highlighted (PDF/image) — for a DOCX/CSV, the detected items highlighted in the text or cells |
| 4 | Excluding a zone | A detected zone after being clicked, shown as excluded (greyed out) |
| 5 | Manual addition (PDF and images only) | "Manual add" mode active, with a hand-drawn zone |
| 6 | Human review confirmation | The explicit review confirmation screen, mandatory before generation |
| 7 | Download | Download screen with the limited-availability message |
| 8 (optional) | Error message | Message shown for an unsupported file (e.g. wrong format) |

## Overview

The application detects and redacts personal or sensitive information in a document — PDF, Word (.docx), CSV, or image (PNG/JPEG) — with a **mandatory** human review step before the final file is generated: no document can be downloaded without a user's explicit approval.

## 1. Log in

Go to the application's address and sign in with your professional credentials (SSO). **\[Screenshot 1\]**

## 2. Upload a document

On the home screen, click "Choose a file" or drag and drop your document into the drop zone. Accepted formats are: PDF, Word (.docx), CSV, and image (PNG/JPEG/JPG). **\[Screenshot 2\]**

## 3. Review and approve the detected zones (mandatory human review)

Once the document has been analyzed, the personal information detected automatically appears highlighted (zones on the preview for a PDF or image, highlighted items in the text for a DOCX or CSV). Click a zone to exclude it from redaction if it was detected by mistake. This step is not optional: automatic detection is never guaranteed to be exhaustive, so a human must review and explicitly approve the result before the document can be generated. **\[Screenshots 3 and 4\]**

## 4. Add a zone manually (PDF and images only)

For PDF and image documents, if a piece of sensitive information was not detected automatically, turn on "manual add" mode in the toolbar, then draw a rectangle directly on the document over the area to hide. Click an added zone to remove it. This feature is **not available** for Word (.docx) and CSV files, which rely solely on automatic detection and manual exclusion. **\[Screenshot 5\]**

## 5. Generate and download the anonymized document

Once the review is complete, click "Validate" to explicitly confirm the content and generate the final document. The anonymized file is available for download for a limited time (a few minutes) — download it right away. **\[Screenshots 6 and 7\]**

## Common error messages

- File rejected: only PDF, DOCX, CSV, PNG, and JPEG formats are accepted.
- Too many attempts: a short delay applies if several documents are sent in quick succession.
- File expired: if the download wasn't done in time, the process needs to be restarted from the beginning. **\[Screenshot 8\]**
