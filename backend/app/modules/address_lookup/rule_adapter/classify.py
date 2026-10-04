"""Read a coverage or exemption clause the source cannot verify word for word.

Module A writes paraphrases, so almost no clause appears verbatim in its
source, and the strict compiler path leaves nearly every rule unresolved. This
is the second reading: a fixed, ordered table of the shapes this corpus
actually uses, each with the rationale it reports. Every reading here is the
machine's own, so whatever it settles is low confidence and stays in the
review queue - it never claims the authority of the source or a reviewer.

A clause gets one of four readings:

  * **condition** - atoms on facts the evaluator can test (year built, units,
    certificate age, ordinance membership, property type, subsidy, seasonal
    use), or on owner facts the data never has, so an owner-only exemption
    stays unknown while "owner-occupied and at most four units" is defeated
    by a 32-unit building whoever owns it;
  * **no building condition** - the clause names who or what is regulated
    (landlords, applicants, deposits, software), a tenancy or transaction,
    a definition, or a note, but not which buildings;
  * **local deference** - a state rule standing aside where local rent
    control applies; becomes a precedence relation, not an exemption;
  * **unresolved** - anything else, including every cross-reference to
    another provision's coverage and every contingency ("only if and when"),
    which stays unknown exactly as before.

Order matters: specific conditions are read before the generic "no building
condition" shapes, and the guard against cross-references and contingencies
runs before those generic shapes can clear anything.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from app.modules.address_lookup.rule_adapter.models import Field, Op, Origin

CONDITION = "condition"
SCOPE = "scope"
TENANCY = "tenancy"
TRANSACTION = "transaction"
DEFINITION = "definition"
QUALIFIER = "qualifier"
DEFERENCE = "local_deference"


@dataclass(frozen=True, slots=True)
class Reading:
    kind: str
    rationale: str
    #: Atoms that must hold together, or - with `alternatives` - any of them.
    atoms: tuple[tuple[Field, Op, Any], ...] = ()
    alternatives: bool = False
    #: A coverage clause phrased as an exclusion ("is exempt", "does not
    #: cover") belongs in the exemption tree.
    as_exemption: bool = False
    #: Set when the clause must stay unresolved, with the reason to report.
    unresolved: str | None = None


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


# ------------------------------------------------------------------ guards ---
#: Coverage written as a list of covered and uncovered categories. The items
#: are alternatives, not requirements; read clause by clause they would be
#: ANDed, so the whole list is left for review.
STRUCTURED_COVERAGE = _re(r"\b(?:fully|partially|not)\s+covered(?:\s+unit\s+types)?\s*:")
#: The rule's own applicability turns on an event the data cannot settle.
_CONTINGENT = _re(r"^applies\s+only\s+if\b|\bif\s+and\s+when\b|\bprovided\s+that\b|\bpromulgat")
#: Coverage borrowed from another provision, or a category the data cannot see.
_CROSS_REFERENCE = _re(
    r"\bsubject\s+to\s+(?:the\s+division|civ|§|section|chapter|this)"
    r"|\bunder\s+(?:subdivision\b|§|section\b)|\bsame\s+exemptions\b"
    r"|\b(?:exempt|excluded)\s+(?:under|from)\s+(?:§|section\b|the\b)"
    r"|\bcovered\s+tenancies\b|\beligible\s+landlords\s+under\b"
    r"|\bfully\s+covered\b|\brent\s+ceiling\b|\bfunding\b|\bincome-restricted\b"
)
#: In an exemption list only these mean "another provision decides": a bare
#: citation after a carve-out ("under subdivision (p)(2)") is not one.
_EXEMPTION_CROSS_REFERENCE = _re(
    r"\bsame\s+exemptions\b|\b(?:exempt|excluded)\s+under\s+(?:§|section\b)"
    r"|\bexcluded\s+from\s+the\s+(?:\w+\s+){0,4}ordinance\b|\bexempt\s+units\s+under\b"
)
#: Sentences in an exemption list that qualify another item rather than
#: exempting anything themselves.
_QUALIFIER = _re(
    r"\bthat\s+exemption\s+does\s+not\s+apply\b|\bregulation\s+may\s+not\s+cover\b"
    r"|\bexempt\s+from\s+local\s+rent\s+control\s+for\s+\d+\s+years\b"
    r"|\bpartially\s+covered\s+and\s+exempt\b|\bthe\s+one-month\s+cap\b"
)
_NEGATIVE = _re(
    r"\bis\s+exempt\b|\bare\s+exempt\b|\bdoes\s+not\s+(?:cover|apply\s+to)\b|\bnot\s+covered\b"
    r"|\bexclude[sd]?\b|\bnot\s+rent-regulated\b"
)

# ------------------------------------------------------------- deference ---
_DEFERENCE = _re(
    r"\b(?:subject\s+to|under)\s+(?:the\s+city's\s+|a\s+|any\s+)?(?:stricter\s+)?"
    r"(?:local\s+)?(?:RSO|rent\s+(?:control|stabilization)|rent\s+ordinance)"
)

# ------------------------------------------------------------ membership ---
_LA_NOT_RSO = _re(r"\bnon-?RSO\b|\bnot\s+regulated\s+by\s+the\s+rent\s+stabilization")
_LA_RSO = _re(
    r"\b(?:subject\s+to|covered\s+by)\s+(?:the\s+)?(?:city\s+of\s+)?(?:los\s+angeles\s+)?"
    r"(?:rent\s+stabilization\s+ordinance|RSO)\b(?!\s+or\b)"
    r"|^rental\s+properties\s+first\s+built\s+on\s+or\s+before\s+october\s+1,\s+1978"
)
_RSO_OR_JCO = _re(r"\bRSO\b\s+(?:or|and)\s+(?:the\s+)?JCO\b|\bRSO\)?\s+or\s+just\s+cause")
_SF_RENT_CONTROLLED = _re(r"\brent-controlled\s+units\s+in\s+san\s+francisco")
_SF_MEMBER = _re(r"\b(?:covered\s+by|under)\s+the\s+(?:san\s+francisco\s+)?rent\s+ordinance\b")
_SF_NEW_UNITS = _re(r"certificate\s+of\s+occupancy\s+after\s+june\s+13,\s+1979")
_BERKELEY_ALL = _re(r"\bfully\b.{0,220}?\bpartially\s+covered|\bfully\s+or\s+partially\s+covered")
_BERKELEY_MEMBER = _re(r"\bcovered\s+by\s+the\s+berkeley\s+rent\s+ordinance\b")
_JCO_NOTE = _re(r"\bJCO\s+can\s+apply\b|\bsingle-family\s+dwelling$")

# ------------------------------------------------------------- owners ---
_OWNER = _re(
    r"owner[- ](?:occupied|occupant|occupies|lives|lived|resides)|owner\s+occup"
    r"|where\s+the\s+(?:owner|landlord)\s+(?:occupies|lives|resides|shares)"
    r"|resident\s+landlord|landlord\s+occupies|landlord\s+lived|owner\s+of\s+record\s+resides"
    r"|owner\s+shares|shares\s+a\s+(?:bathroom|kitchen)|golden\s+duplex"
    r"|by\s+the\s+owner\b|religious\s+organization"
)
_SMALL_LANDLORD = _re(
    r"small[- ]landlord|no\s+more\s+than\s+(?:two|2)\s+(?:residential\s+)?rental\s+properties"
    r"|own\s+only\s+two\s+rental\s+properties"
)
_NATURAL_PERSON = _re(r"\bnatural\s+person")
_WORD_COUNT = {"one": 1, "two": 2, "three": 3, "four": 4, "single": 1}
_FAMILY_BOUND = _re(
    r"\b(one|two|three|four|single|[1-4])(?:-|\s+)(?:or\s+(?:two|three|four)-?\s*)?(?:family|unit)\b"
    r"|\b(one|two|three|four)-\s+or\s+(two|three|four)-family"
    r"|\bduplex(?:es)?\b|\bnot\s+more\s+than\s+(four|three|two|\d+)\s+dwelling\s+units"
    r"|\bproperties\s+of\s+1-(\d)\s+units"
)

# ---------------------------------------------------------------- age ---
_ROLLING_AGE = _re(
    r"within\s+the\s+(?:previous|last|past)\s+15\s+years|produced\s+in\s+the\s+last\s+15\s+years"
    r"|older\s+than\s+15\s+years"
)
_ROLLING_OLDER = _re(r"older\s+than\s+15\s+years")
_BUILT_ON_OR_BEFORE = _re(r"\b(?:built|constructed)\s+on\s+or\s+before\s+(\d{4})-(\d{2})-(\d{2})\b")
_BUILT_AFTER_TEXT = _re(r"\bconstructed\s+after\s+february\s+1,\s+1995\b")
_ILLUSTRATION = _re(r"^a\s+unit\s+built\s+.*\bis\s+covered\s+on\s+and\s+after\b")

# ------------------------------------------------------- property types ---
_PROPERTY_TYPES = (
    ("hotel", r"\bhotels?\b|\bmotels?\b|\btransient\b|\btourist\b"),
    ("hospital", r"\bhospitals?\b|\binpatient\b"),
    (
        "care_facility",
        r"\bcare\s+facilit|extended\s+care|long-term\s+care|nursing|substance\s+abuse"
        r"|treatment\s+centers?",
    ),
    ("religious_facility", r"\breligious\s+facilit"),
    ("dormitory", r"\bdormitor(?:y|ies)\b|university\s+rental\s+units|fraternity|sorority"),
    ("school", r"\bk-12\b|\bschools?\b"),
    ("mobilehome", r"\bmobile\s*homes?\b|\bmobilehomes?\b|manufactured\s+home"),
    (
        "single_family",
        r"\bsingle-family\b|\bone-family\b|\bsingle\s+home\b|\bseparately\s+alienable\b",
    ),
    ("condominium", r"\bcondominiums?\b|\btownhomes?\b"),
    ("cooperative", r"\bcooperatives?\b"),
    (
        "shelter",
        r"\bhomeless\b|transitional\s+housing|shared\s+living\s+quarters"
        r"|detention|correctional",
    ),
    ("age_restricted", r"\bage-restricted\b|\bone\s+sex\b|\belderly\b"),
)

# ---------------------------------------------------------------- status ---
_SUBSIDY = _re(
    r"deed-restricted|deed-\s+or\s+regulatory-restricted|subsidi[sz]ed|public\s+housing"
    r"|section\s+8\s+properties|\bHUD\b|section\s+(?:202|811)\b"
)
_TENANT_SUBSIDY = _re(
    r"rent\s+subsidy\s+is\s+in\s+place|tenants\s+receiving|recipients\s+of|source\s+of\s+income"
)
_SEASONAL = _re(
    r"\bseasonal\b|\bvacation\b|\brecreational\b|short-term\s+residential|\d+\s+days\s+or\s+less"
)

# ------------------------------------------------------ no building condition ---
_TENANCY = _re(
    r"\btenanc(?:y|ies)\s+(?:began|beginning|starting|started|commenced|that\s+began)"
    r"|\bleases?\s+entered\s+into|\bthe\s+tenant\s+has\s+(?:continuously|lived)"
    r"|\bonce\s+the\s+tenant\b|\btenancies\s+(?:beginning|starting)\b"
    r"|\bsame\s+tenant\s+remains\b|\bapplies\s+on\s+or\s+after\b|\bphotographs?\b"
    r"|\bcollected\s+or\s+demanded\s+before\b|\bdeductions\s+apply\s+to\s+tenancies"
    r"|\bheld\s+for\s+one\s+year\b|\bwhere\s+a\s+security\s+deposit\s+was\s+taken"
    r"|\b(?:became|becomes)\s+operative\b|\bapplies\s+for\s+the\s+period\b"
    r"|\bnot\s+adjusted\s+by\b|\bmay\s+not\s+adjust\s+rents\s+of\s+tenants"
    r"|\bsublet|\broommates?\b|\bmove\s+back\b|\btenancy\s+lasts\b"
)
_TRANSACTION = _re(
    r"\brelocation\s+assistance\b|\bno\s+refund\b|\brefund\b|\bdocumentation\s+requirements"
    r"|\bgood-faith\s+estimates\b|\badvance\s+payment\b|\balterations\b|\bservice\s+member\b"
    r"|\bmarket\s+rate\b|\bluxury\s+exemption\b|\bpetition\b|\bpresumption\b"
    r"|\bconviction|\bsex\s+offender|\bmeth-production\b|\bnot\s+discrimination\b"
    r"|\bmay\s+exclude\s+applicants\b|\bnotices?\s+must\s+be\s+filed\b|\bfiled\s+with\s+lahd"
    r"|\bintended\s+occupant\b|\bresident\s+manager\b|\btemporary\s+removal\s+of\s+services"
    r"|\bwritten\s+tenant\s+agreement\b|\bmom\s+&\s+pop\b|\beligible\s+relative\b"
    r"|\bno-fault\s+just\s+cause\b|\bcurable\s+lease\s+violation\b|\bevictions?\s+from\b"
    r"|\bvacate\s+order|\bcourt\s+(?:determines|finds)\b|\bnatural\s+disaster\b"
    r"|\bunpaid\s+rent\b|\bnonpayment\s+of\s+rent\b|\bgovernment\s+(?:agency|order)\b"
    r"|\breprisals?\b|\bscreening\s+criteria\b|\breal\s+estate\s+commission\s+licensee\b"
    r"|\bgovernment\s+entities\s+setting\b|\bcompliance\s+becomes\s+voluntary\b"
    r"|\bmay\s+adopt\s+rent\s+control\b|\bregulation\s+may\s+not\s+cover\b"
    r"|\bsoftware\b|\bspreadsheets?\b|\bdatabases?\b|\breports?\b|\bmarket\s+research\b"
    r"|\bresearch\b|\btesting\b|\bproducts?\b|\bend\s+consumers?\b|\bpublic\s+rent\s+estimates\b"
    r"|\bbrokerage\b|\bcoordinating\s+function\b|\bcompetitor\s+data\b|\bincome\s+limits\b"
    r"|\bdoes\s+not\s+limit\s+an\s+owner's\s+ability\b|\blandlord\s+references\b"
)
_DEFINITION = _re(
    r"\bmeans\b|\bis\s+any\s+methodology\b|\binclude[sd]?\s+level\s+of\s+service\b"
    r"|^output$|\bcosmetic\s+work\b|\bthe\s+page\s+(?:does\s+not|only|says)\b"
    r"|\bthe\s+document\s+does\s+not\b|\bthe\s+source\s+gives\b|\brefers\s+users\b"
    r"|\bnot\s+specific\s+to\s+residential\b|\bcovered\s+types\s+include\b"
    r"|\bhousing\s+services\s+including\b|\bincludes?\s+government-owned\b"
    r"|\bexception\s+applies\s+only\s+to\s+a\s+city\b|\beven\s+then,\s+regulation\b"
    r"|\blocal\s+rent\s+control\s+or\s+rent\s+leveling\s+ordinances\s+apply\b"
    r"|\bexempt\s+from\s+local\s+rent\s+control\s+for\s+30\s+years\b"
    r"|\bthat\s+exemption\s+does\s+not\s+apply\b|\bpartially\s+covered\s+and\s+exempt\b"
    r"|\bthe\s+one-month\s+cap\b|\bmobile\s+home\s+spaces\b|\bfor\s+mobilehome\s+tenancies\b"
    r"|\bdeposit-holding\s+rules\s+differ\b|\bapplies\s+to\s+all\s+cities\s+and\s+towns\b"
    r"|^any\s+city$|\bpersons\s+residing\s+or\s+proposing\b"
)
_INCLUSION = _re(r"\binclud(?:e|es|ing)\b|\bsuch\s+as\b")
_UNSEEN_HISTORY = _re(r"\bconverted\s+to\b|\bconversion\b")
_BUILDING_CONDITION = _re(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:or\s+(?:more|fewer|less|greater)\s+)?(?:dwelling\s+|rental\s+|residential\s+)?units?\b"
    r"|\b(?:built|constructed|year\s+built)\b[^.;]{0,40}\b\d{4}\b|\bcertificate\s+of\s+occupancy\b"
)
_SCOPE = _re(
    r"\bresidential\b|\brental\b|\bdwelling\b|\bhousing\b|\btenan(?:t|cy|cies)\b|\blandlords?\b"
    r"|\blessors?\b|\bowners?\b|\bapplicants?\b|\bhousing\s+providers?\b|\bperson\b"
    r"|\bsecurity\s+(?:for|deposit)\b|\breal\s+estate\s+transactions\b|\bproperty\b"
    r"|\bunits?\b|\bapartments?\b|\bleases?\b"
)


def _types(text: str) -> tuple[tuple[Field, Op, Any], ...]:
    return tuple(
        (Field.property_type, Op.eq, kind)
        for kind, pattern in _PROPERTY_TYPES
        if re.search(pattern, text, re.I)
    )


def _family_bound(text: str) -> int | None:
    bounds: list[int] = []
    for m in _FAMILY_BOUND.finditer(text):
        word = next((g for g in m.groups() if g), None)
        if m.group(0).lower().startswith("duplex"):
            bounds.append(2)
        elif word and word.isdigit():
            bounds.append(int(word))
        elif word:
            # "two- or three-family" names the larger bound last.
            tail = m.group(3) or m.group(0)
            numbers = [_WORD_COUNT[w] for w in re.findall(r"one|two|three|four|single", tail)]
            bounds.append(max(numbers or [_WORD_COUNT[word.lower()]]))
    if re.search(r"golden\s+duplex|two-unit\s+property|\bADUs?\b", text, re.I):
        bounds.append(2)
    for m in re.finditer(
        r"\b(\d+|one|two|three|four)\s+or\s+fewer\s+(?:dwelling\s+|rental\s+)?units\b", text, re.I
    ):
        word = m.group(1).lower()
        bounds.append(int(word) if word.isdigit() else _WORD_COUNT[word])
    return max(bounds) if bounds else None


def _city(record: Any) -> str:
    jurisdiction = getattr(record, "jurisdiction", "") or ""
    return jurisdiction.rpartition(",")[0].strip().casefold() if "," in jurisdiction else ""


def classify(clause: str, origin: Origin, record: Any) -> Reading | None:
    """One reading of one clause, or None when no shape fits."""
    text = " ".join(clause.split())
    is_exemption = origin is Origin.exemption
    level = (getattr(record, "level", "") or "").lower()
    city = _city(record)

    if is_exemption and _EXEMPTION_CROSS_REFERENCE.search(text):
        return Reading("unresolved", "", unresolved="takes its exemptions from another provision")
    if _re(r"\bsame\s+exemptions\b").search(text):
        return Reading("unresolved", "", unresolved="takes its exemptions from another provision")

    # Local deference in a state rule is precedence, not an exemption.
    if is_exemption and level == "state" and _DEFERENCE.search(text):
        return Reading(DEFERENCE, "the state rule stands aside where local rent control applies")
    if is_exemption and _QUALIFIER.search(text):
        return Reading(QUALIFIER, "qualifies another item in the list; exempts nothing itself")

    # Ordinance membership, read later from year built against the cutoff. Only
    # ever for a rule of that city: "the Rent Ordinance" means a different law
    # in each of them.
    if city == "los angeles":
        if _LA_NOT_RSO.search(text):
            return Reading(
                CONDITION,
                "covers only units outside the Los Angeles RSO",
                ((Field.los_angeles_rso_membership, Op.is_false, None),),
            )
        if _RSO_OR_JCO.search(text):
            return Reading(SCOPE, "the RSO and JCO together reach every rental unit in the city")
        if _JCO_NOTE.search(text):
            return Reading(DEFINITION, "a note on where the ordinance can apply")
        if _LA_RSO.search(text):
            return Reading(
                CONDITION,
                "turns on Los Angeles RSO membership",
                ((Field.los_angeles_rso_membership, Op.is_true, None),),
            )
    if city == "san francisco":
        if _SF_RENT_CONTROLLED.search(text):
            return Reading(
                CONDITION,
                "rent control reaches units certificated on or before 1979-06-13",
                ((Field.certificate_of_occupancy_date, Op.lte, dt.date(1979, 6, 13)),),
            )
        if _SF_NEW_UNITS.search(text):
            return Reading(
                CONDITION,
                "reaches units first certificated after 1979-06-13",
                ((Field.certificate_of_occupancy_date, Op.gt, dt.date(1979, 6, 13)),),
            )
        if _SF_MEMBER.search(text) and not _CROSS_REFERENCE.search(text):
            return Reading(
                CONDITION,
                "turns on San Francisco Rent Ordinance membership",
                ((Field.san_francisco_rent_ordinance_membership, Op.is_true, None),),
            )
    if (
        city == "berkeley"
        and not is_exemption
        and (
            _BERKELEY_ALL.search(text)
            or (_BERKELEY_MEMBER.search(text) and not _CROSS_REFERENCE.search(text))
        )
    ):
        return Reading(SCOPE, "units covered by the Berkeley Rent Ordinance, fully or in part")

    # Small landlords: owners holding at most four units in all.
    if _SMALL_LANDLORD.search(text):
        atoms: list[tuple[Field, Op, Any]] = [(Field.owner_unit_count, Op.lte, 4)]
        if _NATURAL_PERSON.search(text) or is_exemption:
            atoms.insert(0, (Field.owner_is_natural_person, Op.is_true, None))
        return Reading(CONDITION, "a small-landlord test: at most four units in all", tuple(atoms))
    if not is_exemption and _NATURAL_PERSON.search(text):
        return Reading(
            CONDITION,
            "turns on the owner being a natural person",
            ((Field.owner_is_natural_person, Op.is_true, None),),
        )

    # Owner occupancy, bounded by the size of the building where the text says so.
    if is_exemption and _re(r"religious\s+organization").search(text):
        return Reading(
            CONDITION,
            "an exemption for housing a religious organization offers",
            ((Field.owner_is_natural_person, Op.is_false, None),),
        )
    if is_exemption and _OWNER.search(text):
        bound = _family_bound(text)
        if bound is None and _re(r"single-family|one-family|single\s+home|\bhome\b").search(text):
            bound = 1
        atoms = [(Field.owner_occupied, Op.is_true, None)]
        if bound is not None:
            atoms.append((Field.units, Op.lte, bound))
        return Reading(
            CONDITION,
            "an owner-occupancy exemption"
            + (f" for buildings of at most {bound} units" if bound is not None else ""),
            tuple(atoms),
        )

    # Rolling fifteen-year new-construction test.
    if _ROLLING_AGE.search(text):
        older = bool(_ROLLING_OLDER.search(text))
        exempt = is_exemption or bool(_NEGATIVE.search(text))
        return Reading(
            CONDITION,
            "the rolling fifteen-year new-construction test",
            ((Field.years_since_certificate_of_occupancy, Op.gte if older else Op.lt, 15),),
            as_exemption=exempt and not is_exemption,
        )
    if _ILLUSTRATION.search(text):
        return Reading(DEFINITION, "an illustration of the rolling test, not a condition")
    if m := _BUILT_ON_OR_BEFORE.search(text):
        cutoff = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return Reading(
            CONDITION,
            f"covers buildings constructed on or before {cutoff.isoformat()}",
            ((Field.certificate_of_occupancy_date, Op.lte, cutoff),),
        )
    if is_exemption and _BUILT_AFTER_TEXT.search(text):
        return Reading(QUALIFIER, "restates the construction cutoff already read from coverage")

    # Buildings by size or type: alternatives an apartment building is not.
    if is_exemption or _NEGATIVE.search(text):
        bound = _family_bound(text) if is_exemption else None
        alternatives: list[tuple[Field, Op, Any]] = []
        if bound is not None:
            alternatives.append((Field.units, Op.lte, bound))
        alternatives.extend(_types(text))
        if alternatives:
            if is_exemption and _SUBSIDY.search(text):
                alternatives.append((Field.building_is_subsidised, Op.is_true, None))
            if is_exemption and _SEASONAL.search(text):
                alternatives.append((Field.seasonal_rental, Op.is_true, None))
            return Reading(
                CONDITION,
                "an exemption for buildings of a particular size or type",
                tuple(alternatives),
                alternatives=True,
                as_exemption=not is_exemption,
            )
    elif _UNSEEN_HISTORY.search(text):
        return Reading(
            "unresolved",
            "",
            unresolved="turns on the building's ownership history, which the data does not record",
        )
    elif (
        _types(text)
        and not _CROSS_REFERENCE.search(text)
        and (_INCLUSION.search(text) or (text[:1].islower() and len(text.split()) <= 6))
    ):
        # Property types given as examples of what is covered ("including
        # mobile homes"), or the tail of such a list: they never shut out an
        # apartment building. Types as the clause's subject are not this.
        return Reading(SCOPE, "names property types the rule includes")

    # Subsidised buildings, and the tenant-level subsidies that are not.
    if not is_exemption and _TENANT_SUBSIDY.search(text):
        return Reading(SCOPE, "names tenants or applicants with subsidies, not buildings")
    if is_exemption and _SUBSIDY.search(text):
        return Reading(
            CONDITION,
            "an exemption for subsidised or deed-restricted housing",
            ((Field.building_is_subsidised, Op.is_true, None),),
        )
    if is_exemption and _SEASONAL.search(text):
        return Reading(
            CONDITION,
            "an exemption for seasonal or vacation rentals",
            ((Field.seasonal_rental, Op.is_true, None),),
        )

    # From here on a clause can only be set aside, so first the clauses that
    # must never be: a contingency, or coverage borrowed from another provision.
    if _CONTINGENT.search(text):
        return Reading(
            "unresolved", "", unresolved="applies only on a contingency the data cannot settle"
        )
    if not is_exemption and _CROSS_REFERENCE.search(text):
        return Reading(
            "unresolved", "", unresolved="takes its coverage from another provision; needs review"
        )

    if _DEFINITION.search(text):
        return Reading(DEFINITION, "a definition or note, not a condition on the building")
    # A building condition the strict reading could not verify in the source -
    # a unit count, a construction year, a certificate date - is exactly what
    # must not be set aside: Module A may have the number wrong.
    if _BUILDING_CONDITION.search(text):
        return Reading(
            "unresolved",
            "",
            unresolved="states a building condition the source does not state word for word",
        )
    if _TENANCY.search(text):
        return Reading(TENANCY, "a condition on the tenancy, not on which buildings are covered")
    if _TRANSACTION.search(text):
        return Reading(
            TRANSACTION,
            "concerns a transaction, an actor or a product, not which buildings are covered",
        )
    if (
        not is_exemption
        and _SCOPE.search(text)
        and (not _NEGATIVE.search(text) or _re(r"\bother\s+than\b").search(text))
    ):
        return Reading(SCOPE, "names who or what is regulated, not which buildings")
    return None
