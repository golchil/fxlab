from fastapi import FastAPI

from app.routers import datasets, jobs

app = FastAPI(
    title="FXLab",
    description="ForexTester CSV import, window generation, and TP/SL labeling API",
    version="0.1.0",
)

app.include_router(datasets.router)
app.include_router(jobs.router)


@app.get("/")
def root():
    return {"message": "FXLab API is running", "version": "0.1.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}
