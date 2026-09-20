# User Guide — Document Anonymizer

🇬🇧 English | 🇫🇷 [Français](./guide-utilisateur-fr.md)

*Guide last updated: September 20, 2026*

This guide explains how to use the document anonymization application, from uploading a file to downloading the anonymized version.

## Overview

The application detects and redacts personal or sensitive information in a document — PDF, Word (.docx), CSV, or image (PNG/JPEG) — with a **mandatory** human review step before the final file is generated: no document can be downloaded without a user's explicit approval.

## 1. Log in

Go to the application's address and sign in with your professional credentials (SSO).

![oauth](Screens/oauth_lab.png)

Here, it's keycloak for this lab:

![keycloak](Screens/keycloak_lab.png)

## 2. Upload a document

On the home screen, click "Choose a file" or drag and drop your document into the drop zone. Accepted formats are: PDF, Word (.docx), CSV, and image (PNG/JPEG/JPG). 

![start](Screens/start.png)

## 3. Review and approve the detected zones (mandatory human review)

Once the document has been analyzed, the personal information detected automatically appears highlighted (zones on the preview for a PDF or image, highlighted items in the text for a DOCX or CSV). Click a zone to exclude it from redaction if it was detected by mistake. This step is not optional: automatic detection is never guaranteed to be exhaustive, so a human must review and explicitly approve the result before the document can be generated. 

![doc](Screens/doc_ex.png)

![result](Screens/result.png)

## 4. Add a zone manually (PDF and images only)

For PDF and image documents, if a piece of sensitive information was not detected automatically, turn on "manual add" mode in the toolbar, then draw a rectangle directly on the document over the area to hide. Click an added zone to remove it. This feature is **not available** for Word (.docx) and CSV files, which rely solely on automatic detection and manual exclusion. 

![manual](Screens/manual.png)

## 5. Generate and download the anonymized document

Once the review is complete, click "Validate" to explicitly confirm the content and generate the final document. The anonymized file is available for download for a limited time (a few minutes) — download it right away. 

![results](Screens/result2.png)

## Common error messages

- File rejected: only PDF, DOCX, CSV, PNG, and JPEG formats are accepted.
- Too many attempts: a short delay applies if several documents are sent in quick succession.
- File expired: if the download wasn't done in time, the process needs to be restarted from the beginning.
