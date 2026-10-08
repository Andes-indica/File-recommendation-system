"""Reproduce authored synthetic judgments; this is not independent user research."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests/fixtures/evaluation"
TOPICS = {
    "backup-recovery.md": [
        "database backup verification",
        "point-in-time recovery restore testing",
        "disaster recovery contacts",
        "service recovery objectives",
    ],
    "camera-photography.md": [
        "camera exposure aperture",
        "shutter speed ISO",
        "outdoor portraits composition",
        "photography image settings",
    ],
    "cloud-migration-plan.md": [
        "staged service cutover rollback",
        "network dependencies database replication",
        "cloud infrastructure migration",
        "deployment milestones rollback windows",
    ],
    "customer-feedback.md": [
        "mobile application feature requests",
        "customer satisfaction themes",
        "product release concerns",
        "mobile users support follow-up",
    ],
    "employee-onboarding.md": [
        "new employee laptop equipment",
        "account access orientation",
        "first-week training tasks",
        "employee setup checklist",
    ],
    "finance-budget.md": [
        "quarterly budget planning",
        "department spending limits",
        "forecast assumptions expenses",
        "finance budget forecast",
    ],
    "garden-care.md": [
        "planting flowers watering seedlings",
        "pruning shrubs soil nutrients",
        "seasonal garden calendar",
        "garden care tracking nutrients",
    ],
    "hiring-interview.md": [
        "backend engineering candidate interview",
        "interviewer observations hiring decision",
        "system design debrief",
        "candidate interview feedback",
    ],
    "product-roadmap.md": [
        "release milestones launch timeline",
        "software roadmap phases",
        "next software release",
        "product launch roadmap",
    ],
    "security-incident-review.md": [
        "exposed access credentials token rotation",
        "audit evidence containment actions",
        "security response owners",
        "incident review credentials",
    ],
    "sprint-retrospective.md": [
        "sprint delivery blockers",
        "planning process improvements",
        "retrospective follow-up owners",
        "sprint retrospective action items",
    ],
    "team-meeting.md": [
        "platform discussion decisions",
        "meeting minutes follow-up",
        "platform team action items",
        "team meeting discussion",
    ],
    "travel-expenses.md": [
        "business travel receipts reimbursement",
        "hotel flight expenses",
        "submission deadlines employee trips",
        "travel reimbursement rules",
    ],
    "vehicle-maintenance.md": [
        "automobile engine oil",
        "tire pressure service intervals",
        "vehicle maintenance guide",
        "automobile maintenance intervals",
    ],
    "support-handoff.txt": [
        "on-call rotation escalation",
        "overnight response coverage",
        "platform support handoff",
        "incident escalation overnight",
    ],
}
UNRELATED = [
    "nebula xylophone blueprint",
    "marine octopus taxonomy",
    "medieval pottery archaeology",
    "lunar rover telemetry",
    "quantum superconductivity experiment",
    "volcanic isotope dating",
    "ballet choreography handbook",
    "sourdough fermentation recipes",
    "submarine sonar acoustics",
    "renaissance sculpture catalogue",
    "rainforest amphibian genetics",
    "telescope exoplanet spectra",
    "cricket batting statistics",
    "ceramic kiln glazing",
    "violin bow repair",
    "aircraft turbine schematics",
    "neolithic cave pigments",
    "saffron harvest techniques",
    "origami folding tutorial",
    "antimatter reactor calibration",
]


def main():
    train, holdout = [], []
    for filename, queries in TOPICS.items():
        for query in queries:
            train.append({"query": query, "relevant_files": [filename]})
        train.append({"query": "find the file " + filename, "relevant_files": [filename]})
        train.append(
            {
                "query": queries[0] + " type:" + Path(filename).suffix[1:],
                "relevant_files": [filename],
            }
        )
        holdout.append(
            {
                "query": "Could you locate the document discussing " + queries[2] + "?",
                "relevant_files": [filename],
            }
        )
    train.extend({"query": q, "relevant_files": [], "expected_no_match": True} for q in UNRELATED)
    holdout.extend(
        {"query": "Find material about " + q, "relevant_files": [], "expected_no_match": True}
        for q in UNRELATED[:15]
    )
    for filename, queries in (
        ("workspace-queries.json", train),
        ("workspace-holdout.json", holdout),
    ):
        payload = {
            "name": filename.removesuffix(".json"),
            "provenance": {
                "kind": "authored_synthetic",
                "independently_reviewed": False,
                "source": "Existing 15 synthetic documents; templates and labels are committed in scripts/build_evaluation_corpus.py.",
                "purpose": "Regression evidence only; the holdout uses separate phrasing, not independently collected user data.",
            },
            "queries": queries,
        }
        (ROOT / filename).write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    main()
