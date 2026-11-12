"""Small SQLite store for user-requested AI categorization results."""

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DATABASE_PATH = Path(__file__).resolve().parents[2] / "paypilot.db"


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS ai_categorizations (
            transaction_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            explanation TEXT NOT NULL,
            direction TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS fraud_analyses (
            transaction_id TEXT NOT NULL,
            analysis_version TEXT NOT NULL,
            transaction_date TEXT,
            amount TEXT,
            currency TEXT,
            risk_score INTEGER NOT NULL,
            risk_level TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            explanation TEXT NOT NULL,
            recommended_action TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'unreviewed',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (transaction_id, analysis_version)
        )"""
    )
    return connection


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def save_categorization(transaction_id: str, result: Any) -> None:
    with _connection() as connection:
        connection.execute(
            """INSERT INTO ai_categorizations
               (transaction_id, category, confidence, explanation, direction)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(transaction_id) DO UPDATE SET
                 category=excluded.category,
                 confidence=excluded.confidence,
                 explanation=excluded.explanation,
                 direction=excluded.direction,
                 updated_at=CURRENT_TIMESTAMP""",
            (transaction_id, result.category, result.confidence, result.explanation, result.direction),
        )


def get_categorizations(transaction_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not transaction_ids:
        return {}
    results: dict[str, dict[str, Any]] = {}
    ids = list(transaction_ids)
    with _connection() as connection:
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"SELECT transaction_id, category, direction FROM ai_categorizations WHERE transaction_id IN ({placeholders})",
                tuple(batch),
            ).fetchall()
            results.update({row["transaction_id"]: {"category": row["category"], "direction": row["direction"]} for row in rows})
    return results


def save_fraud_analyses(analyses: list[dict[str, Any]]) -> None:
    if not analyses:
        return
    with _connection() as connection:
        connection.executemany(
            """INSERT INTO fraud_analyses
               (transaction_id, analysis_version, transaction_date, amount, currency,
                risk_score, risk_level, reasons_json, explanation, recommended_action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(transaction_id, analysis_version) DO UPDATE SET
                 transaction_date=excluded.transaction_date,
                 amount=excluded.amount,
                 currency=excluded.currency,
                 risk_score=excluded.risk_score,
                 risk_level=excluded.risk_level,
                 reasons_json=excluded.reasons_json,
                 explanation=excluded.explanation,
                 recommended_action=excluded.recommended_action,
                 updated_at=CURRENT_TIMESTAMP""",
            [(
                item["transaction_id"],
                item["analysis_version"],
                item.get("transaction_date"),
                item.get("amount"),
                item.get("currency"),
                item["risk_score"],
                item["risk_level"],
                json.dumps(item.get("reasons") or []),
                item["explanation"],
                item["recommended_action"],
            ) for item in analyses if item.get("transaction_id")],
        )


def get_fraud_analyses(
    *, limit: int | None = 100, risk_level: str | None = None, review_status: str | None = None,
    analysis_version: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if risk_level:
        clauses.append("risk_level = ?")
        parameters.append(risk_level)
    if review_status:
        clauses.append("review_status = ?")
        parameters.append(review_status)
    if analysis_version:
        clauses.append("analysis_version = ?")
        parameters.append(analysis_version)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit_clause = ""
    if limit is not None:
        limit_clause = " LIMIT ?"
        parameters.append(limit)
    with _connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM fraud_analyses {where} ORDER BY updated_at DESC, transaction_date DESC{limit_clause}",
            tuple(parameters),
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["reasons"] = json.loads(item.pop("reasons_json"))
        results.append(item)
    return results


def update_fraud_review(
    transaction_id: str, review_status: str, analysis_version: str
) -> dict[str, Any] | None:
    with _connection() as connection:
        connection.execute(
            """UPDATE fraud_analyses SET review_status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE transaction_id = ? AND analysis_version = ?""",
            (review_status, transaction_id, analysis_version),
        )
        row = connection.execute(
            """SELECT * FROM fraud_analyses
               WHERE transaction_id = ? AND analysis_version = ?""",
            (transaction_id, analysis_version),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["reasons"] = json.loads(item.pop("reasons_json"))
    return item
