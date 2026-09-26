# Project security

This document summarizes the main protections implemented in this project, the design principles followed, and the known limitations to consider before any production deployment on real, sensitive data.

This is a **deliberately condensed summary** of a thorough, iterative internal security audit. Technical details that would allow reproducing a flaw are not published here, including for issues already fixed — only the nature of the risk and the principle of the fix are given.

## Table of contents

- [Design principles](#design-principles)
- [Container isolation and hardening](#container-isolation-and-hardening)
- [Protection against malicious documents](#protection-against-malicious-documents)
- [Confidentiality of processed data](#confidentiality-of-processed-data)
- [Authentication and access control](#authentication-and-access-control)
- [Network and exposure](#network-and-exposure)
- [Dependency management](#dependency-management)
- [Monitoring and detection](#monitoring-and-detection)
- [Known limitations and accepted risks](#known-limitations-and-accepted-risks)
- [Recommendations before going to production](#recommendations-before-going-to-production)
- [Reporting a vulnerability](#reporting-a-vulnerability)

## Design principles

- **Fail-closed by default**: an unavailable security component (antivirus, scanning) or an uncertain verdict blocks processing rather than letting it through by default.
- **No personal data in logs or alerts**: audit logs, metrics, and alerts never use a plain-text file name or identifier — a technical hash serves as a traceable reference without exposing the data itself.
- **Defense in depth**: each risk category (container escape, network, application injection, document content) is handled by a dedicated layer, so that no single protection is a single point of failure.
- **Systematic empirical verification**: each fix is tested against a real, reproduced scenario, then replayed after the fix — not merely inferred from a code read. This discipline is what surfaced several real flaws that a code review alone would not have revealed.
- **100% local processing**: no data from the processed document leaves the infrastructure on which the tool is deployed, except when an optional third-party integration (ICAP antivirus, SIEM alerting) that you control is explicitly enabled.

## Container isolation and hardening

- Each service runs as a non-root user, with a read-only filesystem and Linux capabilities reduced to the strict minimum required.
- The main application service runs under a **custom seccomp profile**, built by observing the actual system calls needed for it to function (rather than the far more permissive generic default profile), and verified in active blocking mode — not just logging mode.
- Access to the Docker socket (needed by the reverse proxy for service discovery) goes through a dedicated, read-only proxy, never a direct connection.
- A process count limit (`pids`) is applied to each container, to bound the impact of a potential compromise spawning subprocesses in a loop.

## Protection against malicious documents

Every uploaded document goes through several independent validation layers before any processing:

- **Structure validation**: the file's binary signature and internal structure are verified server-side (not just the extension or the type declared by the browser).
- **Protection against compressed archive bombs** ("zip bombs") for ZIP-based formats (DOCX): limits on total decompressed size, compression ratio, and number of entries.
- **XXE protection** (XML External Entity injection): systematically disabled on all XML parsing, empirically verified against the three classic attack families (local file read, entity bomb, network exfiltration).
- **CSV formula neutralization** for formulas that could be interpreted by a spreadsheet application (protection against formula injection).
- **Time and volume limits on detection itself**, so that a document designed to be expensive to analyze cannot monopolize the service at the expense of other users.
- **Defense against excessively complex regex patterns** (ReDoS): every custom recognizer is tested with adversarial inputs of increasing size before being put into production.
- **Optional antivirus scanning** (standard ICAP protocol), which can be connected to an antivirus solution already deployed in your infrastructure, run before any parsing of the document.

## Confidentiality of processed data

- **Complete metadata removal** when generating the anonymized document (author, creation dates, software used...), for all three supported formats.
- **Removal of residual objects and fragments** from the pre-anonymization state that could otherwise remain invisibly in the output file (replaced but not purged page content, orphaned archive entries).
- **Limited lifetime** of documents being processed on the server, with automatic purging — including in the event of an abnormal interruption of processing.
- **Restrictive permissions** on files in transit: read access reserved to the application itself, not to other local accounts on the host machine.
- **Security HTTP headers** preventing browser-side caching of previews of documents not yet anonymized.

## Authentication and access control

- Authentication fully delegated to a standard OIDC provider (Keycloak, Entra ID, Okta...) — the application never stores or manages any password itself.
- Ownership verification on sensitive endpoints (previewing and finalizing a document), so that a known or guessed task identifier is not enough to access another user's document.
- Protection against bypassing the reverse proxy via a compromised internal component, through a shared secret verified on every request.
- CSRF protection on session cookies (`SameSite` attribute), verified on browsers that do not apply a strict policy by default.

## Network and exposure

- Internal network segmentation: services that do not need outbound Internet access are technically isolated from it, even in the event of an application-level compromise.
- Only the reverse proxy is publicly exposed; all other services communicate exclusively over internal networks that are not routable from the outside.

## Dependency management

- All dependencies (application packages and base images) are pinned to a precise version rather than following a floating tag such as `latest`, for reproducible behavior and to avoid an upstream update silently introducing a regression or vulnerability.
- A full audit of direct and transitive dependencies has been carried out (application packages and Docker images), with critical identified vulnerabilities fixed where an upstream patch was available.
- An automated monitoring script exists to detect new vulnerabilities in the dependencies actually in use — see [Known limitations](#known-limitations-and-accepted-risks).

## Monitoring and detection

- Technical metrics (volumes processed, rejection causes, internal service availability) are exposed in the standard Prometheus format, with no personal data whatsoever.
- An optional alerting mechanism can relay significant security events (antivirus, service availability, disk space) to your syslog/SIEM collector.

## Known limitations and accepted risks

This project honestly documents what remains open rather than staying silent about it:

- **TLS certificate**: the reference deployment ships with a self-signed certificate, suitable for test use only. A trusted certificate is required before any production use.
- **Sizing under real load**: resource sizing (compute, replicas) has not been validated by a complete load test across every possible configuration.
- **Fine-grained access control on the audit log**: any authenticated user can currently view audit metadata (never document content), with no role distinction. An improvement is planned, though not blocking for use in a restricted-trust setting.
- **Application-level rate limiting**: rate limiting is currently enforced at the reverse proxy level rather than within the application itself — sufficient in practice, but less granular than a dedicated application-level control.
- **Backup and high availability**: the reference deployment does not cover automated backups or fault tolerance — to be set up according to your own continuity requirements.
- **Continuous vulnerability monitoring**: tooling exists but is not yet a fully automated process validated under real conditions — periodic manual vigilance remains recommended in addition.
- **Detection quality**: like any system based on entity recognition models, detection is not guaranteed to be 100% exhaustive, particularly on unusual layouts or phrasing. This is why the human review step before final validation is mandatory, not optional.
- **External pentest**: this summary reflects an iterative internal audit, not a penetration test carried out by an independent third party — strongly recommended in addition, before processing any real sensitive data.

## Recommendations before going to production

1. Replace the self-signed TLS certificate with a trusted certificate.
2. Have the sizing validated by a load test representative of your actual usage.
3. Have an external penetration test carried out, especially if health data or any other category of data considered sensitive under applicable regulations will be processed.
4. Enable antivirus scanning (ICAP) and monitoring (syslog/SIEM) if your infrastructure already has the corresponding building blocks.
5. Set up a recurring process for monitoring dependency vulnerabilities, beyond the one-off audit already carried out.
6. Have session duration and access control policy validated by your CISO/DPO, based on your regulatory context.

## Reporting a vulnerability

If you discover a vulnerability in this project, please report it responsibly rather than disclosing it directly (public issue, social media...):

- https://github.com/EpicFail20/obfusk8/security/advisories/new
- We commit to acknowledging receipt within a reasonable timeframe and keeping you informed of the fix's progress before any public disclosure.
- Once a fix is published, we document the nature of the risk and the remediation in the [`CHANGELOG.md`](./CHANGELOG.md), in the same summarized spirit as this document.

---

*This document is a summary and will be updated as new audits are conducted or open points are resolved.*
