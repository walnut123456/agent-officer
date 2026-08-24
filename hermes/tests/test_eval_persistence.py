from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from evals.agent_release_eval import persist_report
from hermes_officer.infrastructure.database import AgentEvalCaseRecord, AgentEvalRunRecord, Database


class EvalPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_report_and_cases_are_persisted_as_one_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermes-eval-persistence-") as directory:
            database_url = f"sqlite+aiosqlite:///{(Path(directory) / 'eval.db').as_posix()}"
            report = {
                "run_id": "eval-run-001",
                "generated_at": "2026-08-22T00:00:00+00:00",
                "gate": "CONDITIONAL",
                "overall_score": 88.5,
                "passed_count": 1,
                "case_count": 2,
                "category_scores": {"reliability": 50.0},
                "category_counts": {"reliability": 2},
                "latency_summaries": {"reliability": {"samples": 2, "p50_ms": 5.0, "p95_ms": 8.0}},
                "quality_gaps": ["reliability<95"],
                "hard_blockers": ["reliability<95"],
                "metadata": {"mode": "offline", "model": None},
                "cases": [
                    {
                        "case_id": "case-pass",
                        "category": "reliability",
                        "passed": True,
                        "score": 1.0,
                        "latency_ms": 5.0,
                        "details": {},
                    },
                    {
                        "case_id": "case-fail",
                        "category": "reliability",
                        "passed": False,
                        "score": 0.0,
                        "latency_ms": 8.0,
                        "details": {"reason": "probe"},
                    },
                ],
            }

            run_id = await persist_report(database_url, report)

            database = Database(database_url)
            try:
                async with database.session() as session:
                    run = await session.scalar(
                        select(AgentEvalRunRecord).where(AgentEvalRunRecord.run_id == run_id)
                    )
                    cases = list(await session.scalars(
                        select(AgentEvalCaseRecord).where(AgentEvalCaseRecord.run_id == run_id)
                    ))
                self.assertIsNotNone(run)
                self.assertEqual("CONDITIONAL", run.gate)
                self.assertEqual(2, run.case_count)
                self.assertEqual(2, len(cases))
                self.assertEqual({"case-pass", "case-fail"}, {item.case_id for item in cases})
            finally:
                await database.dispose()


if __name__ == "__main__":
    unittest.main()
