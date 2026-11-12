from fastapi import APIRouter, HTTPException

from app.schemas.ai import CategorizeTransactionRequest, CategorizeTransactionResponse
from app.services.groq_categorization import (
    CategorizationError,
    groq_categorization_service,
)
from app.services.categorization_store import save_categorization

router = APIRouter(prefix="/api/ai", tags=["AI"])


@router.post("/categorize", response_model=CategorizeTransactionResponse)
async def categorize_transaction(
    transaction: CategorizeTransactionRequest,
) -> CategorizeTransactionResponse:
    """Categorize the supplied transaction fields without adding transaction facts."""
    try:
        result = await groq_categorization_service.categorize(transaction)
        if transaction.transaction_id:
            save_categorization(transaction.transaction_id, result)
        return result
    except CategorizationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
