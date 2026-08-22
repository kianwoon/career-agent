"""Candidate credibility / signal-validation module.

Profiles are SELF-REPORTED. Users inflate titles, polish summaries with AI,
and claim skills they barely use. This module validates claims against the
evidenced experience text:

1. TITLE INFLATION
   - Bank titles (AVP, VP, SVP) are often IC-level, not leadership.
   - Grand titles at tiny companies (CTO of a 2-person startup) mean little.
   - "Lead/Head/Chief" in headline may not match the actual role ladder.

2. TENURE DEPTH
   - Long tenure at a real company > short stints.
   - Job-hopping (many short roles) signals less depth per area.
   - A "Director" with 3 months tenure is a weak signal.

3. EVIDENCED SKILLS
   - A claimed skill that appears in the experience bullets is credible.
   - A claimed skill that NEVER appears in the experience text is likely
     resume-padding (especially AI-generated "skill lists").
   - Self-reported numbers ("improved by 25%") are soft evidence at best.

The module produces a credibility score (0-100) plus structured flags and
evidence so downstream matching can discount inflated signals.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Titles that LOOK senior but are often IC-level in banking/finance.
BANK_INFLATED_TITLES = {
    "assistant vice president",
    "assistant vp",
    "avp",
    "vice president",
    "vp",
    "senior vice president",
    "svp",
    "executive director",
    "first vice president",
    "fvp",
}

# Genuinely leadership-scoped titles (when supported by evidence).
LEADERSHIP_TITLES = {
    "head of",
    "chief",
    "cto",
    "cfo",
    "coo",
    "director of",
    "vp of engineering",
    "vice president of engineering",
    "engineering manager",
    "lead engineer",
    "team lead",
    "technical lead",
}

# Short tenure threshold (months) below which a role is a "short stint".
SHORT_STINT_MONTHS = 12

# Minimum total evidenced tenure for a credible seniority claim.
SENIOR_TENURE_YEARS = 8


@dataclass
class RoleEntry:
    """A single parsed role from the experience section."""

    title: str = ""
    company: str = ""
    duration_text: str = ""
    months: int = 0
    location: str = ""
    bullets: str = ""


@dataclass
class CredibilityReport:
    """The credibility assessment of a candidate profile."""

    score: float = 50.0  # 0-100, higher = more credible
    title_inflation: float = 0.0  # 0-1, higher = more inflated
    tenure_depth: float = 0.0  # 0-1, higher = deeper tenure
    evidence_ratio: float = 0.0  # fraction of claimed skills evidenced
    flags: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    roles: list[RoleEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "title_inflation": round(self.title_inflation, 2),
            "tenure_depth": round(self.tenure_depth, 2),
            "evidence_ratio": round(self.evidence_ratio, 2),
            "flags": self.flags,
            "evidence": self.evidence,
            "num_roles": len(self.roles),
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_MONTH_PATTERNS = [
    # "2 yrs 5 mos" / "2 years 5 months" / "1 yr 8 mos"
    re.compile(r"(\d+)\s*(?:yrs?|years?)\s*(?:(\d+)\s*(?:mos?|months?))?", re.I),
    # "6 mos" / "6 months" (months only)
    re.compile(r"(\d+)\s*(?:mos?|months?)", re.I),
]


def _duration_to_months(text: str) -> int:
    """Parse 'Apr 2024 - Present · 2 yrs 5 mos' -> 29 months.

    The year-first pattern is tried first so '2 yrs 5 mos' doesn't get
    misread; the month-only pattern catches '6 mos'.
    """
    # Year-first pattern (may have optional months).
    m = _MONTH_PATTERNS[0].search(text)
    if m:
        years = int(m.group(1) or 0)
        months = int(m.group(2) or 0) if len(m.groups()) > 1 and m.group(2) else 0
        return years * 12 + months
    # Month-only pattern.
    m = _MONTH_PATTERNS[1].search(text)
    if m:
        return int(m.group(1))
    return 0


def parse_roles(experience_text: str) -> list[RoleEntry]:
    """Parse the experience section into RoleEntry objects.

    Structure (from real data):
        <Role Title>
        <Company> · Full-time
        <Duration> · <years> yrs <months> mos
        <Location> · On-site/Hybrid/Remote
        - bullet 1
        - bullet 2
    """
    if not experience_text:
        return []

    lines = [ln.strip() for ln in experience_text.splitlines() if ln.strip()]
    # Skip the leading "Experience" heading.
    if lines and lines[0].lower() == "experience":
        lines = lines[1:]

    roles: list[RoleEntry] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        is_bullet = line.startswith(("-", "•", "·", "1.", "2.", "3.", "4.", "5.")) or re.match(r"^\d+\.", line)
        # Skip LinkedIn's skill-summary lines like "Gitlab, J2EE and +11 skills".
        is_skill_line = re.search(r"and\s+\+?\d+\s+skills?$", line, re.I) or line.startswith("… more")

        # Detect a role block: [title] [company(· Full-time)?] [duration]
        # A title line is short, not a bullet, not a skill-line, and is
        # followed by a company line that is followed by a duration line.
        if not is_bullet and not is_skill_line and i + 2 < len(lines):
            next1, next2 = lines[i + 1], lines[i + 2]
            next1_is_company = (
                "·" in next1 or "full-time" in next1.lower()
            ) or (
                # bare company name: short, no duration, no bullet, capitalized
                len(next1) < 60
                and not _duration_to_months(next1)
                and "present" not in next1.lower()
                and not next1.startswith(("-", "•"))
                and not re.match(r"^\d+\.", next1)
            )
            next2_is_duration = "·" in next2 or _duration_to_months(next2) or "present" in next2.lower()
            if next1_is_company and next2_is_duration:
                title = line
                company = next1.split("·")[0].strip()
                duration_text = next2
                months = _duration_to_months(duration_text)
                i += 3
                # Optional location line (short, no bullet).
                location = ""
                if i < len(lines) and len(lines[i]) < 80 and not is_bullet and "·" not in lines[i]:
                    location = lines[i]
                    i += 1
                # Collect bullets until the next role block.
                bullets: list[str] = []
                while i < len(lines):
                    bl = lines[i]
                    is_bbullet = bl.startswith(("-", "•", "·", "1.", "2.", "3.", "4.", "5.")) or re.match(r"^\d+\.", bl)
                    bl_is_skill_line = re.search(r"and\s+\+?\d+\s+skills?$", bl, re.I) or bl.startswith("… more")
                    if not is_bbullet and not bl_is_skill_line and i + 2 < len(lines):
                        n1, n2 = lines[i + 1], lines[i + 2]
                        n1_is_company = "·" in n1 or "full-time" in n1.lower() or (
                            len(n1) < 60 and not _duration_to_months(n1) and "present" not in n1.lower() and not n1.startswith(("-", "•"))
                        )
                        n2_is_duration = "·" in n2 or _duration_to_months(n2) or "present" in n2.lower()
                        if n1_is_company and n2_is_duration:
                            break  # next role starts
                    if is_bbullet:
                        bullets.append(bl)
                    i += 1
                roles.append(
                    RoleEntry(
                        title=title,
                        company=company,
                        duration_text=duration_text,
                        months=months,
                        location=location,
                        bullets="\n".join(bullets),
                    )
                )
                continue
        i += 1

    return roles


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


def _detect_title_inflation(roles: list[RoleEntry], headline: str) -> tuple[float, list[str]]:
    """Score title inflation 0-1 and collect flags."""
    flags: list[str] = []
    inflation = 0.0
    if not roles:
        return 0.0, flags

    for role in roles:
        title_lower = role.title.lower()
        # Bank-inflated titles (AVP/VP/SVP are often IC in finance).
        for t in BANK_INFLATED_TITLES:
            if t in title_lower:
                inflation += 0.35
                flags.append(f"'{role.title}' at {role.company}: bank title often IC-level, not leadership")
                break
        # Grand title with short tenure.
        if any(t in title_lower for t in ("head of", "chief", "director", "cto")):
            if 0 < role.months < 12:
                inflation += 0.5
                flags.append(f"'{role.title}' with only {role.months//12}y{role.months%12}m tenure — grand title, thin evidence")
            elif role.months == 0:
                inflation += 0.3
                flags.append(f"'{role.title}' with no duration shown — unverifiable seniority")

    # Headline grandiosity vs. actual role ladder.
    headline_lower = (headline or "").lower()
    headline_lead = any(t in headline_lower for t in LEADERSHIP_TITLES)
    has_real_lead = any(
        any(t in r.title.lower() for t in ("head of", "chief", "director of", "cto", "vp of engineering", "engineering manager"))
        for r in roles
    )
    if headline_lead and not has_real_lead:
        inflation += 0.3
        flags.append("Headline claims leadership but experience shows no real leadership roles")

    return min(inflation, 1.0), flags


def _tenure_depth(roles: list[RoleEntry]) -> tuple[float, list[str]]:
    """Score tenure depth 0-1: longer, fewer stints = deeper."""
    flags: list[str] = []
    if not roles:
        return 0.0, flags

    months_list = [r.months for r in roles if r.months > 0]
    if not months_list:
        return 0.2, ["No role durations shown — unverifiable depth"]

    total_months = sum(months_list)
    avg_months = total_months / len(months_list)
    short_stints = sum(1 for m in months_list if 0 < m < SHORT_STINT_MONTHS)

    # Depth based on average tenure.
    if avg_months >= 36:
        depth = 1.0
    elif avg_months >= 24:
        depth = 0.8
    elif avg_months >= 12:
        depth = 0.6
    else:
        depth = 0.3

    # Penalize job-hopping.
    if short_stints / len(months_list) > 0.4:
        depth *= 0.6
        flags.append(f"{short_stints}/{len(months_list)} roles are short stints (< {SHORT_STINT_MONTHS}m) — possible job-hopping")

    return depth, flags


def _evidence_ratio(
    roles: list[RoleEntry], claimed_skills: list[str], experience_text: str = ""
) -> tuple[float, list[str]]:
    """Fraction of claimed skills that appear in experience text.

    Searches the FULL experience text (bullets + role titles + the "and +N
    skills" lines LinkedIn adds) rather than only bullets, because a skill
    appearing anywhere in the role context is some evidence it was used.
    """
    flags: list[str] = []
    if not claimed_skills:
        return 0.0, flags

    search_text = (experience_text or "").lower()

    evidenced = 0
    missing: list[str] = []
    for skill in claimed_skills:
        s = skill.lower().strip()
        if not s:
            continue
        # Multi-word skills (e.g. "Spring Boot") match as literal phrase.
        if re.search(re.escape(s), search_text):
            evidenced += 1
        else:
            missing.append(skill)

    ratio = evidenced / len(claimed_skills) if claimed_skills else 0.0
    if ratio < 0.4 and len(claimed_skills) >= 3:
        flags.append(
            f"Only {evidenced}/{len(claimed_skills)} claimed skills appear in experience text "
            f"(missing: {', '.join(missing[:5])}) — possible resume padding"
        )
    return ratio, flags


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def assess_credibility(candidate: dict[str, Any]) -> CredibilityReport:
    """Assess a candidate's credibility from their enriched profile."""
    experience_text = str(candidate.get("experience", "") or "")
    headline = str(candidate.get("headline", "") or "")
    claimed_skills = list(candidate.get("skills", []) or [])

    roles = parse_roles(experience_text)

    title_infl, infl_flags = _detect_title_inflation(roles, headline)
    depth, depth_flags = _tenure_depth(roles)
    ev_ratio, ev_flags = _evidence_ratio(roles, claimed_skills, experience_text)

    flags = infl_flags + depth_flags + ev_flags

    # Bullet substance: do roles have concrete, action-oriented bullets?
    bullet_count = sum(1 for r in roles if r.bullets.strip())
    action_verbs = {"architect", "build", "built", "led", "lead", "developed", "develop",
                    "designed", "design", "implemented", "implement", "launched", "created",
                    "create", "migrated", "migrate", "scaled", "scale", "improved", "improve",
                    "managed", "manage", "delivered", "deliver", "engineered", "optimized"}
    bullets_text = " ".join(r.bullets for r in roles).lower()
    action_hits = sum(1 for v in action_verbs if re.search(r"\b" + v + r"\w*", bullets_text))
    has_substance = bullet_count >= 2 and action_hits >= 3
    if not roles:
        has_substance = False
    if not has_substance and roles:
        flags.append("Experience lacks concrete action bullets — claims are hard to verify")

    # Composite credibility score.
    # Weighted: tenure depth 35%, bullet substance 25%, evidence ratio 20%,
    # title inflation 20%. Low evidence_ratio is a caution, not a death sentence.
    substance_score = 1.0 if has_substance else 0.3
    score = 100 * (0.35 * depth + 0.25 * substance_score + 0.20 * ev_ratio + 0.20 * (1.0 - title_infl))

    # Penalty for no roles at all (can't verify anything).
    if not roles:
        score *= 0.5
        flags.append("No experience roles parsed — cannot verify claims")

    evidence: list[dict[str, str]] = []
    if title_infl > 0.2:
        evidence.append(
            {"field": "title_inflation", "value": f"Detected inflated title signals ({title_infl:.0%})"}
        )
    if depth:
        evidence.append(
            {
                "field": "tenure_depth",
                "value": f"{len([r for r in roles if r.months>0])} roles, avg {_avg_months(roles):.0f}m each",
            }
        )
    if ev_ratio:
        evidence.append(
            {"field": "evidence_ratio", "value": f"{ev_ratio:.0%} of claimed skills evidenced in experience"}
        )

    report = CredibilityReport(
        score=round(min(score, 100), 1),
        title_inflation=title_infl,
        tenure_depth=depth,
        evidence_ratio=ev_ratio,
        flags=flags,
        evidence=evidence,
        roles=roles,
    )
    return report


def _avg_months(roles: list[RoleEntry]) -> float:
    ms = [r.months for r in roles if r.months > 0]
    return sum(ms) / len(ms) if ms else 0.0
