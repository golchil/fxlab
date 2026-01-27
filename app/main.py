import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import datasets, jobs, images, ui

app = FastAPI(
    title="FXLab",
    description="ForexTester CSV import, window generation, and TP/SL labeling API",
    version="0.1.0",
)

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include routers
app.include_router(datasets.router)
app.include_router(jobs.router)
app.include_router(images.router)
app.include_router(ui.router)


@app.get("/")
def root():
    return {"message": "FXLab API is running", "version": "0.1.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}
