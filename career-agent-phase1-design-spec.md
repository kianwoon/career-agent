# Career Agent — Phase 1 Design Specification

## 1. Objective

Build a cloud-hosted **Career Agent** that can:

- Search for jobs across supported websites.
- Search for candidates across supported websites.
- Read and extract structured information from job and candidate pages.
- Rank jobs or candidates using configurable matching logic.
- Maintain persistent browser sessions.
- Allow human takeover when MFA, CAPTCHA, or unexpected interaction occurs.
- Require explicit approval before external actions such as sending messages or submitting applications.

Phase 1 should prove the end-to-end architecture without attempting full autonomy.

---

## 2. Phase 1 Scope

### In Scope

1. **Job Search**
   - Search selected job sites and company career pages.
   - Extract job title, company, location, description, source URL, and posting date where available.
   - Deduplicate results.
   - Rank jobs against a stored career profile.

2. **Candidate Search**
   - Search selected public or authenticated sources.
   - Extract candidate/profile information available to the authenticated user.
   - Rank candidates against a supplied job description.

3. **Cloud Browser Runtime**
   - Self-hosted Steel browser service.
   - Chromium browser sessions.
   - Stagehand and Playwright for browser control.
   - Persistent authenticated sessions.
   - Human takeover for MFA, CAPTCHA, or unexpected pages.

4. **Agent Orchestration**
   - LangGraph-based workflow orchestration.
   - FastAPI service layer.
   - Durable task state.

5. **Storage**
   - PostgreSQL for business data.
   - Redis for execution state and task coordination.
   - Qdrant for embeddings and semantic retrieval.

6. **Approval Controls**
   - Read/search/analyse actions may run automatically.
   - Sending messages, connection requests, or submitting applications requires user approval.

---

## 3. Non-Goals

Phase 1 will **not**:

- Build or fork a browser engine.
- Implement CAPTCHA bypass.
- Implement proxy rotation or anti-detection techniques.
- Automate LinkedIn connection requests or messaging without approval.
- Automatically submit job applications without approval.
- Support large-scale multi-tenant recruitment operations.
- Replace ATS or CRM systems.
- Perform automatic CV rewriting and submission.
- Build advanced billing, subscriptions, or enterprise RBAC.

---

## 4. Primary Users

### Job Seeker

Typical request:

> Find senior AI platform or technology leadership roles in Singapore that fit my experience.

Expected output:

- Ranked jobs.
- Match score.
- Supporting evidence.
- Gaps or concerns.
- Source link.
- Recommended next action.

### Recruiter / Hiring User

Typical request:

> Find candidates with Java, Kafka, payments, microservices, and banking experience.

Expected output:

- Ranked candidate shortlist.
- Match score.
- Evidence supporting the score.
- Missing requirements.
- Source links.
- Suggested next action.

---

## 5. High-Level Architecture

```text
                    Web UI
                      |
                   FastAPI
                      |
              LangGraph Supervisor
                      |
        +-------------+-------------+
        |             |             |
   Search Agent   Match Agent   Policy Engine
        |             |             |
        +-------------+-------------+
                      |
                 Browser API
                      |
                  Stagehand
                      |
                 Playwright
                      |
              Steel Browser Service
                      |
                   Chromium
                      |
       +--------------+--------------+
       |              |              |
    LinkedIn      Job Sites      Career Sites
```

Supporting services:

```text
PostgreSQL  -> persistent business state
Redis       -> task/execution state
Qdrant      -> vector search / semantic matching
LLM         -> reasoning, extraction, reranking
```

---

## 6. Core Components

### 6.1 Web UI

Minimal UI for Phase 1:

- Search input.
- Job / Candidate mode selector.
- Task status.
- Ranked results.
- Browser session viewer.
- Human takeover button.
- Approve / reject actions.
- Agent activity timeline.

Suggested stack:

- Next.js
- TypeScript

---

### 6.2 Career API

Responsibilities:

- Accept user requests.
- Create agent tasks.
- Return task status and results.
- Manage approvals.
- Manage browser sessions.
- Expose job/candidate records.

Suggested stack:

- FastAPI
- Pydantic

---

### 6.3 LangGraph Supervisor

Responsibilities:

- Interpret user intent.
- Plan search strategy.
- Dispatch browser actions.
- Track workflow state.
- Call extraction and matching services.
- Pause for approval or human takeover.
- Resume failed/interrupted tasks.

Example workflow:

```text
REQUEST
  |
UNDERSTAND
  |
PLAN SEARCH
  |
RUN SEARCH
  |
EXTRACT
  |
NORMALIZE
  |
DEDUPLICATE
  |
MATCH / RANK
  |
RETURN RESULTS
```

---

### 6.4 Browser Runtime

Recommended Phase 1 stack:

- **Steel** — self-hosted browser/session runtime.
- **Chromium** — browser engine.
- **Stagehand** — higher-level browser interaction.
- **Playwright** — deterministic browser automation.

Responsibilities:

- Start/resume browser sessions.
- Navigate.
- Observe page structure.
- Click/type.
- Extract structured data.
- Upload files when approved.
- Capture screenshots.
- Support human takeover.

---

## 7. Browser Session Design

Each user receives one or more persistent browser profiles.

Example:

```text
user_id: user-001
browser_profile: profile-001
region: Singapore
session_state:
  cookies
  localStorage
  login state
  browser preferences
```

Principles:

- Keep sessions region-stable.
- Prefer stable outbound IP.
- Encrypt session data.
- Never store browser session secrets in source control.
- Treat authenticated cookies as credentials.
- Do not rotate proxies to disguise automation.
- Pause when CAPTCHA or MFA appears.

---

## 8. Browser Control Interface

The Career Agent should **not** receive unrestricted shell access.

Expose a narrow browser API.

Example operations:

```text
browser.navigate(url)

browser.observe()

browser.click(element)

browser.type(element, text)

browser.extract(schema)

browser.back()

browser.screenshot()

browser.pause()

browser.request_human()
```

Suggested observation response:

```json
{
  "url": "https://example.com/jobs",
  "title": "Jobs",
  "elements": [
    {
      "id": "e12",
      "role": "textbox",
      "name": "Search jobs"
    },
    {
      "id": "e21",
      "role": "button",
      "name": "Search"
    }
  ]
}
```

---

## 9. Job Search Workflow

```text
User request
   |
Load career profile
   |
Generate search hypotheses
   |
Search source
   |
Open result
   |
Extract job data
   |
Normalize
   |
Deduplicate
   |
Match against career profile
   |
Rank
   |
Store
   |
Return shortlist
```

### Minimum Job Fields

```text
id
title
company
location
description
source
source_url
posted_at
discovered_at
employment_type
salary_text
match_score
match_reason
status
```

---

## 10. Candidate Search Workflow

```text
Job Description
   |
Understand actual work
   |
Extract mandatory requirements
   |
Generate candidate search strategy
   |
Search sources
   |
Extract profile evidence
   |
Normalize
   |
Match
   |
Rank
   |
Store shortlist
```

### Candidate Ranking Principle

Do not rely primarily on job title.

Rank using evidence such as:

- Actual responsibilities.
- Relevant technologies.
- Industry/domain experience.
- Seniority.
- Leadership responsibility.
- Recency.
- Location.
- Required skills.

### Minimum Candidate Fields

```text
id
name
headline
location
source
source_url
summary
experience
skills
evidence
match_score
match_reason
discovered_at
```

---

## 11. Matching Engine

Phase 1 should use a simple hybrid model.

Example job scoring:

```text
Capability fit       30%
Experience fit       20%
Domain fit           15%
Seniority fit        10%
Career direction     10%
Location              5%
Compensation          5%
Other                 5%
```

Candidate scoring can use:

```text
Mandatory skills     30%
Actual work match    25%
Domain experience    15%
Seniority            10%
Relevant recency     10%
Location              5%
Other                 5%
```

Pipeline:

```text
Hard filters
   |
Embedding similarity
   |
Structured scoring
   |
LLM reranking
   |
Evidence-backed result
```

Every match score should retain supporting evidence.

---

## 12. Policy / Approval Engine

### Automatic

- Navigate.
- Search.
- Read pages.
- Extract information.
- Analyse.
- Rank.
- Save results.
- Research companies.
- Draft text.

### Approval Required

- Send LinkedIn message.
- Send connection request.
- Send email.
- Submit application.
- Upload final CV.
- Confirm interview.
- Modify external records.

### Never Automatic in Phase 1

- CAPTCHA bypass.
- MFA bypass.
- Deleting applications.
- Changing account security settings.
- Bulk unsolicited messaging.

Example policy:

```yaml
search_job: allow
read_profile: allow
save_candidate: allow
draft_message: allow

send_message: require_approval
connect_person: require_approval
submit_application: require_approval

bypass_captcha: deny
change_password: deny
```

---

## 13. Human Takeover

Human takeover is mandatory for Phase 1.

Trigger conditions:

- MFA.
- CAPTCHA.
- Login expired.
- Unexpected modal.
- Site workflow changed.
- Agent confidence too low.
- High-risk external action.

Flow:

```text
Agent detects blocker
      |
Pause task
      |
Notify user
      |
Open live browser
      |
User takes control
      |
User completes step
      |
Return control
      |
Agent resumes
```

---

## 14. Data Model

### Core Tables

```text
users
career_profiles
job_descriptions
jobs
candidates
companies
search_tasks
browser_sessions
browser_actions
match_evaluations
evidence
approvals
applications
messages
```

### Example Search Task

```text
search_tasks

id
user_id
type
query
status
workflow_state
created_at
started_at
completed_at
error
```

### Evidence

```text
evidence

id
entity_type
entity_id
field
value
source_url
source_text
captured_at
```

This lets every AI-generated recommendation be traced back to source evidence.

---

## 15. Security

Minimum requirements:

1. Encrypt browser/session credentials.
2. Store secrets in a managed secret store.
3. Do not expose Steel or Chromium directly to the public internet.
4. Browser workers run in isolated containers.
5. Agent receives only restricted browser commands.
6. Web content is treated as untrusted input.
7. Protect against indirect prompt injection.
8. Maintain action audit logs.
9. External actions require approval.
10. Use least-privilege service credentials.

---

## 16. Observability

Every browser task should generate an activity timeline.

Example:

```text
05:12:04 Opened job search
05:12:06 Entered query
05:12:10 Found 46 jobs
05:12:13 Opened result 1
05:12:15 Extracted job description
05:12:16 Match score: 89
05:12:18 Saved result
```

Capture:

- Task ID.
- Browser session ID.
- Agent step.
- URL.
- Action.
- Result.
- Duration.
- Error.
- Screenshot for significant failures.
- Approval events.

Recommended:

- Structured logs.
- OpenTelemetry later.
- Langfuse for agent traces if desired.

---

## 17. Deployment

Phase 1 does not require Kubernetes.

Recommended initial deployment:

```text
Cloud VM / Container Host
|
+-- career-web
+-- career-api
+-- career-agent-worker
+-- steel
+-- chromium workers
+-- postgres
+-- redis
+-- qdrant
```

Production services can later move to managed equivalents.

### Network Layout

```text
Internet
   |
Web/API
   |
Private Network
   |
   +-- LangGraph Workers
   +-- PostgreSQL
   +-- Redis
   +-- Qdrant
   |
Browser Network
   |
   +-- Steel
   +-- Chromium workers
```

The browser service should not expose an unrestricted public control endpoint.

---

## 18. API Sketch

### Start Job Search

```http
POST /api/v1/search/jobs
```

```json
{
  "query": "AI platform leadership roles",
  "location": "Singapore"
}
```

### Start Candidate Search

```http
POST /api/v1/search/candidates
```

```json
{
  "job_description_id": "jd-123"
}
```

### Task Status

```http
GET /api/v1/tasks/{task_id}
```

### Results

```http
GET /api/v1/tasks/{task_id}/results
```

### Browser Takeover

```http
POST /api/v1/browser/{session_id}/takeover
```

### Approval

```http
POST /api/v1/approvals/{approval_id}
```

```json
{
  "decision": "approve"
}
```

---

## 19. Phase 1 Milestones

### Milestone 1 — Browser Runtime

Deliver:

- Steel running in cloud.
- Persistent Chromium session.
- Playwright connection.
- Stagehand connection.
- Login state survives restart.
- Live browser takeover works.

### Milestone 2 — Job Search

Deliver:

- Run search on one supported job source.
- Extract structured jobs.
- Store results.
- Deduplicate.
- Basic ranking.

### Milestone 3 — Candidate Search

Deliver:

- Accept JD.
- Generate candidate criteria.
- Search one supported candidate source.
- Extract profiles.
- Rank candidates.
- Show evidence.

### Milestone 4 — Agent Orchestration

Deliver:

- LangGraph supervisor.
- Durable tasks.
- Retry/resume.
- Browser task state.
- Human handoff.

### Milestone 5 — Approval & Audit

Deliver:

- Policy engine.
- Approval workflow.
- Activity timeline.
- Browser action logs.

---

## 20. Phase 1 Acceptance Criteria

Phase 1 is complete when:

- [ ] User can create a persistent cloud browser session.
- [ ] User can manually log in and reuse the same session later.
- [ ] Agent can run a job search without human browser control.
- [ ] Agent can extract and store structured job results.
- [ ] Agent can rank jobs against a career profile.
- [ ] Agent can accept a JD and produce a ranked candidate shortlist.
- [ ] Every ranking includes supporting evidence.
- [ ] Agent pauses on MFA or CAPTCHA.
- [ ] User can take over and return control to the agent.
- [ ] External actions cannot execute without explicit approval.
- [ ] Failed browser tasks can resume without restarting the complete workflow.
- [ ] Agent activity is auditable.

---

## 21. Key Risks

### Website Changes

DOM/UI changes can break deterministic automation.

Mitigation:

- Prefer semantic selectors.
- Use Stagehand for unstable pages.
- Keep adapters per source.
- Add regression tests.

### Account Restrictions

Some websites restrict automated access.

Mitigation:

- Follow platform terms.
- Avoid anti-detection techniques.
- Keep activity reasonable.
- Require human interaction where necessary.
- Design the system so no single platform is mandatory.

### Prompt Injection

External pages may contain malicious instructions.

Mitigation:

- Treat page content as data.
- Restrict agent tools.
- Never expose unrestricted shell/filesystem access.
- Require approval for sensitive actions.

### Browser Session Theft

Authenticated cookies are highly sensitive.

Mitigation:

- Encrypt profile/session storage.
- Use private networks.
- Rotate compromised sessions.
- Never log raw session secrets.

### LLM Incorrect Decisions

A model may incorrectly rank or interpret jobs/candidates.

Mitigation:

- Evidence-backed scoring.
- Structured rules before LLM reranking.
- Show reasons and gaps.
- Human approval for external actions.

---

## 22. Phase 2 Candidates

After Phase 1 is stable:

- Scheduled daily job discovery.
- Multiple job boards.
- Company watch lists.
- Application preparation.
- CV tailoring.
- Outreach drafting.
- Email integration.
- Interview scheduling.
- Recruiter workspace.
- Multiple candidate sources.
- Knowledge graph.
- Advanced career memory.
- Salary intelligence.
- Notifications.
- Multi-user tenant support.
- ATS integration.
- Analytics dashboards.

---

## 23. Recommended Phase 1 Technology Stack

```text
Frontend        Next.js
API             FastAPI
Workflow        LangGraph
Browser runtime Steel
Browser control Stagehand + Playwright
Browser engine  Chromium
Database        PostgreSQL
Task state      Redis
Vector DB       Qdrant
Observability   Structured logs + Langfuse
Deployment      Docker containers
```

---

## 24. Phase 1 Guiding Principle

> **Build the intelligence and control layer, not another browser.**

Chromium handles the web.

Steel manages cloud browser sessions.

Stagehand and Playwright control the browser.

LangGraph decides what the Career Agent should do.

The Career Agent adds the domain-specific intelligence: understanding jobs, understanding candidates, ranking, evidence, memory, and controlled actions.
