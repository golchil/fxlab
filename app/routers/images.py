"""Image API router for serving and managing images."""
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import WindowImage, Window, Label
from app.schemas import ImageDetailResponse
from app.services.minio_client import get_image

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/{image_id}/raw")
def get_image_raw(image_id: int, db: Session = Depends(get_db)):
    """Get raw image data (PNG)."""
    window_image = db.query(WindowImage).filter(WindowImage.id == image_id).first()
    if not window_image:
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        image_data = get_image(window_image.image_key)
        return Response(content=image_data, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch image: {str(e)}")


@router.get("/{image_id}", response_model=ImageDetailResponse)
def get_image_detail(image_id: int, db: Session = Depends(get_db)):
    """Get image details with metadata."""
    window_image = db.query(WindowImage).filter(WindowImage.id == image_id).first()
    if not window_image:
        raise HTTPException(status_code=404, detail="Image not found")

    window = db.query(Window).filter(Window.id == window_image.window_id).first()
    if not window:
        raise HTTPException(status_code=404, detail="Window not found")

    # Get label if exists
    label = db.query(Label).filter(
        Label.dataset_id == window_image.dataset_id,
        Label.bar_ts == window.end_ts
    ).first()

    return ImageDetailResponse(
        id=window_image.id,
        window_id=window_image.window_id,
        dataset_id=window_image.dataset_id,
        image_key=window_image.image_key,
        width=window_image.width,
        height=window_image.height,
        ma_periods=window_image.ma_periods,
        created_at=window_image.created_at,
        window_start_ts=window.start_ts,
        window_end_ts=window.end_ts,
        lookback_n=window.lookback_n,
        label_result=label.result if label else None,
    )
